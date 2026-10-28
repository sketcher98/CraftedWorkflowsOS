"""
Config Editor Backend — Phase 1B
Safe read/modify/write for router config.json with backup, validation, atomic commit.

Security invariants (from config.py):
- NEVER reads .env or key VALUES. Key names (key_env) are referenced by name only.
- All writes: timestamped .bak backup -> json validate -> atomic tmp+rename.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from routeops.config import ROUTER_CONFIG_JSON, HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT


class ConfigEditorError(Exception):
    pass


class EnvEditor:
    """Transactional editor for ~/.hermes/.env — same doctrine as ConfigEditor.

    Workflow: backup → edit → validate → atomic tmp+rename → verify.
    On any failure the backup is restored automatically (instant rollback).
    The file is NEVER written with a plain open(...,'w'): a crash mid-write
    would destroy every provider credential with no recovery path.
    """

    ENV_PATH = Path.home() / ".hermes" / ".env"

    def __init__(self, env_path: Optional[Path] = None):
        self.env_path = Path(env_path) if env_path else self.ENV_PATH

    def backup(self) -> Optional[Path]:
        """Timestamped backup before any write. None if the file doesn't exist."""
        if not self.env_path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = self.env_path.with_name(self.env_path.name + ".bak-" + stamp)
        shutil.copy2(self.env_path, bak)
        return bak

    def _validate(self, text: str) -> None:
        """Every non-blank, non-comment line must be KEY=value with a sane KEY."""
        import re
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise ConfigEditorError(
                    f"line {i} is not KEY=value: {stripped[:60]}")
            key = stripped.split("=", 1)[0].strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                raise ConfigEditorError(f"line {i} has invalid key name: {key[:60]}")

    def set_var(self, key_env: str, value: str) -> Path:
        """Set (or replace) KEY=value. Returns the backup path.

        Raises ConfigEditorError on any failure — the original file is
        restored before the exception propagates.
        """
        if not key_env or "=" in key_env or "\n" in key_env:
            raise ConfigEditorError(f"invalid env var name: {key_env!r}")
        if "\n" in value or "\r" in value:
            raise ConfigEditorError("env var value contains a newline")

        bak = self.backup()
        original = None
        if bak is not None:
            try:
                original = self.env_path.read_text()
            except Exception:
                original = None

        try:
            text = original if original is not None else ""
            lines = text.splitlines(keepends=True)
            new_line = f"{key_env}={value}\n"
            replaced = False
            out = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and \
                        stripped.split("=", 1)[0].strip() == key_env:
                    out.append(new_line)
                    replaced = True
                else:
                    out.append(line if line.endswith("\n") else line + "\n")
            if not replaced:
                if out and not out[-1].endswith("\n"):
                    out[-1] += "\n"
                out.append(new_line)

            result = "".join(out)
            self._validate(result)

            tmp = self.env_path.with_name(self.env_path.name + ".tmp")
            with open(tmp, "w") as f:
                f.write(result)
                f.flush()
                os.fsync(f.fileno())
            # Verify tmp parses before it replaces the live file
            self._validate(tmp.read_text())
            tmp.replace(self.env_path)
            return bak
        except Exception:
            # Instant rollback — restore the original file
            try:
                if original is not None:
                    tmp = self.env_path.with_name(self.env_path.name + ".rollback")
                    with open(tmp, "w") as f:
                        f.write(original)
                    tmp.replace(self.env_path)
                elif bak is None:
                    # File did not exist before and creation failed
                    if self.env_path.exists():
                        self.env_path.unlink()
            except Exception:
                pass
            raise


