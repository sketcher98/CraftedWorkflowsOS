"""
Route Editor UI — Phase 1B v2
Search flow: fuzzy query -> pick model from live matches -> pick providers.
Pure stdlib. Key values never enter Python: .env is sourced inside a bash
subshell for curl; only env var NAMES appear in code/display.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from routeops.adapters.config_editor import (
    ConfigEditor, ConfigEditorError)
from routeops.tui.bars import CLIENT_ABORT_ERRORS


def _norm(s: str) -> str:
    """Normalize for fuzzy match: lowercase, alphanumeric only."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


class RouteEditor:
    """Modal editor: search models across provider catalogs, pick providers."""

    def __init__(self, app_state):
        self.app_state = app_state
        self.ed = ConfigEditor()
        self.mode = "closed"
        # closed|edit|add|results|pick_prov|diff|confirm_delete|result
        self.msg = ""
        self.pending: Optional[dict] = None
        self.pending_reason = ""
        self.pref_idx = 0
        self.route_idx = 0
        self.input_buf = ""
        self.probe_results: List[str] = []
        # search flow state
        self.matches: List[Tuple[str, List[str]]] = []  # (model_id, [providers])
        self.result_idx = 0
        self.chosen_model = ""
        self.chosen_provs: List[str] = []
        self.pick_on: Dict[str, bool] = {}
        self.pick_idx = 0
        self.need_restart = False
        self._moved_to: Optional[int] = None  # pref idx after a move_pref commit

    # ------------------------------------------------------------- helpers

    def _prefs(self, cfg=None):
        c = cfg if cfg is not None else self.ed.load()
        return c.get("preferences", [])

    def _current_pref(self, cfg=None):
        prefs = self._prefs(cfg)
        if 0 <= self.pref_idx < len(prefs):
            return prefs[self.pref_idx]
        return None

    # ---------------------------------------------------- key management

    def _prov_entry(self, cfg, name):
        for p in cfg.get("providers", []):
            if p["name"] == name:
                return p
        return None

    def _keys_of(self, cfg, name):
        prov = self._prov_entry(cfg, name)
        if not prov:
            return [], ""
        if prov.get("key_envs"):
            return list(prov["key_envs"]), prov["base_url"]
        if prov.get("key_env"):
            return [prov["key_env"]], prov["base_url"]
        return [], prov["base_url"]

    def open_key_mgmt(self, provider_name: str):
        self.km_prov = provider_name
        self.km_idx = 0
        self.km_results = {}   # key_env -> {"ok": bool, "detail": str}
        self.km_stats = self._load_key_stats(provider_name)
        self.mode = "key_mgmt"
        self.msg = ("t=test key  T=test ALL  a=add  d=delete  "
                    "rotate=a then verify then d  Esc=models")

    def _load_key_stats(self, provider_name: str):
        """Per-key usage stats from router-events.jsonl: successes, failures,
        last-used timestamp. Returns {key_env: {...}} — cheap tail scan."""
        import json as _json
        from routeops.config import ROUTER_EVENTS_JSONL
        stats = {}
        try:
            with open(ROUTER_EVENTS_JSONL, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 400_000))  # last ~400KB
                tail = f.read().decode("utf-8", "replace")
            for line in tail.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = _json.loads(line)
                except Exception:
                    continue
                if e.get("event") != "route_result":
                    continue
                if e.get("provider") != provider_name:
                    continue
                ke = e.get("key_env")
                if not ke:
                    continue
                s = stats.setdefault(ke, {"ok": 0, "fail": 0, "abort": 0, "last": None})
                # Client aborts are neutral — same rule as every other screen.
                err = (e.get("error") or "").strip().lower()
                if any(m in err for m in CLIENT_ABORT_ERRORS):
                    s["abort"] += 1
                elif e.get("outcome") == "success":
                    s["ok"] += 1
                else:
                    s["fail"] += 1
                ts = e.get("timestamp", "")
                if ts and (s["last"] is None or ts > s["last"]):
                    s["last"] = ts
        except Exception:
            pass
        return stats

    def km_test(self, all_keys: bool = False):
        cfg = self.ed.load()
        keys, base_url = self._keys_of(cfg, self.km_prov)
        if not keys:
            return
        targets = keys if all_keys else [keys[self.km_idx]]
        for k in targets:
            r = self.ed.probe_catalog(base_url, k, timeout=15)
            self.km_results[k] = {
                "ok": bool(r.get("ok")),
                "detail": (f"{len(r['models'])} models" if r.get("ok")
                           else r.get("error", "")[:40])}

    def km_stage_add(self, name: str):
        name = name.strip()
        if not name or " " in name:
            self.mode = "result"
            self.probe_results = ["key name must be a single env-var word "
                                  "(e.g. NVIDIA_API_KEY_4)"]
            return
        cfg = self.ed.load()
        prov = self._prov_entry(cfg, self.km_prov)
        if not prov:
            return
        key_envs = prov.setdefault("key_envs", [])
        if not key_envs and prov.get("key_env"):
            key_envs.append(prov["key_env"])
        if name in key_envs:
            self.mode = "result"
            self.probe_results = [f"key '{name}' already on {self.km_prov}"]
            return
        key_envs.append(name)
        self.km_note = (f"AFTER SAVE: paste value into ~/.hermes/.env as "
                        f"{name}=sk-... then restart router")
        self._stage(cfg, f"add key '{name}' to {self.km_prov}")

    def km_stage_delete(self):
        cfg = self.ed.load()
        keys, _ = self._keys_of(cfg, self.km_prov)
        if len(keys) <= 1:
            self.mode = "result"
            self.probe_results = ["cannot delete the only key — add a new "
                                  "one first (a)"]
            return
        if not (0 <= self.km_idx < len(keys)):
            return
        name = keys[self.km_idx]
        prov = self._prov_entry(cfg, self.km_prov)
        prov["key_envs"] = [k for k in keys if k != name]
        if prov.get("key_env") == name:
            prov["key_env"] = prov["key_envs"][0]
        self._stage(cfg, f"delete key '{name}' from {self.km_prov}")

    # ---------------------------------------------------- model browser

    def open_model_browser(self, provider_name: str):
        self.mb_prov = provider_name
        self.mb_result = None
        cfg = self.ed.load()
        keys, base_url = self._keys_of(cfg, provider_name)
        r = self.ed.probe_catalog(base_url, keys[0] if keys else "",
                                  timeout=20)
        if r.get("ok"):
            self.mb_models = r["models"]
            self.mb_idx = 0
            self.mode = "model_browser"
            self.msg = ("j/k=select  t=test model  a=route it  c=to bench  "
                        "Esc=done")
        else:
            self.mode = "result"
            self.probe_results = [f"{provider_name}: catalog probe failed — "
                                  f"{r.get('error','')[:60]}"]
    def mb_test_model(self):
        if not (0 <= self.mb_idx < len(self.mb_models)):
            return
        model = self.mb_models[self.mb_idx]
        cfg = self.ed.load()
        keys, base_url = self._keys_of(cfg, self.mb_prov)
        r = self.ed.test_key(base_url, keys[0] if keys else "", model,
                             timeout=45)
        self.mb_result = (f"{model}: OK 200" if r.get("ok")
                          else f"{model}: FAIL {r.get('detail','')[:50]}")

    def mb_stage_pref(self):
        if not (0 <= self.mb_idx < len(self.mb_models)):
            return
        model = self.mb_models[self.mb_idx]
        cfg = self.ed.load()
        if any(p["name"] == model for p in cfg["preferences"]):
            self.mb_result = f"preference '{model}' already exists"
            return
        cfg["preferences"].append({
            "name": model, "context_window": 1048576,
            "routes": [{"provider": self.mb_prov, "model": model}],
        })
        self._stage(cfg, f"route '{model}' on {self.mb_prov}")

    def mb_to_bench(self):
        if not (0 <= self.mb_idx < len(self.mb_models)):
            return
        if getattr(self, "bench", None) is None:
            self.bench = []
        if len(self.bench) >= 4:
            self.mb_result = "bench full (4 slots) — run it (c from providers)"
            return
        self.bench.append((self.mb_prov, self.mb_models[self.mb_idx]))
        self.mb_result = (f"bench slot {len(self.bench)}/4: "
                           f"{self.mb_prov}/{self.mb_models[self.mb_idx]}")

    # ---------------------------------------------------- test bench

    def open_bench(self):
        if getattr(self, "bench", None) is None:
            self.bench = []
        self.bench_results = None
        self.mode = "test_bench"
        self.msg = ("r=run all  x=clear slots  Esc=close  "
                    "(add slots: model_browser c)")

    def bench_run(self):
        cfg = self.ed.load()
        results = []
        for prov_name, model in getattr(self, "bench", []):
            keys, base_url = self._keys_of(cfg, prov_name)
            r = self.ed.test_key(base_url, keys[0] if keys else "", model,
                                 timeout=45)
            results.append((prov_name, model, r))
        self.bench_results = results

    def bench_clear(self):
        self.bench = []
        self.bench_results = None

    # ---------------------------------------------------- search

    def open_add(self):
        self.mode = "add"
        self.input_buf = ""
        self.msg = "type part of a model name (e.g. glm 5, llama 3, qwen)"

    def search(self, query: str):
        """Probe all provider catalogs, fuzzy-match, build model picker."""
        cfg = self.ed.load()
        q = _norm(query)
        if not q:
            return
        by_model: Dict[str, List[str]] = {}
        notes = []
        for prov in cfg["providers"]:
            r = self.ed.probe_catalog(prov["base_url"], prov.get("key_env", ""))
            models = r.get("models", []) if r.get("ok") else []
            if not r.get("ok"):
                notes.append(f"{prov['name']}: {r.get('error','')[:50]}")
            for m in models:
                by_model.setdefault(m, []).append(prov["name"])
        hits = []
        for model_id, provs in by_model.items():
            n = _norm(model_id)
            if q in n or q in n.replace("/", ""):
                hits.append((model_id, provs))
        # rank: exact suffix first, then prefix, then alpha
        hits.sort(key=lambda mp: (not _norm(mp[0]).endswith(q),
                                  not _norm(mp[0]).startswith(q),
                                  mp[0]))
        self.matches = hits[:50]
        self.result_idx = 0
        if notes:
            self.probe_results = notes
        if not self.matches:
            self.mode = "result"
            extra = f" (probe issues: {'; '.join(notes)})" if notes else ""
            self.msg = f"'{query}' matched nothing{extra}"[:150]
        else:
            self.mode = "results"
            self.msg = (f"{len(self.matches)} match(es) — "
                        "j/k select, Enter pick, Esc cancel")

    def choose_model(self):
        if not (0 <= self.result_idx < len(self.matches)):
            return
        model_id, provs = self.matches[self.result_idx]
        self.chosen_model = model_id
        self.chosen_provs = provs
        self.pick_on = {p: True for p in provs}
        self.pick_idx = 0
        self.mode = "pick_prov"
        self.msg = "space=toggle provider  Enter=stage  Esc=back"

    def toggle_pick(self):
        provs = self.chosen_provs
        if not provs:
            return
        p = provs[self.pick_idx]
        self.pick_on[p] = not self.pick_on.get(p, True)

    # ---------------------------------------------------- provider mgmt

    def open_prov_mgmt(self):
        """Provider management: list providers, enable/disable/remove/add."""
        self.mode = "prov_mgmt"
        self.pick_idx = 0
        self.msg = "j/k=select  space=disable/enable  r=remove  a=add  Esc=close"

    def _cfg_provs(self, cfg=None):
        c = cfg if cfg is not None else self.ed.load()
        return c.get("providers", [])

    def _disabled_set(self, cfg=None):
        c = cfg if cfg is not None else self.ed.load()
        return {p["name"] for p in c.get("disabled_providers", [])}

    def toggle_provider_disable(self):
        cfg = self.ed.load()
        provs = self._cfg_provs(cfg)
        if not (0 <= self.pick_idx < len(provs)):
            return
        name = provs[self.pick_idx]["name"]
        dis = cfg.setdefault("disabled_providers", [])
        if any(d.get("name") == name for d in dis):
            # ENABLE: move back from disabled_providers into providers
            cfg["disabled_providers"] = [d for d in dis if d.get("name") != name]
            entry = next((d for d in dis if d.get("name") == name),
                         {"name": name})
            cfg["providers"].append(entry)
            self.msg = f"'{name}' staged: ENABLE"
        else:
            # DISABLE: remove from active providers (router cannot route to it),
            # keep definition in disabled_providers for one-keypress re-enable.
            blocked_by = [p["name"] for p in cfg["preferences"]
                          if any(r["provider"] == name for r in p["routes"])]
            if blocked_by:
                self.mode = "result"
                self.probe_results = [
                    f"cannot disable '{name}': still routed by preferences: "
                    + ", ".join(blocked_by),
                    "remove those routes first (e on the preference)"]
                return
            entry = provs[self.pick_idx]
            cfg["providers"] = [p for p in provs if p["name"] != name]
            dis.append(entry)
            self.msg = f"'{name}' staged: DISABLE (removed from router on next restart)"
        self._stage(cfg, f"toggle provider '{name}'")

    def remove_provider(self):
        cfg = self.ed.load()
        provs = self._cfg_provs(cfg)
        if not (0 <= self.pick_idx < len(provs)):
            return
        name = provs[self.pick_idx]["name"]
        # block removal if any active preference routes to it
        for pref in cfg["preferences"]:
            if any(r["provider"] == name for r in pref["routes"]):
                self.mode = "result"
                self.probe_results = [f"cannot remove '{name}': still routed by "
                                      f"preference '{pref['name']}' — remove that route first"]
                self.msg = ""
                return
        cfg["providers"] = [p for p in provs if p["name"] != name]
        self._stage(cfg, f"remove provider '{name}'")

    def open_prov_add(self):
        self.mode = "prov_add"
        self.input_buf = ""
        self.msg = "format: name base_url key_env  (space-separated)"

    def add_provider(self, line: str):
        parts = line.split()
        if len(parts) != 3:
            self.mode = "result"
            self.probe_results = ["need exactly: name base_url key_env"]
            self.msg = ""
            return
        name, base_url, key_env = parts
        if not base_url.startswith("http"):
            self.mode = "result"
            self.probe_results = ["base_url must start with http(s)://"]
            self.msg = ""
            return
        cfg = self.ed.load()
        names = {p["name"] for p in self._cfg_provs(cfg)}
        if name in names:
            self.mode = "result"
            self.probe_results = [f"provider '{name}' already exists"]
            self.msg = ""
            return
        # R5: verify the key_env var actually exists in ~/.hermes/.env
        # before we stage a provider that would never authenticate.
        env_path = Path.home() / ".hermes" / ".env"
        if not env_path.exists() or key_env not in env_path.read_text(errors="replace"):
            self.mode = "result"
            self.probe_results = [f"key_env '{key_env}' not found in ~/.hermes/.env"]
            self.msg = ""
            return
        # live-probe before staging
        probe = self.ed.probe_catalog(base_url, key_env, timeout=15)
        note = (f"probe: {len(probe['models'])} models" if probe["ok"]
                else f"probe FAILED ({probe['error'][:60]}) — added anyway")
        cfg["providers"].append({"name": name, "base_url": base_url,
                                 "key_env": key_env, "key_envs": [key_env]})
        self.probe_results = [note]
        self._stage(cfg, f"add provider '{name}'")

    def move_pick(self, delta: int):
        if self.chosen_provs:
            self.pick_idx = max(0, min(len(self.chosen_provs) - 1,
                                       self.pick_idx + delta))

    def stage_choice(self):
        """Stage preference with chosen model on selected providers only."""
        selected = [p for p in self.chosen_provs if self.pick_on.get(p)]
        if not selected:
            self.mode = "result"
            self.probe_results = ["no providers selected — nothing staged"]
            self.msg = ""
            return
        cfg = self.ed.load()
        if any(p["name"] == self.chosen_model for p in cfg["preferences"]):
            self.mode = "result"
            self.probe_results = [f"preference '{self.chosen_model}' "
                                  "already exists"]
            self.msg = ""
            return
        cfg["preferences"].append({
            "name": self.chosen_model,
            "context_window": 1048576,
            "routes": [{"provider": p, "model": self.chosen_model}
                       for p in selected],
        })
        self._stage(cfg, f"add '{self.chosen_model}' on {', '.join(selected)}")

    def _stage(self, cfg, reason: str):
        """Common staging -> diff gate."""
        self.pending = cfg
        self.pending_reason = reason
        self.mode = "diff"

    def stage_strategy_toggle(self):
        """Toggle routing strategy between configured order and LKGP (B-6).

        Staged through the same diff gate as every other edit — nothing is
        written until the user confirms the diff preview.
        """
        cfg = self.ed.load()
        cur = cfg.get("strategy", "")
        cfg["strategy"] = "" if cur == "lkgp" else "lkgp"
        self._stage(cfg, f"strategy: {cur or 'configured order'} -> {cfg['strategy'] or 'configured order'}")

    # ------------------------------------------------------- edit/delete

    def open_edit(self, pref_idx: int):
        self.pref_idx = pref_idx
        self.mode = "edit"
        self.route_idx = 0
        self.msg = "e=toggle  arrows/jk=cursor  m=move down  u=move up  d=delete pref  Enter=save  Esc=cancel"
        # Clear any stale pending; fresh edit session starts from current config
        self.pending = None

    def toggle_route(self):
        if self.pending is None:
            self.pending = self.ed.load()
        pref = self._current_pref(self.pending)
        if not pref:
            return
        providers = [p["name"] for p in self.pending.get("providers", [])]
        have = {r["provider"]: r for r in pref.get("routes", [])}
        idx = 0
        for prov in providers:
            if prov in have:
                if idx == self.route_idx:
                    pref["routes"].remove(have[prov])
                    self.msg = f"removed route {prov} (staged)"
                    return
                idx += 1
        for prov in providers:
            if prov not in have:
                pref["routes"].append(
                    {"provider": prov, "model": self._model_for(pref)})
                pref["routes"].sort(key=lambda r: providers.index(r["provider"]))
                self.msg = f"added route {prov} (staged)"
                return

    def _model_for(self, pref):
        models = [r["model"] for r in pref["routes"]]
        return models[0] if models else pref["name"]

    def move_route(self, delta: int):
        cfg = self.ed.load()
        pref = self._current_pref(cfg)
        if not pref:
            return
        n = len(pref["routes"])
        self.route_idx = max(0, min(n - 1, self.route_idx + delta))

    def move_route_in_fallback(self, delta: int):
        """Swap the selected route with its neighbor in the fallback chain.
        Changes are kept in the pending config; commit with Enter."""
        # Ensure we have a pending config to work with
        if self.pending is None:
            self.pending = self.ed.load()
        pref = self._current_pref(self.pending)
        if not pref:
            return
        routes = pref.get("routes", [])
        n = len(routes)
        if not (0 <= self.route_idx < n):
            return
        new_idx = self.route_idx + delta
        if not (0 <= new_idx < n):
            return  # already at edge
        routes[self.route_idx], routes[new_idx] = routes[new_idx], routes[self.route_idx]
        self.route_idx = new_idx
        # Do NOT call _stage() here — keep editing until Enter
        self.msg = f"reordered (staged) — Enter to save, Esc to cancel"

    def stage_delete(self, pref_idx: int):
        self.pref_idx = pref_idx
        pref = self._current_pref()
        self.pending_reason = pref["name"] if pref else "?"
        self.mode = "confirm_delete"

    def move_pref(self, pref_idx: int, delta: int, after=None):
        """Swap preference pref_idx with its neighbor (delta ±1).
        Moves accumulate in pending — nothing staged until Enter.
        Tab cursor follows the moved preference after commit."""
        # Work on pending if moves already staged, else start from disk
        if self.pending is None:
            self.pending = self.ed.load()
        prefs = self.pending.get("preferences", [])
        if not (0 <= pref_idx < len(prefs)):
            return
        new_idx = pref_idx + delta
        if not (0 <= new_idx < len(prefs)):
            return  # already at edge
        name = prefs[pref_idx]["name"]
        prefs[pref_idx], prefs[new_idx] = prefs[new_idx], prefs[pref_idx]
        self._moved_to = new_idx
        # remember where the tab cursor should land after commit
        if after:
            self._after_move = after
        else:
            self._after_move = None
        # DO NOT stage — keep accumulating moves; Enter stages everything
        self.pending_reason = f"preference reorder ({name} {'up' if delta < 0 else 'down'})"
        self.msg = f"moved '{name}' {'up' if delta < 0 else 'down'} (staged) — Enter to save, Esc to cancel"

    def exec_delete(self):
        cfg = self.ed.load()
        prefs = cfg["preferences"]
        if 0 <= self.pref_idx < len(prefs):
            name = prefs[self.pref_idx]["name"]
            del prefs[self.pref_idx]
            self._commit(cfg, f"delete preference '{name}'")

    def stage_save_edit(self):
        # Use pending if we have staged edits, otherwise load fresh
        if self.pending is None:
            self.pending = self.ed.load()
        self.pending_reason = "route edits"
        self.mode = "diff"

    # ---------------------------------------------------------- commit

    def commit_pending(self):
        if self.pending is not None:
            self._commit(self.pending, self.pending_reason)
            self.pending = None

    def _sync_hermes_default(self, cfg) -> Optional[str]:
        """Keep hermes model.default == router preferences[0].name.
        Runs after every successful commit; no-ops when already aligned.
        Returns a human note, or None."""
        prefs = cfg.get("preferences", [])
        if not prefs:
            return None
        want = prefs[0]["name"]
        # read current default without yaml dependency (TUI may run on system python)
        try:
            text = Path.home().joinpath(".hermes", "config.yaml").read_text()
            m = re.search(r"^model:\n  default: (\S+)", text, re.M)
            cur = m.group(1) if m else None
        except Exception:
            return None
        if cur == want:
            return None
        import shutil, subprocess
        hermes = shutil.which("hermes") or str(
            Path.home() / ".hermes/hermes-agent/venv/bin/hermes")
        try:
            r = subprocess.run([hermes, "config", "set", "model.default", want],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return f"SYNCED hermes default model: {cur} -> {want} (was dangling or stale)"
            return f"SYNC FAILED — set hermes model.default to '{want}' manually"
        except Exception as e:
            return f"SYNC FAILED: {str(e)[:60]}"

    def _auto_restart_router(self, reason: str = ""):
        """Restart the router immediately after a confirmed config commit.

        User-mandated behavior: approve the diff, save, and the router
        restarts at once so changes take effect without a manual window.
        Only runs from _commit/_commit_pending — AFTER the staged-diff gate
        has been confirmed — never from a keystroke.
        """
        try:
            from routeops.adapters.config_editor import restart_router
            result = restart_router()
            if result.get("ok"):
                self.need_restart = False  # restart done — banner cleared
                self.msg = f"SAVED: {reason}. Router restarted."
                self.probe_results = [f"✅ router restart: OK"] + (self.probe_results or [])
            else:
                # keep banner + manual command as fallback
                self.msg = f"SAVED: {reason}, but router restart FAILED: {result.get('detail', '')[:80]}"
                self.probe_results = [f"⚠ router restart failed — manual: kill $(pgrep -f '^\\./router$'); bash ~/CraftedWorkflowsOS/router/scripts/startr"] + (self.probe_results or [])
        except Exception as e:
            self.msg = f"SAVED: {reason}, but router restart errored: {str(e)[:60]}"

    def _commit(self, cfg, reason: str):
        try:
            old = self.ed.load()
            diff = self.ed.diff_summary(old, cfg)
            bak, path = self.ed.commit(cfg, reason)
            self.need_restart = True
            # User-mandated: a confirmed config commit restarts the router
            # immediately so changes go live without a manual window. The
            # banner in routes_curses only appears if that restart FAILS.
            self.probe_results = diff + [
                f"backup: {Path(bak).name}",
            ]
            self.mode = "result"
            self.msg = f"SAVED: {reason}. Restarting router..."
            # keep hermes default aligned with preferences[0] (dangling-link killer)
            sync_note = self._sync_hermes_default(cfg)
            if sync_note:
                self.probe_results = [sync_note] + self.probe_results
            # post-commit hook for preference moves (tab cursor follow)
            after_move = getattr(self, "_after_move", None)
            if after_move:
                try:
                    after_move()
                except Exception:
                    pass
            self._after_move = None
            # Force-refresh AppState config so screens reflect the change NOW
            # (not on the next 60s poll).
            try:
                from routeops.adapters.config_read import ConfigAdapter
                adapter = ConfigAdapter(str(self.ed.config_path))
                fresh = adapter.force_reload()
                if fresh is not None and self.app_state is not None:
                    self.app_state.config = fresh
            except Exception:
                pass  # poller will catch up within 60s worst case
            # AUTO-RESTART ROUTER — user-mandated: after a confirmed diff
            # commit, restart immediately so changes go live now.
            self._auto_restart_router(reason)
        except ConfigEditorError as e:
            self.pending = None
            self.mode = "result"
            self.msg = f"ABORTED: {e}"

    # ------------------------------------------------------------ tests

    def test_current_route(self, pref_idx: int, route_idx: int):
        cfg = self.ed.load()
        prefs = self._prefs(cfg)
        if not (0 <= pref_idx < len(prefs)):
            return
        pref = prefs[pref_idx]
        if not (0 <= route_idx < len(pref["routes"])):
            return
        route = pref["routes"][route_idx]
        prov = next((p for p in cfg["providers"]
                     if p["name"] == route["provider"]), None)
        if not prov:
            return
        self.mode = "result"
        self.msg = f"testing {route['provider']}/{route['model']} ..."
        r = self.ed.test_key(prov["base_url"], prov.get("key_env", ""),
                             route["model"])
        # R9: separate state — never write into probe_results (search uses
        # that; a route test must not clobber a pending search).
        self.route_test_result = [f"{route['provider']}/{route['model']}: "
                                  + ("OK — key works" if r["ok"]
                                     else f"FAIL {r['detail']}")]
        self.msg = "test complete"

    def cancel(self):
        self.pending = None
        self.mode = "closed"
        self.msg = ""
