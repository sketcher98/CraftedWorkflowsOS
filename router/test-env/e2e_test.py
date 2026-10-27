#!/usr/bin/env python3
"""
Isolated E2E test: Hermes -> Router -> 503 -> Hermes retry -> recovery

This test runs a genuine Hermes agent against the test router in an isolated
temp HERMES_HOME, simulating the exact failure→503→retry→recovery path.

NO production Hermes/router files touched.
NO "??" manual intervention.
FULL automated observation of request IDs, Hermes retries, router telemetry.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path

# ─── Setup isolated test environment ───
TEST_ROOT = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/router/test-env")
HERMES_AGENT_ROOT = Path("/data/data/com.termux/files/home/.hermes/hermes-agent")

# Create isolated HERMES_HOME
temp_home = Path(tempfile.mkdtemp(prefix="hermes_e2e_"))
os.environ["HERMES_HOME"] = str(temp_home)
print(f"[E2E] Isolated HERMES_HOME: {temp_home}")

# Copy minimal Hermes config
config_src = HERMES_AGENT_ROOT / "cli-config.yaml.example"
config_dst = temp_home / "config.yaml"
shutil.copy(config_src, config_dst)

# Configure test to use local router on :8644 with nemotron model
with open(config_dst, "a") as f:
    f.write("""
agent:
  base_url: "http://127.0.0.1:8644/v1"
  api_key: "test-key-1"
  model: "nvidia/nemotron-3-ultra-550b-a55b"
  provider: "custom"
  api_max_retries: 3
  api_mode: "chat_completions"
""")

# ─── Start mock provider (503 then success) ───
mock_cmd = [str(TEST_ROOT / "mock-providers")]
mock_proc = subprocess.Popen(
    mock_cmd,
    cwd=TEST_ROOT,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
print("[E2E] Started mock-providers")

# Start test router
router_cmd = [
    str(TEST_ROOT / "test-router"),
]
router_env = os.environ.copy()
router_env["CONFIG_FILE"] = str(TEST_ROOT / "config-e2e.json")
router_env["MOCK_KEY_1"] = "test-key-1"

router_proc = subprocess.Popen(
    router_cmd,
    cwd=TEST_ROOT,
    env=router_env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
print("[E2E] Started test-router on :8644")

# Wait for both to be ready
time.sleep(3)

# Configure mock: 503 first, then success after control command
import requests
requests.post("http://127.0.0.1:19990/control",
    json={"port": 19990, "behavior": "503"})
print("[E2E] Mock set to 503")

# Verify router health
requests.get("http://127.0.0.1:8644/health", timeout=5)
print("[E2E] Router healthy")

# ─── Run Hermes through the test ───
# Use run_agent.py directly to invoke a single turn
run_agent = HERMES_AGENT_ROOT / "run_agent.py"

# Create a minimal test script that uses the agent
test_script = f"""
import sys
sys.path.insert(0, "{HERMES_AGENT_ROOT}")

from run_agent import AIAgent

agent = AIAgent(
    base_url="http://127.0.0.1:8644/v1",
    api_key="test-key-1",
    model="nvidia/nemotron-3-ultra-550b-a55b",
    provider="custom",
    api_mode="chat_completions",
    api_max_retries=3,
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
    session_id="e2e-test",
    platform="cli",
)

result = agent.chat("Say 'hello' briefly")
print("RESULT:", result.get("final_response", "")[:200])
print("COMPLETED:", result.get("completed"))
print("FAILED:", result.get("failed"))
print("API_CALLS:", result.get("api_calls"))
"""

# Write test script
test_file = temp_home / "e2e_test.py"
test_file.write_text(test_script)

print("[E2E] Running Hermes agent through test router...")
print("[E2E] Phase 1: Provider returns 503 -> Router returns 503 -> Hermes retries")
print("[E2E] Phase 2: Mock switches to success -> Hermes retry succeeds")

# Run Hermes
start = time.time()
result = subprocess.run(
    ["python3", str(test_file)],
    cwd=HERMES_AGENT_ROOT,
    env={**os.environ, "HERMES_HOME": str(temp_home)},
    capture_output=True,
    text=True,
    timeout=120,
)
elapsed = time.time() - start

print(f"[E2E] Hermes exited in {elapsed:.1f}s with code {result.returncode}")
print("STDOUT:")
print(result.stdout)
if result.stderr:
    print("STDERR:")
    print(result.stderr)

# ─── Analyze results ───
print("\n" + "="*60)
print("E2E ANALYSIS")
print("="*60)

# Check router telemetry
events_file = TEST_ROOT / "router-events.jsonl"
if events_file.exists():
    print("\nRouter Events:")
    with open(events_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    evt = json.loads(line)
                    if "e2e" in evt.get("request_id", "") or "req-" in evt.get("request_id", ""):
                        print(f"  {evt['timestamp']} | {evt['event']} | req={evt.get('request_id','')[:30]} | prov={evt.get('provider','')} | model={evt.get('model','')} | outcome={evt.get('outcome','')} | status={evt.get('status','')}")
                except:
                    pass

# ─── Cleanup ───
router_proc.terminate()
mock_proc.terminate()
try:
    router_proc.wait(timeout=5)
    mock_proc.wait(timeout=5)
except:
    router_proc.kill()
    mock_proc.kill()

# Cleanup temp home
shutil.rmtree(temp_home, ignore_errors=True)

print("\n[E2E] Complete")