import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setup.wizard import Wizard, BasePrompter
from setup.harnesses import all_harnesses


class FakePrompter(BasePrompter):
    def __init__(self, selects, provider_answers):
        self.selects = list(selects)
        self.provider_answers = list(provider_answers)
        self.confirms = []

    def multi_select(self, question, options, default_all=True):
        return [options[i - 1] for i in self.selects]

    def ask(self, question, default=""):
        if self.provider_answers:
            return self.provider_answers.pop(0)
        return "ok"

    def confirm(self, question, default=True):
        self.confirms.append(question)
        if "Start the router now" in question:
            return False
        # "Add another provider?" -> first False to stop the loop
        if "Add another provider" in question:
            return False
        return True


def test_multi_harness_flow(tmp_home, tmp_router):
    # 1) fake an installed hermes config so detection finds it
    (tmp_home / ".hermes").mkdir(parents=True)
    (tmp_home / ".hermes" / "config.yaml").write_text("model:\n  default: old\n")

    all_h = all_harnesses()
    # select hermes (1) + codex (2) + opencode (3)
    prompts = FakePrompter([1, 2, 3], [
        "ok",  # welcome prompt
        "nvidia", "https://api.nvidia.com/v1", "nvidia/nemotron-3-ultra-550b-a55b",
        "sk-TESTKEY123",
    ])
    w = Wizard(prompter=prompts, config_path=tmp_router / "config.json",
               env_path=tmp_home / ".env", router_dir=tmp_router,
               home=tmp_home)
    res = w.run()
    assert res["status"] == "done", res
    assert set(res["harnesses"]) == {"hermes", "codex", "opencode"}, res

    cfg = json.loads((tmp_router / "config.json").read_text())
    provs = cfg["providers"]
    assert len(provs) == 1 and provs[0]["name"] == "nvidia"
    assert provs[0]["key_envs"] == ["ROUTER_NVIDIA_API_KEY"]
    assert cfg["preferences"][0]["name"] == "nvidia/nemotron-3-ultra-550b-a55b"

    env = (tmp_home / ".env").read_text()
    assert "ROUTER_NVIDIA_API_KEY=sk-TESTKEY123" in env
    assert "sk-TESTKEY123" not in (tmp_router / "config.json").read_text()

    # harness configs written
    hermes = (tmp_home / ".hermes" / "config.yaml").read_text()
    assert "base_url: http://127.0.0.1:8643/v1" in hermes
    codex = (tmp_home / ".codex" / "config.toml").read_text()
    assert "[model_providers.router]" in codex
    opencode = json.loads((tmp_home / ".config" / "opencode" / "opencode.json").read_text())
    assert "router" in opencode["provider"]
    return "MULTI-HARNESS OK"


def test_all_harnesses_selectable(tmp_home, tmp_router):
    all_h = all_harnesses()
    prompts = FakePrompter(list(range(1, len(all_h) + 1)), [
        "p1", "https://x/v1", "m1", "k1",
    ])
    w = Wizard(prompter=prompts, config_path=tmp_router / "config.json",
               env_path=tmp_home / ".env", router_dir=tmp_router,
               home=tmp_home)
    res = w.run()
    assert res["status"] == "done"
    assert len(res["harnesses"]) == len(all_h)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        print(test_multi_harness_flow(root, root / "router"))
        print(test_all_harnesses_selectable(root, root / "router2"))
        print("ALL OK")


if __name__ == "__main__":
    main()