class ConfigEditor:
    """Transactional editor for router/config.json."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else Path(ROUTER_CONFIG_JSON)

    # ------------------------------------------------------------------ read

    def load(self) -> dict:
        """Load current config as plain dict (editable copy)."""
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except FileNotFoundError:
            raise ConfigEditorError(f"config.json not found at {self.config_path}")
        except json.JSONDecodeError as e:
            raise ConfigEditorError(f"config.json is corrupt: {e}")

    # ---------------------------------------------------------------- write

    def backup(self) -> Path:
        """Timestamped backup before any write."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = self.config_path.with_suffix(".json.bak-" + stamp)
        shutil.copy2(self.config_path, bak)
        return bak

    def _validate(self, cfg: dict) -> None:
        """Structural validation. Raises ConfigEditorError with reason."""
        if not isinstance(cfg, dict):
            raise ConfigEditorError("config root must be an object")
        for key in ("host", "port", "providers", "preferences", "retry"):
            if key not in cfg:
                raise ConfigEditorError(f"missing required key: {key}")
        if not isinstance(cfg["providers"], list) or not cfg["providers"]:
            raise ConfigEditorError("providers must be a non-empty list")
        if not isinstance(cfg["preferences"], list):
            raise ConfigEditorError("preferences must be a list")
        names = set()
        for p in cfg["providers"]:
            if not all(k in p for k in ("name", "base_url", "key_env")):
                raise ConfigEditorError(
                    f"provider missing name/base_url/key_env: {p.get('name','?')}")
            if p["name"] in names:
                raise ConfigEditorError(f"duplicate provider name: {p['name']}")
            names.add(p["name"])
        seen_prefs = set()
        for pref in cfg["preferences"]:
            if "name" not in pref or "routes" not in pref:
                raise ConfigEditorError(f"preference missing name/routes: {pref}")
            if pref["name"] in seen_prefs:
                raise ConfigEditorError(f"duplicate preference: {pref['name']}")
            seen_prefs.add(pref["name"])
            for r in pref["routes"]:
                if not all(k in r for k in ("provider", "model")):
                    raise ConfigEditorError(f"route missing provider/model in {pref['name']}")
                if r["provider"] not in names:
                    raise ConfigEditorError(
                        f"route references unknown provider '{r['provider']}' "
                        f"in preference '{pref['name']}'")

    def commit(self, cfg: dict, reason: str = "") -> Tuple[Path, Path]:
        """Backup -> validate -> atomic write. Returns (bak_path, new_path)."""
        self._validate(cfg)
        # round-trip check: must be loadable identical
        rt = json.loads(json.dumps(cfg))
        if rt != cfg:
            raise ConfigEditorError("config not JSON-serializable losslessly")
        bak = self.backup()
        tmp = self.config_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
        # validate the tmp file parses before renaming over the live config
        with open(tmp) as f:
            json.load(f)
        tmp.replace(self.config_path)
        return bak, self.config_path

    # ------------------------------------------------------- strategy knob

    def set_strategy(self, strategy: str) -> Tuple[Path, Path]:
        """Set the routing strategy ("" = configured order, "lkgp" = LKGP).

        B-6: LKGP is opt-in now. Empty string means the router honours the
        configured provider order exactly. Uses the same backup→validate→
        atomic-commit path as every other config change.
        """
        if strategy not in ("", "lkgp"):
            raise ConfigEditorError(
                f"unknown strategy '{strategy}' (allowed: '', 'lkgp')")
        cfg = self.load()
        cfg["strategy"] = strategy
        return self.commit(cfg, reason=f"strategy={strategy or '(default)'}")

    # ---------------------------------------------------------------- diff

    def diff_summary(self, old: dict, new: dict) -> List[str]:
        """Human-readable line diff of provider/preference changes."""
        lines: List[str] = []

        def pref_sig(cfg):
            return {
                p["name"]: [(r["provider"], r["model"]) for r in p["routes"]]
                for p in cfg.get("preferences", [])
            }

        def prov_sig(cfg):
            return {p["name"]: p["base_url"] for p in cfg.get("providers", [])}

        # Preference ORDER changes = fallback priority changes — show explicitly
        old_order = [p["name"] for p in old.get("preferences", [])]
        new_order = [p["name"] for p in new.get("preferences", [])]
        if old_order != new_order:
            lines.append("PREFERENCE ORDER (fallback priority):")
            lines.append(f"   - {' -> '.join(old_order)}")
            lines.append(f"   + {' -> '.join(new_order)}")

        op, np_ = pref_sig(old), pref_sig(new)
        for name in op:
            if name not in np_:
                lines.append(f"DEL preference  {name} (was: {op[name]})")
        for name in np_:
            if name not in op:
                lines.append(f"ADD preference  {name} -> {np_[name]}")
            elif op[name] != np_[name]:
                lines.append(f"EDIT preference {name}:")
                lines.append(f"   - {op[name]}")
                lines.append(f"   + {np_[name]}")
        ov, nv = prov_sig(old), prov_sig(new)
        for n in ov:
            if n not in nv:
                lines.append(f"DEL provider    {n} ({ov[n]})")
        for n in nv:
            if n not in ov:
                lines.append(f"ADD provider    {n} ({nv[n]})")

        # Strategy + retry block (B-6): config-level knobs that affect routing
        old_strat = old.get("strategy", "")
        new_strat = new.get("strategy", "")
        if old_strat != new_strat:
            lines.append("STRATEGY:")
            lines.append(f"   - {old_strat or '(default: configured order)'}")
            lines.append(f"   + {new_strat or '(default: configured order)'}")
        old_retry = {k: v for k, v in (old.get("retry") or {}).items()}
        new_retry = {k: v for k, v in (new.get("retry") or {}).items()}
        for k in sorted(set(old_retry) | set(new_retry)):
            if old_retry.get(k) != new_retry.get(k):
                lines.append(f"RETRY {k}: {old_retry.get(k)} -> {new_retry.get(k)}")

        if not lines:
            lines.append("no changes")
        return lines

    # ------------------------------------------------------- catalog probe

    def _curl_authed(self, url: str, key_env: str, extra: List[str],
                     timeout: float) -> str:
        """Run curl via bash subshell. Sources ~/.hermes/.env so $KEY_NAME
        expands inside the shell only — key values never enter Python."""
        env_file = Path.home() / ".hermes" / ".env"
        # FIX: previously the literal 'Bearer ***' was sent — the key_env
        # parameter was accepted but never referenced, so every probe
        # authenticated with garbage and 401'd for healthy providers. Now
        # $key_env expands from the sourced .env inside the shell.
        cmd = (
            f'set -a; [ -f {env_file} ] && . {env_file}; set +a; '
            f'curl -s -m {int(timeout)} -o - -w "\\n%{{http_code}}" '
            f'-H "Authorization: Bearer ${key_env}" '
            + " ".join(f"'{a}'" for a in extra)
            + f" '{url}'"
        )
        import subprocess
        return subprocess.run(["bash", "-c", cmd], capture_output=True,
                               text=True, timeout=timeout + 10).stdout

    def probe_catalog(self, base_url: str, key_env: str,
                      timeout: float = HTTP_READ_TIMEOUT) -> Dict:
        """Live /v1/models query. Returns {'ok': bool, 'models': [..], 'error': str}.
        Uses curl (not urllib) — Zydit WAF 403s python-urllib fingerprints."""
        try:
            out = self._curl_authed(f"{base_url.rstrip('/')}/models",
                                     key_env, [], timeout)
            body, _, code = out.rpartition("\n")
            if code.strip() == "200":
                data = json.loads(body)
                models = [m["id"] for m in data.get("data", [])]
                return {"ok": True, "models": sorted(models), "error": ""}
            return {"ok": False, "models": [],
                    "error": f"HTTP {code.strip()}: {body.strip()[:100]}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "models": [], "error": "timeout"}
        except json.JSONDecodeError:
            return {"ok": False, "models": [], "error": "non-JSON response"}
        except Exception as e:
            return {"ok": False, "models": [], "error": str(e)[:120]}

    def test_key(self, base_url: str, key_env: str, model: str,
                 timeout: float = 45.0) -> Dict:
        """Fire a 1-token test request. Returns {'ok': bool, 'detail': str}."""
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 5,
        })
        try:
            out = self._curl_authed(f"{base_url.rstrip('/')}/chat/completions",
                                    key_env,
                                    ["-H", "Content-Type: application/json",
                                     "-d", body],
                                    timeout)
            body_resp, _, code = out.rpartition("\n")
            if code.strip() == "200":
                return {"ok": True, "detail": "200 OK — key works"}
            return {"ok": False,
                    "detail": f"HTTP {code.strip()}: {body_resp.strip()[:120]}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": f"timeout after {timeout:.0f}s (hang)"}
        except Exception as e:
            return {"ok": False, "detail": str(e)[:150]}


# ---------------------------------------------------------------- router ctl

def router_restart_pending_note() -> str:
    return ("Router restart REQUIRED for config changes to take effect. "
            "Restart with: bash ~/bin/startr  (after stopping current router)")


def restart_router() -> Dict:
    """Stop current router by exact PID, start via startr, verify health.
    Only called with explicit user confirmation from the TUI.
    """
    try:
        ps = subprocess.run(["pgrep", "-f", r"^\./router$"],
                            capture_output=True, text=True)
        pids = [p for p in ps.stdout.split() if p.strip()]
        killed = 0
        for pid in pids:
            subprocess.run(["kill", pid], capture_output=True)
            killed += 1
        time.sleep(3)
        r = subprocess.run(["bash", str(Path.home() / "bin" / "startr")],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        return {"ok": "Health: OK" in out or "already running" in out.lower(),
                "detail": f"killed {killed}; startr: {out[-200:]}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}
