"""
Providers Screen for RouteOps Curses TUI - Scrollable list with detail drawer.
"""

import curses
from datetime import datetime
from typing import Optional, List

from routeops.state import AppState
from routeops.models.provider import ProviderHealth
from routeops.tui.bars import (
    pct_bar, pct_subbar, pct_label, tier_color, tier_icon, hardfail_icon,
    provider_rollup, provider_routes, cooldown_text, BAR_CELLS,
    verdict, status_from_verdict, health_display, cell_display,
)


class ProvidersScreen:
    """Providers health screen with scrollable list and detail drawer."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_index = 0
        self.show_detail = False
        self.detail_scroll = 0
        # provider-management inline overlays
        self.mode = "closed"      # closed | add_prov | confirm_del | prov_diff | confirm_dis
        self.input_buf = ""
        self.overlay_msg = ""
        self.pending_cfg = None    # staged provider change awaiting diff review
        self.pending_diff = []
        self.pending_reason = ""
        # add-provider wizard state (mirrors Hermes' custom endpoint wizard)
        self.wiz_step = 0     # 0 url,1 key,2 verifying,3 model,4 name,5 confirm
        self.wiz_url = ""
        self.wiz_key = ""
        self.wiz_models = []
        self.wiz_model_idx = -1
        self.wiz_name = ""

    def set_size(self, width: int, height: int) -> None:
        self.width = max(40, width)
        self.height = max(10, height)

    def _sorted_providers(self) -> List[str]:
        """Providers with active routes first (most routes first), unrouted last.
        Shared by handle_key and render so indices always match."""
        providers = list(self.app_state.providers.keys())
        routed_count = {}
        if self.app_state.config and self.app_state.config.preferences:
            for pref in self.app_state.config.preferences:
                for route in pref.routes:
                    routed_count[route.provider] = (
                        routed_count.get(route.provider, 0) + 1)
        providers.sort(key=lambda n: (-routed_count.get(n, 0), n))
        return providers

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        providers = self._sorted_providers()
        max_index = len(providers) - 1

        # ---- inline overlays: add provider wizard / confirm delete ----
        if self.mode == "add_prov":
            return self._wiz_key(key)

        if self.mode == "confirm_del":
            if key in (ord('y'), ord('Y')):
                self._stage_remove_selected()
                return True
            if key in (27, ord('n'), ord('N')):
                self.mode = "closed"
                return True
            return True

        if self.mode == "prov_diff":
            # staged provider change review: y commits, n/Esc discards
            if key in (ord('y'), ord('Y'), 10, 13, curses.KEY_ENTER):
                self._commit_pending_provider()
                return True
            if key in (27, ord('n'), ord('N')):
                self.pending_cfg = None
                self.pending_diff = []
                self.mode = "closed"
                self.overlay_msg = "cancelled — nothing written"
                return True
            return True

        if key in (curses.KEY_UP, ord('k')):
            if self.show_detail:
                self.detail_scroll = max(0, self.detail_scroll - 1)
            else:
                self.selected_index = max(0, self.selected_index - 1)
                self._adjust_scroll(len(self._provider_rows()))
            # P1: navigation invalidates a stale test banner
            self.test_result = None
            self.test_lines = None
            return True

        if key in (curses.KEY_DOWN, ord('j')):
            if self.show_detail:
                self.detail_scroll += 1
            else:
                self.selected_index = min(len(self._provider_rows()) - 1, self.selected_index + 1)
                self._adjust_scroll(len(self._provider_rows()))
            # P1: navigation invalidates a stale test banner
            self.test_result = None
            self.test_lines = None
            return True

        if key in (10, curses.KEY_ENTER):  # Enter
            row = self._selected_provider_row()
            if not self.show_detail and row["name"] and not row["disabled"]:
                self.show_detail = True
                self.detail_scroll = 0
            return True

        if key == ord('t'):  # probe selected provider (home AND detail)
            row = self._selected_provider_row() if not self.show_detail else {"name": providers[self.selected_index] if providers else ""}
            if row["name"]:
                self._test_selected(providers)
            return True

        if key == ord('a'):  # add provider (home)
            if not self.show_detail:
                self.mode = "add_prov"
                self.input_buf = ""
                self.overlay_msg = ""
            return True

        if key == ord('x'):  # toggle disable/enable (selected row)
            if self.mode == "closed" and not self.show_detail:
                self._stage_disable_selected()
            return True

        if key == ord('d'):  # delete provider (home)
            if not self.show_detail and self._provider_rows():
                self.mode = "confirm_del"
                self.overlay_msg = self._selected_provider_row()["name"]
            return True

        if key in (27, ord('h'), ord('q')):  # Esc, h, q
            if self.show_detail:
                self.show_detail = False
                self.detail_scroll = 0
            else:
                return False  # Let parent handle
            return True
        
        if key == ord('m'):  # Key Management — inside provider detail only
            if self.show_detail and providers:
                self.app_state.detail_target = self._selected_provider_row()["name"]
                # open straight into THIS provider's keys (not all-providers)
                self.app_state._from_provider_detail = True
                return "keymgmt"
            return True

        return False

    def _wiz_key(self, key: int) -> bool:
        """Key handling for the add-provider wizard."""
        if key == 27:  # Esc cancels at any step
            self.mode = "closed"
            self.input_buf = ""
            return True
        if key in (10, 13, curses.KEY_ENTER):
            self._wiz_next()
            return True
        if key in (8, 127, curses.KEY_BACKSPACE):
            self.input_buf = self.input_buf[:-1]
            return True
        if 32 <= key <= 126:
            self.input_buf += chr(key)
            return True
        return True

    def _wiz_next(self):
        """Advance the wizard one step."""
        if self.wiz_step == 0:      # URL entered
            url = self.input_buf.strip()
            if not url.startswith("http"):
                self.overlay_msg = "URL must start with http(s)://"
                return
            self.wiz_url = url.rstrip("/")
            # auto default display name from the meaningful host segment
            # (skip generic first parts like api/www)
            host = url.split("//")[-1].split("/")[0]
            parts = [p for p in host.split(".") if p and p.lower() not in ("api", "www")]
            self.wiz_name = (parts[0].capitalize() if parts else host.split(".")[0].capitalize())
            self.input_buf = ""
            self.wiz_step = 1
        elif self.wiz_step == 1:    # API key entered
            if not self.input_buf.strip():
                self.overlay_msg = "API key cannot be empty"
                return
            self.wiz_key = self.input_buf.strip()
            self.input_buf = ""
            self.wiz_step = 2
            self._wiz_probe()
        elif self.wiz_step == 2:    # probe done -> show models
            self.wiz_step = 3
        elif self.wiz_step == 3:    # model selected (or skip)
            # allow typing a model name to filter, Enter = keep
            self.wiz_step = 4
            self.input_buf = ""
        elif self.wiz_step == 4:    # display name
            if self.input_buf.strip():
                self.wiz_name = self.input_buf.strip()
            self.input_buf = ""
            self.wiz_step = 5
            self._wiz_save()
        elif self.wiz_step == 5:    # done
            self.mode = "closed"
            self.input_buf = ""
            self.wiz_step = 0

    def _wiz_probe(self):
        """Probe the catalog and list models (step 2→3)."""
        from routeops.adapters.config_editor import ConfigEditor
        try:
            ce = ConfigEditor()
            r = ce.probe_catalog(self.wiz_url, self.wiz_key_env(), timeout=20)
            if r.get("ok"):
                self.wiz_models = r["models"]
                self.overlay_msg = (f"Verified endpoint — {len(self.wiz_models)} model(s) visible")
            else:
                self.wiz_models = []
                self.overlay_msg = f"Probe FAILED: {r.get('error','')[:60]} — still save?"
        except Exception as e:
            self.overlay_msg = f"probe error: {str(e)[:60]}"

    def _wiz_key_env(self) -> str:
        """Derive the .env var name: HERMES_CUSTOM_API_<HOST_UNDERSCORED>_API_KEY.
        Matches Hermes' convention: a leading 'api.'/'www.' is dropped, so
        https://api.xkiro.com -> HERMES_CUSTOM_API_XKIRO_COM_API_KEY."""
        import re
        host = self.wiz_url.split("//")[-1].split("/")[0]
        if host.lower().startswith(("api.", "www.")):
            host = host.split(".", 1)[1]
        stem = re.sub(r"[^A-Za-z0-9]", "_", host).upper().strip("_")
        return f"HERMES_CUSTOM_API_{stem}_API_KEY"

    def _wiz_save(self):
        """Write .env + config.json (EnvEditor + ConfigEditor)."""
        from routeops.adapters.config_editor import ConfigEditor, EnvEditor
        try:
            key_env = self._wiz_key_env()
            EnvEditor().set_var(key_env, self.wiz_key)
            ce = ConfigEditor()
            cfg = ce.load()
            names = {p.get("name") for p in cfg.get("providers", [])}
            name = self.wiz_name
            if name in names:
                name = name + "2"
            cfg.setdefault("providers", []).append({
                "name": name, "base_url": self.wiz_url,
                "key_env": key_env, "key_envs": [key_env],
                "models_discovered": True,
            })
            ce.commit(cfg, f"add provider '{name}' via wizard")
            self.overlay_msg = (f"API key saved to .env as {key_env}\n"
                                f"Endpoint saved. Use /model in chat or 'hermes model' to set a model.")
            self.wiz_models = []  # reset for next use
            self.wiz_key = ""
        except Exception as e:
            self.overlay_msg = f"save failed: {str(e)[:80]}"

    def _provider_rows(self) -> List[dict]:
        """All providers — active + disabled (from config disabled_providers).
        Cached 2s. Each row: {name, base_url, key_env, disabled}."""
        now = __import__("time").time()
        if getattr(self, "_rows_cache", None) is not None and \
           now - getattr(self, "_rows_at", 0) < 2:
            return self._rows_cache
        rows = []
        try:
            from routeops.adapters.config_editor import ConfigEditor
            cfg = ConfigEditor().load()
            dis = {p.get("name") for p in cfg.get("disabled_providers", [])}
            for p in cfg.get("providers", []):
                rows.append({"name": p.get("name", "?"),
                             "base_url": p.get("base_url", ""),
                             "key_env": next(iter(p.get("key_envs") or []), p.get("key_env", "")),
                             "disabled": False})
            for p in cfg.get("disabled_providers", []):
                rows.append({"name": p.get("name", "?"),
                             "base_url": p.get("base_url", ""),
                             "key_env": next(iter(p.get("key_envs") or []), p.get("key_env", "")),
                             "disabled": True})
            rows.sort(key=lambda r: (r["disabled"], r["name"].lower()))
        except Exception:
            rows = [{"name": n, "base_url": "", "key_env": "", "disabled": False}
                    for n in self.app_state.providers]
        self._rows_cache = rows
        self._rows_at = now
        return rows

    def _selected_provider_row(self) -> dict:
        rows = self._provider_rows()
        if not (0 <= self.selected_index < len(rows)):
            return {"name": "", "disabled": False}
        return rows[self.selected_index]

    def _stage_remove_selected(self):
        """Stage removal of the selected provider -> diff review."""
        row = self._selected_provider_row()
        name = row["name"]
        if not name:
            self.mode = "closed"
            return
        try:
            from routeops.adapters.config_editor import ConfigEditor
            ce = ConfigEditor()
            cfg = ce.load()
            for pref in cfg.get("preferences", []):
                if any(r.get("provider") == name for r in pref.get("routes", [])):
                    self.overlay_msg = (f"cannot remove '{name}': still routed by "
                                        f"preference '{pref.get('name')}' — remove that route first")
                    self.mode = "closed"
                    return
            before = len(cfg.get("providers", []))
            cfg["providers"] = [p for p in cfg.get("providers", [])
                                if p.get("name") != name]
            cfg["disabled_providers"] = [p for p in cfg.get("disabled_providers", [])
                                         if p.get("name") != name]
            if len(cfg.get("providers", [])) == before and not row["disabled"]:
                self.overlay_msg = f"provider '{name}' not found (active)"
                self.mode = "closed"
                return
            self.pending_reason = f"remove provider '{name}'"
            self._prepare_diff(cfg)
        except Exception as e:
            self.overlay_msg = f"stage failed: {str(e)[:80]}"
            self.mode = "closed"

    def _stage_disable_selected(self):
        """Stage disable/enable toggle for the selected provider -> diff review."""
        row = self._selected_provider_row()
        name = row["name"]
        if not name:
            self.mode = "closed"
            return
        try:
            from routeops.adapters.config_editor import ConfigEditor
            ce = ConfigEditor()
            cfg = ce.load()
            dis = cfg.setdefault("disabled_providers", [])
            if row["disabled"]:
                # ENABLE: move back from disabled_providers into providers
                entry = next((d for d in dis if d.get("name") == name),
                             {"name": name, "key_env": row["key_env"]})
                if "base_url" not in entry:
                    entry["base_url"] = row["base_url"]
                cfg["disabled_providers"] = [d for d in dis if d.get("name") != name]
                if not any(p.get("name") == name for p in cfg.get("providers", [])):
                    cfg.setdefault("providers", []).append(entry)
                self.pending_reason = f"ENABLE provider '{name}'"
            else:
                # DISABLE: refuse while routed; move provider -> disabled
                blocked_by = [p.get("name") for p in cfg.get("preferences", [])
                              if any(r.get("provider") == name for r in p.get("routes", []))]
                if blocked_by:
                    self.overlay_msg = (f"cannot disable '{name}': still routed by "
                                        f"preferences: {', '.join(blocked_by)}")
                    self.mode = "closed"
                    return
                entry = next((p for p in cfg.get("providers", [])
                              if p.get("name") == name), {"name": name})
                cfg["providers"] = [p for p in cfg.get("providers", [])
                                    if p.get("name") != name]
                if not any(d.get("name") == name for d in dis):
                    dis.append(entry)
                self.pending_reason = f"DISABLE provider '{name}'"
            self._prepare_diff(cfg)
        except Exception as e:
            self.overlay_msg = f"stage failed: {str(e)[:80]}"
            self.mode = "closed"

    def _prepare_diff(self, cfg: dict):
        """Build diff vs current config -> enter prov_diff mode."""
        from routeops.adapters.config_editor import ConfigEditor
        try:
            old = ConfigEditor().load()
            self.pending_diff = ConfigEditor().diff_summary(old, cfg)
        except Exception as e:
            self.pending_diff = [f"diff error: {e}"]
        self.pending_cfg = cfg
        self.mode = "prov_diff"
        self.input_buf = ""

    def _commit_pending_provider(self):
        from routeops.adapters.config_editor import ConfigEditor
        try:
            ConfigEditor().commit(self.pending_cfg, reason=self.pending_reason)
            self.overlay_msg = f"SAVED: {self.pending_reason}"
        except Exception as e:
            self.overlay_msg = f"SAVE FAILED: {str(e)[:80]}"
        self.pending_cfg = None
        self.pending_diff = []
        self._rows_cache = None
        self.mode = "closed"

    def _render_wiz(self, term, y: int, rows: int, cols: int) -> None:
        """Render the add-provider wizard (step by step)."""
        term.write(y, 0, "┌ ADD CUSTOM PROVIDER ───────────────────────────────────────────┐"[:cols], curses.color_pair(1))
        y += 1
        step = self.wiz_step
        if step == 0:
            term.write(y, 2, "Custom OpenAI-compatible endpoint configuration:", curses.A_BOLD)
            y += 1
            term.write(y, 2, "API base URL [e.g. https://api.example.com/v1]:", curses.color_pair(5))
            y += 1
            term.write(y, 2, f"> {self.input_buf}_", curses.color_pair(2) | curses.A_BOLD)
        elif step == 1:
            term.write(y, 2, "API key [sk-...]:", curses.color_pair(5))
            y += 1
            term.write(y, 2, f"> {'*' * len(self.input_buf)}_", curses.color_pair(2) | curses.A_BOLD)
            y += 1
            term.write(y, 2, f"endpoint: {self.wiz_url}", curses.A_DIM)
        elif step == 2:
            term.write(y, 2, "Verifying endpoint…", curses.color_pair(3) | curses.A_BOLD)
            y += 1
            if self.overlay_msg:
                term.write(y, 2, self.overlay_msg[:cols-6], curses.color_pair(5))
        elif step == 3:
            term.write(y, 2, f"Verified endpoint — {len(self.wiz_models)} model(s) visible", curses.color_pair(2))
            y += 1
            term.write(y, 2, "Select a default model (Enter to skip — set later via /model):", curses.color_pair(5))
            y += 1
            for i, m in enumerate(self.wiz_models[:20]):
                line = f"  {i+1}. {m}"
                term.write(y, 2, line[:cols-6], curses.A_DIM)
                y += 1
            term.write(y, 2, f"> {self.input_buf}_", curses.color_pair(2) | curses.A_BOLD)
        elif step == 4:
            term.write(y, 2, "Display name [Enter to keep default]:", curses.color_pair(5))
            y += 1
            term.write(y, 2, f"default: {self.wiz_name}", curses.A_DIM)
            y += 1
            term.write(y, 2, f"> {self.input_buf}_", curses.color_pair(2) | curses.A_BOLD)
        elif step == 5:
            term.write(y, 2, self.overlay_msg[:cols-6], curses.color_pair(2) | curses.A_BOLD)
            y += 1
            term.write(y, 2, "(Enter to close)", curses.A_DIM)
        term.write(rows - 1, 0, "[Enter]=next  [Esc]=cancel"[:cols], curses.color_pair(5) | curses.A_DIM)

    def _test_selected(self, providers: List[str]) -> None:
        """Live-test ALL key pools of the selected provider, each individually.
        Result: per-key status lines shown in the detail-style inline banner.
        Runs on a daemon thread — probes take up to 15s each and must never
        freeze the TUI. The provider name comes from the rendered row
        (self._selected_provider_row()) — NOT from the providers-list index."""
        from routeops.adapters.config_editor import ConfigEditor
        name = self._selected_provider_row()["name"]
        pm = self.app_state.providers.get(name)
        if pm is None:
            self.test_result = f"TEST {name}: not available"
            self.test_lines = None
            return
        ce = ConfigEditor()
        key_envs = list(getattr(pm.config, 'key_envs', None) or
                        ([pm.config.key_env] if getattr(pm.config, 'key_env', '') else []))
        if not key_envs:
            self.test_result = f"TEST {name}: no keys configured"
            self.test_lines = None
            return
        import threading
        self.test_result = f"CATALOG PROBE {name}: testing {len(key_envs)} key pool(s) …"
        self.test_lines = None

        def run():
            try:
                lines = []
                any_ok = False
                for i, kenv in enumerate(key_envs):
                    r = ce.probe_catalog(pm.config.base_url, kenv, timeout=15)
                    if r.get("ok"):
                        lines.append(f"  [{i}] {kenv:<44} ● live ({len(r['models'])} models)")
                        any_ok = True
                    else:
                        lines.append(f"  [{i}] {kenv:<44} ✗ FAIL {r.get('error','')[:30]}")
                verdict = "OK" if any_ok else "ALL KEYS DEAD"
                self.test_result = f"CATALOG PROBE {name}: {verdict} — {len(key_envs)} pool(s):"
                self.test_lines = lines
            except Exception as e:
                self.test_result = f"CATALOG PROBE {name}: probe error {str(e)[:50]}"
                self.test_lines = None

        threading.Thread(target=run, daemon=True).start()

    test_result = None
    test_lines = None

    def _adjust_scroll(self, item_count: int) -> None:
        """Adjust scroll offset to keep selected item visible."""
        visible_rows = self.height - 6  # Approximate visible items
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_rows:
            self.scroll_offset = self.selected_index - visible_rows + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, item_count - visible_rows)))

    def render(self, term) -> None:
        """Render providers screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Inline overlays: add provider wizard / confirm delete
        if self.mode == "add_prov":
            self._render_wiz(term, y, rows, cols)
            return
        if self.mode == "confirm_del":
            term.write(y, 0, "┌ DELETE PROVIDER ───────────────────────────────────────────────┐"[:cols], curses.color_pair(4))
            y += 1
            term.write(y, 2, f"Remove provider '{self.overlay_msg}'?  [y]=yes  [n/Esc]=no", curses.color_pair(4) | curses.A_BOLD)
            return
        if self.mode == "prov_diff":
            # staged change review — diff lines + y/n confirm
            term.write(y, 0, ("┌ STAGED CHANGE (" + self.pending_reason[:40] + ") ──────────────────────┐")[:cols], curses.color_pair(5))
            y += 1
            for line in self.pending_diff[: rows - y - 3]:
                term.write(y, 2, line[: cols - 6], curses.color_pair(5))
                y += 1
            term.write(rows - 1, 0, "[y]=commit  [n/Esc]=discard"[:cols], curses.color_pair(3) | curses.A_BOLD)
            return

        # Title bar — context-dependent: home shows list actions; detail
        # shows the provider name (the detail body already carries its own
        # action hint line).
        from routeops.tui.render_std import render_header, render_box
        y = render_header(term.write, self.app_state, y, cols, rows)

        if self.show_detail and self.app_state.providers:
            provider_name = self._selected_provider_row()["name"]
            title = f"● PROVIDERS — DETAIL: {provider_name}"
        else:
            title = f"● PROVIDERS  [{len(self.app_state.providers)} providers]  [Enter=detail]  [t]=probe  [a]=add  [x]=disable/enable  [d]=del  [Esc] Back"
        term.write(y, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Inline test result banner (multi-line per-key results)
        if self.test_result and not self.show_detail:
            ok = "OK" in self.test_result
            term.write(y, 0, self.test_result[:cols],
                       (curses.color_pair(2) if ok else curses.color_pair(4)) | curses.A_BOLD)
            y += 1
            if self.test_lines:
                for tl in self.test_lines:
                    color = curses.color_pair(2) if "● live" in tl else curses.color_pair(4)
                    term.write(y, 0, tl[:cols], color)
                    y += 1

        if self.show_detail and self.app_state.providers:
            # Detail drawer
            provider_name = self._selected_provider_row()["name"]
            pm = self.app_state.providers[provider_name]
            detail_lines, seg_lines = self._build_detail_lines(pm, cols - 4)
            # Probe results (from [t]) render INSIDE the detail view — a
            # banner only drawn on home was invisible while in detail.
            # OK rows green, FAIL rows red.
            if self.test_result and self.test_lines:
                title_c = curses.color_pair(2) if "OK" in self.test_result else curses.color_pair(4)
                seg_lines[len(detail_lines)] = [(self.test_result, title_c | curses.A_BOLD)]
                detail_lines.append("")
                for tl in self.test_lines:
                    row_c = curses.color_pair(2) if "● live" in tl else curses.color_pair(4)
                    seg_lines[len(detail_lines)] = [(tl, row_c)]
                    detail_lines.append("")
            # Actions live INSIDE the provider screen: t/m.
            detail_lines.append("")
            detail_lines.append("[t]=probe keys  [m]=key management  [Esc]=back to provider list")
            self._render_detail_box(term, y, cols, f"DETAIL: {provider_name}",
                                    detail_lines, seg_lines)
        else:
            # Provider list — active first, disabled dimmed + marked.
            rows_all = self._provider_rows()
            visible_rows = rows - y - 3

            shown = 0
            body = []
            for i in range(self.scroll_offset, min(self.scroll_offset + visible_rows, len(rows_all))):
                row = rows_all[i]
                name = row["name"]
                pm = self.app_state.providers.get(name)
                if pm is None:
                    segs = [(f"{name:<22}", curses.A_DIM),
                            (f"[DISABLED]", curses.color_pair(4) | curses.A_BOLD)]
                else:
                    segs = self._format_provider_segs(name, pm, cols - 2)
                    if row["disabled"]:
                        segs = [(t, a | curses.A_DIM) for t, a in segs]
                        segs.append((f" [DISABLED]", curses.color_pair(4) | curses.A_BOLD))

                if i == self.selected_index:
                    segs = [(t, a | curses.color_pair(9) | curses.A_BOLD) for t, a in segs]
                body.append(segs)
                shown += 1

            # Summary line — SAME accounting as the rendered rows. Counting by
            # /health alone made the summary claim 'OK: 3' while two rows
            # rendered DEGRADED — the summary must match what is on screen.
            n_total = len(rows_all)
            summary = ""
            if n_total:
                n_ok = n_cool = n_hard = n_noroute = n_deg = n_dis = 0
                for row in rows_all:
                    name = row["name"]
                    if row["disabled"]:
                        n_dis += 1
                        continue
                    pm = self.app_state.providers.get(name)
                    if pm is None:
                        continue
                    h = pm.health
                    if provider_routes(self.app_state, name) == 0:
                        n_noroute += 1
                        continue
                    if h.hard_failed:
                        n_hard += 1
                        continue
                    if not h.healthy:
                        n_cool += 1
                        continue
                    pct, ok_t, fail_t, _ab = provider_rollup(self.app_state, name)
                    v = verdict(pct, ok_t + fail_t)
                    # P4: a zero-traffic provider renders green NO DATA YET in
                    # the rows; the summary must count that as OK, not
                    # Degraded — rows and summary must agree.
                    if v in ("healthy", "no_data"):
                        n_ok += 1
                    else:
                        n_deg += 1
                routed_total = n_total - n_noroute - n_dis
                summary = f"Total: {n_total}  Routed: {routed_total}  OK: {n_ok}"
                if n_deg:
                    summary += f"  Degraded: {n_deg}"
                if n_cool:
                    summary += f"  Cool: {n_cool}"
                if n_hard:
                    summary += f"  Hard-failed: {n_hard}"
                if n_dis:
                    summary += f"  Disabled: {n_dis}"
                if n_noroute:
                    summary += f"  No-route: {n_noroute}"
                if shown < n_total:
                    summary += f"  (showing {shown} of {n_total})"
            body.append("")
            body.append([(summary, curses.color_pair(5) | curses.A_DIM)])
            y = render_box(term.write, y, cols, "PROVIDERS", body, rows)

            # Scroll indicator
            if len(rows_all) > visible_rows:
                indicator = f"  ↑↓ {self.scroll_offset + 1}-{min(self.scroll_offset + visible_rows, len(rows_all))} of {len(rows_all)}"
                term.write(y, 0, indicator[:cols], curses.color_pair(5))

    def _format_provider_segs(self, name: str, pm, cols: int) -> List:
        """Provider row as colored segments: bar (JSONL rollup %) + icon + status
        + fails + cooldown + models + keys. One visual language with Overview."""
        health = pm.health
        failures = health.failures if health.failures else 0
        models = str(health.catalog_models) if health.catalog_models else "0"
        if getattr(pm.config, 'key_envs', None):
            keys = len(pm.config.key_envs)
        elif getattr(pm.config, 'key_env', '') or (hasattr(pm.config, 'key_envs') and getattr(pm.config, 'key_envs') == ()):
            keys = 1
        else:
            keys = 0

        pct, _ok, _fail, _abort = provider_rollup(self.app_state, name)
        n_routes = provider_routes(self.app_state, name)

        # Unrouted provider: configured but no preference routes to it.
        if n_routes == 0:
            segs = [
                (f"{name:<12}  ", curses.A_DIM),
                (pct_bar(None), curses.A_DIM),
                (f" {pct_label(None)} ", curses.A_DIM),
                ("◎ ", curses.A_DIM),
                ("NO ROUTE     ", curses.A_DIM),
                (f" 0 routes in any preference", curses.A_DIM),
            ]
            return segs
        # ONE shared verdict drives the bar, the icon AND the status word —
        # '0.0% ○ HEALTHY' is impossible by construction.
        in_cd = not health.healthy and not health.hard_failed
        hv = health_display(pct, _ok + _fail, _fail, in_cd)

        # Status word + icon: hard-failed wins, then /health state
        if health.hard_failed:
            icon, ic = hardfail_icon()
            status = "HARD-FAILED"
            st_color = curses.color_pair(4) | curses.A_BOLD
            bar_c = curses.color_pair(4)
        elif in_cd:
            icon, ic = ("◐", curses.color_pair(3))
            status = "COOLDOWN"
            st_color = curses.color_pair(3)
            bar_c = hv.bar_color
        else:
            icon, ic = hv.icon, hv.text_color
            status = hv.status
            st_color = hv.text_color
            bar_c = hv.bar_color

        cd = cooldown_text(health)

        segs = [
            (f"{name:<12}  ", curses.A_BOLD),
            (pct_bar(hv.bar_pct), bar_c),
            (f" {pct_label(hv.bar_pct)} ", bar_c | curses.A_BOLD),
            (f"{icon} ", ic),
            (f"{status:<12} ", st_color),
            (f" fails:{failures:<2} ", 0),
            # P7: surface client-abort count — a provider with many aborts
            # is not failing, and hiding it hid a real signal.
            (f" aborts:{_abort:<2} " if _abort else "", 0),
            (f"{cd:>7}  ", 0),
            (f"models:{models:<3} ", 0),
            (f"keys:{keys}", 0),
        ]
        return segs

    # P6: full-file event source — same pattern as Diagnostics, so the
    # provider detail drawer shows real totals, not the 500-event window.
    def _full_provider_events(self, provider_name: str) -> List:
        from routeops.adapters.history_index import HistoryIndex
        from routeops.config import ROUTER_EVENTS_JSONL
        if getattr(self, "_full_index", None) is None:
            self._full_index = HistoryIndex(str(ROUTER_EVENTS_JSONL))
            self._full_index.refresh()
        elif getattr(self, "_full_last", 0) < __import__("time").time() - 5:
            self._full_index.refresh()
        self._full_last = __import__("time").time()
        return [e for e in self._full_index._events
                if e.provider == provider_name]

    def _build_detail_lines(self, pm, width: int):
        """Build detail lines for provider. Model-health and recent-event rows
        come back as colored segments via seg_rows: {line_idx: [(text, attr)]}."""
        lines = []
        w = width - 4
        health = pm.health

        seg_rows = {}  # line idx -> colored segments

        # Unrouted provider: no preference routes here — say so plainly
        if provider_routes(self.app_state, pm.config.name) == 0:
            lines.append("Status: NO ROUTE")
            lines.append("This provider serves no traffic — no preference")
            lines.append("routes to it. Health/catalog below are what the")
            lines.append("provider WOULD offer if routed.")
            lines.append("")
            lines.append(f"Catalog models: {health.catalog_models if health.catalog_models else 0}")
            lines.append("")
            lines.append("KEY POOLS:")
            if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
                for i, key_env in enumerate(pm.config.key_envs):
                    lines.append(f"  [{i}] {key_env}")
            elif pm.config.key_env:
                lines.append(f"  [0] {pm.config.key_env}")
            else:
                lines.append("  (no key_env configured)")
            lines.append("")
            lines.append(f"Base URL: {pm.config.base_url}")
            lines.append("")
            lines.append("To route it: Routes screen → [e] edit a preference,")
            lines.append("or [o] providers → [m] browse this provider's models.")
            return [line[:w] for line in lines], seg_rows

        # Health status
        if health.hard_failed:
            lines.append(f"Status: HARD-FAILED")
        elif not health.healthy:
            lines.append(f"Status: COOLDOWN")
        else:
            lines.append(f"Status: HEALTHY")

        lines.append(f"Failures: {health.failures if health.failures else 0}")
        lines.append(cooldown_text(health).replace("cd:", "Cooldown: "))

        lines.append(f"Catalog models: {health.catalog_models if health.catalog_models else 0}")
        lines.append("")

        # Key pools
        lines.append("KEY POOLS:")
        if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
            for i, key_env in enumerate(pm.config.key_envs):
                lines.append(f"  [{i}] {key_env}")
        elif pm.config.key_env:
            lines.append(f"  [0] {pm.config.key_env}")
        else:
            lines.append("  (no key_env configured)")

        lines.append("")
        lines.append(f"Base URL: {pm.config.base_url}")

        # Rollup header: real success % across this provider's active models
        pct, ok_t, fail_t, abort_t = provider_rollup(self.app_state, pm.config.name)
        hv = health_display(pct, ok_t + fail_t, fail_t)
        seg_rows[len(lines)] = [
            ("  ", 0),
            (pct_bar(hv.bar_pct), hv.bar_color),
            (f" {pct_label(hv.bar_pct)} ", hv.bar_color | curses.A_BOLD),
            (hv.icon, hv.text_color),
            (f"  {hv.status}  rollup across active models  ok:{ok_t}  fail:{fail_t}", 0),
        ]
        if abort_t:
            seg_rows[len(lines)].append((f"  aborted:{abort_t}", curses.A_DIM))
        lines.append("")  # placeholder
        lines.append("")

        # Per-cell health from /health routes[] (B-7) — the router now
        # reports every provider×model cell individually. This is the
        # ground truth that the provider-level green used to hide.
        # Q1/P2: shared cell_display renderer — same visuals as Routes.
        cells = getattr(pm.health, "cells", ()) or ()
        if cells:
            lines.append("ROUTER CELL HEALTH (from /health routes[]):")
            for cell in cells:
                c_short = cell.model.split("/")[-1][:24]
                c_icon, c_status, c_color = cell_display(cell)
                seg_rows[len(lines)] = [
                    ("  ", 0),
                    (c_icon, c_color),
                    (f"  {c_short:<24}", 0),
                    (f" {c_status:<20}", c_color),
                    (f" fail:{cell.failures:<3}", 0),
                ]
                lines.append("")  # placeholder
            lines.append("")

        # Per-model health derived from JSONL (route_result success/fail counts)
        # Config is source of truth: only models in active preferences shown
        mh = getattr(self.app_state, '_model_health', {})
        # Build the set of models actively routed for THIS provider
        active_models_for_prov = set()
        if self.app_state.config:
            for pref in self.app_state.config.preferences:
                for route in pref.routes:
                    if route.provider == pm.config.name:
                        active_models_for_prov.add(route.model)
        prov_models = {k: v for k, v in mh.items()
                       if k[0] == pm.config.name and k[1] in active_models_for_prov}
        lines.append("")
        if prov_models or active_models_for_prov:
            lines.append("MODEL HEALTH (active routes, from JSONL results):")
            # Show every active model, even with no data yet
            for model in sorted(active_models_for_prov):
                st = prov_models.get((pm.config.name, model))
                m_short = model.split("/")[-1][:24]
                if st:
                    m_ok = st.get("ok", 0)
                    m_fail = st.get("fail", 0)
                    m_abort = st.get("abort", 0)
                    total = m_ok + m_fail
                    # ONE shared verdict — zero-traffic reads as NO DATA (empty bar),
                    # a single failure reads DEGRADED, never a fake 0% red bar.
                    mpct = (m_ok / total * 100) if total else None
                    hv = health_display(mpct, total, m_fail)
                    last = st["last_outcome"] or "—"
                    lc = (curses.color_pair(2) if last == "success"
                          else curses.color_pair(4) if last in ("failure", "timeout")
                          else curses.A_DIM)
                    counts = f" ok:{m_ok:<4} fail:{m_fail:<4}"
                    if m_abort:
                        counts += f" abort:{m_abort:<4}"
                    seg_rows[len(lines)] = [
                        ("  ", 0),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)} ", hv.bar_color | curses.A_BOLD),
                        (hv.icon, hv.text_color),
                        (f" {m_short:<24}", 0),
                        (counts, 0),
                        (f"  last: {last}", lc),
                    ]
                    lines.append("")  # placeholder
                else:
                    # Zero traffic for this model — configured but not yet
                    # exercised: full green bar + NO DATA YET.
                    hv = health_display(None, 0, 0)
                    seg_rows[len(lines)] = [
                        ("  ", 0),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)} ", hv.bar_color),
                        (hv.icon, hv.text_color),
                        (f" {m_short:<24}", curses.A_DIM),
                        ("  no results yet", curses.A_DIM),
                    ]
                    lines.append("")  # placeholder
        else:
            lines.append("MODEL HEALTH: no active routes for this provider")

        # Recent events for this provider — NEWEST FIRST, colored segments
        lines.append("")
        lines.append("RECENT EVENTS (newest first):")
        from datetime import datetime as _dt
        _now = _dt.now().astimezone()
        # P6: full-file events, not the 500-event memory window
        prov_events = self._full_provider_events(pm.config.name)
        CYAN = curses.color_pair(5)
        GREEN = curses.color_pair(2)
        RED = curses.color_pair(4)
        YELLOW = curses.color_pair(3)
        WHITE = 0
        for event in reversed(prov_events[-7:]):
            ts = event.timestamp.strftime("%H:%M:%S") if event.timestamp else "--:--:--"
            model = event.model or ""
            outcome = event.outcome or ""
            status = event.status if event.status else ""
            error = event.error or ""
            # age tag
            try:
                age = (_now - event.timestamp).total_seconds() if event.timestamp else 1e9
            except (TypeError, ValueError):
                age = 1e9
            age_str = ("live" if age < 90 else
                       f"{int(age // 60)}m" if age < 5400 else
                       f"{age / 3600:.1f}h" if age < 129600 else
                       f"{age / 86400:.0f}d")
            if event.event_type == "route_attempt":
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       ("ATTEMPT", YELLOW)]
            elif status and error:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{status} {error[:28]}", RED)]
            elif outcome == "success" or status == 200:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{status or 'OK'}", GREEN)]
            else:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{outcome or event.event_type}", YELLOW)]
            seg.append((f"  [{age_str}]", CYAN if age_str == "live" else curses.color_pair(3)))
            seg_rows[len(lines)] = seg
            lines.append("")  # placeholder
        if not prov_events:
            seg_rows[len(lines)] = [(f"  (no events for {pm.config.name})", WHITE)]
            lines.append("")  # placeholder
        return [line[:w] for line in lines], seg_rows

    def _render_detail_box(self, term, y: int, cols: int, title: str,
                           lines: List[str],
                           seg_rows: Optional[dict] = None) -> None:
        """Render detail box with scroll support. Any line idx present in
        seg_rows renders as colored segments; others render as plain text."""
        w = cols - 4
        max_visible = term.size.rows - y - 2

        # Top border
        term.write(y, 0, f"┌ {title} {'─' * max(0, w - len(title) - 2)}┐", curses.color_pair(1))

        # Content
        for i in range(self.detail_scroll, min(self.detail_scroll + max_visible, len(lines))):
            row = y + 1 + (i - self.detail_scroll)
            segs = (seg_rows or {}).get(i)
            if segs:
                term.write(row, 2, "│ ", curses.color_pair(1))
                x = 4
                room = w - 2
                for text, attr in segs:
                    if room <= 0:
                        break
                    term.write(row, x, text[:room], attr)
                    x += len(text[:room])
                    room -= len(text[:room])
                term.write(row, w + 2, " │", curses.color_pair(1))
            else:
                term.write(row, 2, f"│ {lines[i].ljust(w - 2)} │")

        # Bottom border
        term.write(y + 1 + min(max_visible, len(lines) - self.detail_scroll), 0,
                   "└" + "─" * w + "┘", curses.color_pair(1))

        # Scroll indicator
        if len(lines) > max_visible:
            indicator = f"  ↑↓ {self.detail_scroll + 1}-{min(self.detail_scroll + max_visible, len(lines))} of {len(lines)}"
            term.write(y + 1 + max_visible, 2, indicator[:w], curses.color_pair(5))
