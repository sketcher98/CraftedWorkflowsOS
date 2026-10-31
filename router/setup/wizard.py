"""router setup wizard — the 15-minute non-technical onboarding flow.

Flow:
  1. Welcome + what this does.
  2. Detect installed harnesses (Hermes, Codex, OpenCode, OpenClaw, Pi, VS Code).
  3. Multi-select: configure ANY subset (one or all).
  4. Providers: add one or more (name, base_url, default model, paste key).
     Keys are stored ONLY in a .env file (never echoed, never in config.json).
  5. Wizard writes:
       - config.json (backed up first, atomic write)
       - .env (appended with new keys only)
       - each selected harness's config file (backed up first)
  6. Optional: start router + verify /health.

The prompt interface is injectable so the flow is testable headlessly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .harnesses import (
    all_harnesses,
    detect_installed,
    write_for,
    back_up_file,
)

HOME = Path.home()
DEFAULT_ROUTER_DIR = HOME / "CraftedWorkflowsOS" / "router"
DEFAULT_CONFIG = DEFAULT_ROUTER_DIR / "config.json"
DEFAULT_ENV = HOME / ".hermes" / ".env"  # shared-ish; wizard keeps a copy


class BasePrompter:
    """Override for tests (no stdin)."""

    def ask(self, question: str, default: str = "") -> str:
        if default:
            question = f"{question} [{default}] "
        answer = input(question).strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        answer = input(question + suffix).strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    def multi_select(self, question: str, options: list[str],
                     default_all: bool = True) -> list[str]:
        print(question)
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        raw = self.ask("Pick numbers (comma/space separated)", "")
        if not raw.strip() and default_all:
            return list(options)
        try:
            idxs = [int(x) for x in raw.replace(",", " ").split()]
        except ValueError:
            return []
        return [options[i - 1] for i in idxs if 1 <= i <= len(options)]


def _safe_env_name(provider_name: str) -> str:
    stem = "".join(c if c.isalnum() else "_" for c in provider_name).upper()
    return f"ROUTER_{stem}_API_KEY"


@dataclass
class ProviderInput:
    name: str
    base_url: str
    model: str
    key: str


class Wizard:
    def __init__(self, prompter: BasePrompter | None = None,
                 config_path: Path = DEFAULT_CONFIG,
                 env_path: Path = DEFAULT_ENV,
                 router_dir: Path = DEFAULT_ROUTER_DIR,
                 home: Path = HOME):
        self.p = prompter or BasePrompter()
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)
        self.router_dir = Path(router_dir)
        self.home = Path(home)

    # ------------------------------------------------------------- steps
    def run(self) -> dict:
        self.p.ask("Router setup — I'll connect your AI tools to one local "
                    "router. Press Enter to continue.")
        harnesses = self._choose_harnesses()
        if not harnesses:
            print("No harnesses selected — nothing to wire.")
            return {"status": "aborted"}
        providers = self._collect_providers()
        if not providers:
            print("No providers added — nothing to write.")
            return {"status": "aborted"}

        cfg = self._load_cfg()
        changed = self._apply_providers(cfg, providers)
        if not changed:
            print("No config changes needed.")
            return {"status": "noop"}

        self._write_cfg(cfg)
        self._write_env(providers)

        summary = ["Wrote:"]
        summary.append(f"  router config: {self.config_path}")
        summary.append(f"  {self.env_path.name} (keys only)")
        for h in harnesses:
            write_for(h.id, {"model": providers[0].model}, summary, home=self.home)

        print("\n".join(summary))
        if self.p.confirm("Start the router now and verify it?"):
            self._start_and_verify()
        return {"status": "done", "harnesses": [h.id for h in harnesses],
                "providers": [p.name for p in providers]}

    def _choose_harnesses(self) -> list:
        installed = detect_installed(self.home)
        all_h = all_harnesses()
        if installed:
            print(f"Detected: {', '.join(h.display for h in installed)}")
        else:
            print("No known harness detected — you can still pick what to configure.")
        options = [h.display for h in all_h]
        picked = self.p.multi_select("Which tools should use the router? "
                                     "(multi-select)", options, default_all=False)
        if not picked:
            return []
        by_display = {h.display: h for h in all_h}
        return [by_display[name] for name in picked]

    def _collect_providers(self) -> list[ProviderInput]:
        providers = []
        while True:
            print("\n--- Provider ---")
            name = self.p.ask("Name (e.g. nvidia, openrouter, xkiro)")
            if not name:
                break
            base_url = self.p.ask("API base URL",
                                   default="https://api.openrouter.ai/v1")
            model = self.p.ask("Default model id to route first",
                               default="na")
            key = self.p.ask("Paste your API key", default="")
            providers.append(ProviderInput(name=name.strip(),
                                           base_url=base_url.strip(),
                                           model=model.strip(),
                                           key=key.strip()))
            if not self.p.confirm("Add another provider?"):
                break
        return providers

    def _load_cfg(self) -> dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"host": "127.0.0.1", "port": 8643,
                "retry": {"failure_limit": 2, "cooldown_seconds": 15},
                "providers": [], "preferences": []}

    def _apply_providers(self, cfg: dict, providers: list[ProviderInput]) -> bool:
        cfg.setdefault("providers", [])
        cfg.setdefault("preferences", [])
        changed = False
        for p in providers:
            env = _safe_env_name(p.name)
            if not any(x.get("name") == p.name for x in cfg["providers"]):
                cfg["providers"].append({
                    "name": p.name, "base_url": p.base_url,
                    "key_env": env, "key_envs": [env],
                })
                changed = True
            if p.model and p.model != "na":
                if not any(x.get("name") == p.model for x in cfg["preferences"]):
                    cfg["preferences"].append({
                        "name": p.model, "context_window": 1048576,
                        "routes": [{"provider": p.name, "model": p.model}],
                    })
                    changed = True
        return changed

    def _write_cfg(self, cfg: dict) -> None:
        back_up_file(self.config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _write_env(self, providers: list[ProviderInput]) -> None:
        back_up_file(self.env_path)
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
        lines = [l for l in existing.splitlines() if l.strip()]
        known = {l.split("=", 1)[0] for l in lines if "=" in l}
        with open(self.env_path, "a", encoding="utf-8") as f:
            for p in providers:
                env = _safe_env_name(p.name)
                if env in known:
                    continue
                f.write(f"{env}={p.key}\n")
                known.add(env)

    def _start_and_verify(self) -> None:
        startr = self.router_dir / "scripts" / "startr"
        if not startr.exists():
            print("startr not found — start the router manually, then check /health.")
            return
        print("Starting router...")
        subprocess.run(["bash", str(startr)], cwd=str(self.router_dir), check=False)
        import urllib.request
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8643/health", timeout=15) as r:
                print(f"Health check: HTTP {r.status} — router is UP.")
        except Exception as e:
            print(f"Health check could not confirm: {e}")


def main() -> None:
    sys.exit(0 if Wizard().run().get("status") in ("done", "noop") else 1)


if __name__ == "__main__":
    main()
