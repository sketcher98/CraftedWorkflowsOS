"""Harness registry: known AI clients, detection, config writers.

The wizard detects which harnesses exist on this machine, lets the user
pick ANY subset (one, two, or all), and writes the router connection into
each selected harness's own config file.

Writers NEVER edit an existing file destructively: any target file is
backed up with a timestamped suffix before modification, and the wizard
reports exactly which files it touched.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()


@dataclass
class Harness:
    id: str
    display: str
    # Paths to check (in order) to decide whether the harness is installed.
    detect_paths: list[str] = field(default_factory=list)
    # None = pure detection (config writing handled inline if needed)
    config_hint: str = ""

    def is_installed(self) -> bool:
        return any(Path(p).exists() for p in self.detect_paths)


# ---------------------------------------------------------------------------
# Detection paths (best-effort defaults; users can point us elsewhere)
# ---------------------------------------------------------------------------
HARNESSES: list[Harness] = [
    Harness("hermes", "Hermes Agent",
            detect_paths=[str(HOME / ".hermes" / "config.yaml")],
            config_hint="~/.hermes/config.yaml (model.default + base_url)"),
    Harness("codex", "Codex CLI (OpenAI)",
            detect_paths=[str(HOME / ".codex" / "config.toml")],
            config_hint="~/.codex/config.toml (model_providers)"),
    Harness("opencode", "OpenCode",
            detect_paths=[str(HOME / ".config" / "opencode" / "opencode.json"),
                          str(HOME / ".opencode" / "opencode.json")],
            config_hint="opencode.json (provider entry)"),
    Harness("openclaw", "OpenClaw",
            detect_paths=[str(HOME / ".openclaw" / "config.json")],
            config_hint="~/.openclaw/config.json"),
    Harness("pi", "Pi (Inflection CLI)",
            detect_paths=[str(HOME / ".pi" / "config.json"),
                          str(HOME / ".config" / "pi" / "config.json")],
            config_hint="~/.pi/config.json"),
    Harness("vscode", "Visual Studio Code / GitHub Copilot",
            detect_paths=[str(HOME / ".vscode" / "settings.json"),
                          str(HOME / ".config" / "Code" / "User" / "settings.json")],
            config_hint="settings.json (custom endpoints)"),
]


def all_harnesses() -> list[Harness]:
    return list(HARNESSES)


def detect_installed(home: Path = HOME) -> list[Harness]:
    out = []
    for h in HARNESSES:
        paths = [str(home / p.removeprefix(str(HOME) + os.sep))
                 if p.startswith(str(HOME)) else p for p in h.detect_paths]
        if any(Path(p).exists() for p in paths):
            out.append(h)
    return out


def back_up_file(path: Path) -> Path | None:
    """Timestamped backup; returns the backup path or None if absent."""
    if not path.exists():
        return None
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{ts}")
    shutil.copy2(path, bak)
    return bak


def _require_parents(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Writers (one per harness). Each returns a short human summary.
# All writers operate under an explicit home root (tests use a temp root).
# ---------------------------------------------------------------------------
def write_hermes(cfg: dict, summary: list[str], home: Path = HOME) -> None:
    """hermes config.yaml: set model.default + base_url for the first preference."""
    model = cfg.get("model") or "router-default"
    path = home / ".hermes" / "config.yaml"
    bak = back_up_file(path)
    if bak:
        summary.append(f"  hermes: backed up existing config.yaml -> {bak.name}")
    _require_parents(path)
    # Minimal, safe YAML block (profiles keep their own; main config only).
    block = (
        "\n# --- router (added by router setup wizard) ---\n"
        f"model:\n  default: {model}\n  provider: custom\n"
        "  base_url: http://127.0.0.1:8643/v1\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    summary.append(f"  hermes: appended router block to {path}")


def write_codex(cfg: dict, summary: list[str], home: Path = HOME) -> None:
    path = home / ".codex" / "config.toml"
    bak = back_up_file(path)
    if bak:
        summary.append(f"  codex: backed up existing config.toml -> {bak.name}")
    _require_parents(path)
    block = (
        "\n# --- router (added by router setup wizard) ---\n"
        "[model_providers.router]\n"
        'name = "Router"\n'
        "base_url = \"http://127.0.0.1:8643/v1\"\n"
        'wire_api = "chat_completions"\n'
        'env_key = "ROUTER_API_KEY"\n'
        'request_max_retries = 2\n'
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    summary.append(f"  codex: appended provider block to {path}")


def write_opencode(cfg: dict, summary: list[str], home: Path = HOME) -> None:
    path = home / ".config" / "opencode" / "opencode.json"
    bak = back_up_file(path)
    if bak:
        summary.append(f"  opencode: backed up existing opencode.json -> {bak.name}")
    _require_parents(path)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    providers = data.setdefault("provider", {})
    providers["router"] = {
        "npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL": "http://127.0.0.1:8643/v1"},
        "models": {"router-default": {"name": "Router default"}},
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    summary.append(f"  opencode: added router provider to {path}")


def _placeholder_writer(harness_id: str):
    def write(cfg: dict, summary: list[str], home: Path = HOME) -> None:
        summary.append(f"  {harness_id}: config writer pending (detected, not yet wired)")
    return write


WRITERS = {
    "hermes": write_hermes,
    "codex": write_codex,
    "opencode": write_opencode,
    "openclaw": _placeholder_writer("openclaw"),
    "pi": _placeholder_writer("pi"),
    "vscode": _placeholder_writer("vscode"),
}


def write_for(harness_id: str, cfg: dict, summary: list[str],
              home: Path = HOME) -> None:
    WRITERS.get(harness_id, _placeholder_writer(harness_id))(cfg, summary, home)
