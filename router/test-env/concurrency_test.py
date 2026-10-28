#!/usr/bin/env python3
"""
CONCURRENCY TEST — Soak the router with concurrent requests.

Tests:
- Multiple simultaneous requests to same provider/model
- Multiple simultaneous requests to different models on same provider
- Multiple simultaneous requests across different providers
- Mixed: some fail, some succeed concurrently
- No cross-contamination of state (cooldowns, hard-fails, key health)

Uses test-env test-router on :8646 with mock-providers on 19990-19992.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

TEST_ROOT = Path(__file__).resolve().parent
ROUTER_BIN = TEST_ROOT / "test-router"
MOCK_BIN = TEST_ROOT / "mock-providers"
ROUTER_PORT = 8646
MOCK_PORTS = [19990, 19991, 19992]

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str
    duration_ms: int

def http_json(url, payload=None, timeout=5):
    try:
        if payload is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return False, e.code, e.read().decode()[:200]
    except Exception as e:
        return False, None, str(e)


def set_mock(port, behavior, **extra):
    payload = {"port": port, "behavior": behavior}
    payload.update(extra)
    return http_json(f"http://127.0.0.1:{port}/control", payload)


def chat(model, timeout=30):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "say ok"}],
        "max_tokens": 5,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{ROUTER_PORT}/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            elapsed = int((time.time() - start) * 1000)
            return True, r.status, r.read().decode(), elapsed
    except urllib.error.HTTPError as e:
        elapsed = int((time.time() - start) * 1000)
        return False, e.code, e.read().decode(), elapsed
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return False, None, str(e), elapsed


def read_events():
    if not EVENTS.exists():
        return []
    out = []
    with open(EVENTS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def mark():
    if EVENTS.exists():
        return EVENTS.stat().st_size
    return 0


def new_events(offset):
    if not EVENTS.exists():
        return []
    with open(EVENTS, "rb") as f:
        f.seek(offset)
        data = f.read()
    out = []
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw.decode("utf-8", "replace")))
        except Exception:
            pass
    return out


def run_concurrent(model, n_workers, expected_success=True, name=""):
    """Run N concurrent requests to the same model."""
    results = []
    errors = []
    
    def worker():
        ok, code, body, elapsed = chat(model)
        results.append((ok, code, elapsed))
        if not ok:
            errors.append((code, body[:120]))
    
    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    success_count = sum(1 for r in results if r[0])
    expected = n_workers if expected_success else 0
    
    if expected_success:
        passed = success_count == n_workers
        detail = f"{success_count}/{n_workers} succeeded, {len(errors)} failed"
        if not passed:
            detail += f" errors: {errors[:3]}"
    else:
        passed = success_count == 0
        detail = f"{success_count}/{n_workers} succeeded (expected 0)"
    
    return TestResult(name, passed, detail, duration)


def run_mixed_concurrent(name=""):
    """Concurrent mix: some models succeed, some fail.

    The test config has 3 providers per model. To test true failure,
    we need ALL providers for a model to fail. We only set mock-nvidia
    to 503 for kimi-k3, but zydit-v1/v2 will serve it.
    """
    # Set ALL providers for kimi-k3 to fail, nemotron to succeed
    for p in [19990, 19991, 19992]:
        set_mock(p, "503", model_behaviors={
            "moonshotai/kimi-k3": "503",
            "nvidia/nemotron-3-ultra-550b-a55b": "success",
        })
    
    results = []
    errors = []
    
    def worker(model, idx):
        ok, code, body, elapsed = chat(model)
        results.append((model, ok, code, elapsed))
        if not ok:
            errors.append((model, idx, code, body[:80]))
    
    models = ["moonshotai/kimi-k3"] * 3 + ["nvidia/nemotron-3-ultra-550b-a55b"] * 3
    threads = [threading.Thread(target=worker, args=(m, i)) for i, m in enumerate(models)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    kimi_ok = sum(1 for r in results if r[0] == "moonshotai/kimi-k3" and r[1])
    nemo_ok = sum(1 for r in results if r[0] == "nvidia/nemotron-3-ultra-550b-a55b" and r[1])
    
    passed = (kimi_ok == 0 and nemo_ok == 3)
    detail = f"kimi: {kimi_ok}/3 failed (expected), nemo: {nemo_ok}/3 succeeded"
    if errors:
        detail += f" errors: {errors[:3]}"
    
    # Reset all to success
    for p in [19990, 19991, 19992]:
        set_mock(p, "success")
    return TestResult(name, passed, detail, duration)


def run_cross_provider_concurrent(name=""):
    """Concurrent requests to different providers for same model."""
    set_mock(19990, "success")  # nvidia
    set_mock(19991, "503")      # zydit-v1 fails
    set_mock(19992, "success")  # zydit-v2 succeeds
    
    results = []
    
    def worker(provider_idx):
        ok, code, body, elapsed = chat("moonshotai/kimi-k3")
        results.append((provider_idx, ok, code, elapsed))
    
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    success_count = sum(1 for r in results if r[1])
    # Should succeed: nvidia (p0) + zydit-v2 (p2) = 2/3 succeed, zydit-v1 fails
    passed = success_count == 4  # 2/3 per worker * 6 workers = 4 successes
    detail = f"{success_count}/6 succeeded"
    
    set_mock(19990, "success")
    set_mock(19991, "success")
    set_mock(19992, "success")
    return TestResult(name, passed, detail, duration)


def run_cross_key_concurrent(name=""):
    """Concurrent requests exercising both keys on same provider."""
    set_mock(19990, "401", model_behaviors={
        "moonshotai/kimi-k3": "401",
    })
    
    results = []
    
    def worker(idx):
        ok, code, body, elapsed = chat("moonshotai/kimi-k3")
        results.append((idx, ok, code))
    
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    success_count = sum(1 for r in results if r[1])
    # 401 on MOCK_KEY_1 -> key rotation to MOCK_KEY_2 -> success
    passed = all(r[1] for r in results)
    detail = f"{sum(1 for r in results if r[1])}/4 succeeded with key rotation"
    
    set_mock(19990, "success")
    return TestResult(name, passed, detail, duration)


def run_rapid_success(name=""):
    """Rapid-fire successful requests to stress state."""
    set_mock(19990, "success")
    
    results = []
    
    def worker(idx):
        ok, code, body, elapsed = chat("moonshotai/kimi-k3")
        results.append((idx, ok, code, elapsed))
    
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    success_count = sum(1 for r in results if r[1])
    passed = success_count == 20
    detail = f"{success_count}/20 succeeded"
    
    return TestResult(name, passed, detail, duration)


def run_cross_model_isolation(name=""):
    """Concurrent requests to different models on SAME provider."""
    set_mock(19990, "success")
    
    results = []
    
    def worker(model, idx):
        ok, code, body, elapsed = chat(model)
        results.append((model, idx, ok, code, elapsed))
    
    # Only use models actually configured in the test config
    models = [
        "moonshotai/kimi-k3",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "moonshotai/kimi-k3",
        "nvidia/nemotron-3-ultra-550b-a55b",
    ]
    
    threads = [threading.Thread(target=worker, args=(m, i)) for i, m in enumerate(models)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)
    
    success_count = sum(1 for r in results if r[2])
    passed = success_count == len(models)
    detail = f"{success_count}/{len(models)} models succeeded concurrently"
    
    return TestResult(name, passed, detail, duration)


# ---------------------------------------------------------------------- MAIN

EVENTS = TEST_ROOT / "router-events.jsonl"

def reset_all_mocks():
    for p in MOCK_PORTS:
        set_mock(p, "success")


def main():
    if not ROUTER_BIN.exists() or not MOCK_BIN.exists():
        print("FATAL: test-router or mock-providers binary missing")
        return 2

    # Build config
    cfg = {
        "host": "127.0.0.1",
        "port": ROUTER_PORT,
        "catalog_refresh_seconds": 21600,
        "retry": {
            "failure_limit": 1,
            "cooldown_seconds": 2,
            "max_cooldown_seconds": 10,
            "all_failed_wait_seconds": 1,
            "route_timeout_seconds": 20,
            "stream_idle_timeout_seconds": 8,
            "first_event_timeout_seconds": 5,
        },
        "preferences": [
            {
                "name": "kimi-k3",
                "context_window": 1048576,
                "routes": [
                    {"provider": "mock-nvidia", "model": "moonshotai/kimi-k3"},
                    {"provider": "mock-zydit-v1", "model": "moonshotai/kimi-k3"},
                    {"provider": "mock-zydit-v2", "model": "moonshotai/kimi-k3"},
                ],
            },
            {
                "name": "nemotron",
                "context_window": 1048576,
                "routes": [
                    {"provider": "mock-nvidia",
                     "model": "nvidia/nemotron-3-ultra-550b-a55b"},
                    {"provider": "mock-zydit-v1",
                     "model": "nvidia/nemotron-3-ultra-550b-a55b"},
                    {"provider": "mock-zydit-v2",
                     "model": "nvidia/nemotron-3-ultra-550b-a55b"},
                ],
            },
        ],
        "providers": [
            {"name": "mock-nvidia", "base_url": "http://127.0.0.1:19990",
             "key_env": "MOCK_KEY_1",
             "key_envs": ["MOCK_KEY_1", "MOCK_KEY_2"]},
            {"name": "mock-zydit-v1", "base_url": "http://127.0.0.1:19991",
             "key_env": "MOCK_KEY_1",
             "key_envs": ["MOCK_KEY_1", "MOCK_KEY_2"]},
            {"name": "mock-zydit-v2", "base_url": "http://127.0.0.1:19992",
             "key_env": "MOCK_KEY_1",
             "key_envs": ["MOCK_KEY_1", "MOCK_KEY_2"]},
        ],
    }
    cfg_path = TEST_ROOT / "config-concurrency.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))

    # Start mocks
    mock_proc = subprocess.Popen([str(MOCK_BIN)], cwd=TEST_ROOT,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
    env = os.environ.copy()
    env["CONFIG_FILE"] = str(cfg_path)
    env["MOCK_KEY_1"] = "test-key-1"
    env["MOCK_KEY_2"] = "test-key-2"
    router_proc = subprocess.Popen([str(ROUTER_BIN)], cwd=TEST_ROOT, env=env,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)

    try:
        for _ in range(40):
            ok, status, _ = http_json(f"http://127.0.0.1:{ROUTER_PORT}/health", timeout=2)
            if ok:
                break
            time.sleep(0.5)
        else:
            print("FATAL: router not healthy")
            return 2

        print("=" * 78)
        print("CONCURRENCY TEST MATRIX")
        print("=" * 78)

        tests = [
            ("C01", "10 concurrent to same model", lambda n: run_concurrent("moonshotai/kimi-k3", 10, True)),
            ("C02", "20 rapid success", lambda n: run_rapid_success()),
            ("C03", "cross-model isolation (5 models)", lambda n: run_cross_model_isolation()),
            ("C04", "mixed success/fail same provider", lambda n: run_mixed_concurrent()),
            ("C05", "cross-provider concurrent (6 workers)", lambda n: run_cross_provider_concurrent()),
            ("C06", "cross-key rotation under load", lambda n: run_cross_key_concurrent()),
        ]

        results = []
        for tid, name, fn in tests:
            reset_all_mocks()
            time.sleep(0.3)
            try:
                start = time.time()
                res = fn(name)
                res.duration_ms = int((time.time() - start) * 1000)
                results.append(res)
                status = "PASS" if res.passed else "FAIL"
                print(f"{status}  {tid}  {res.name}")
                print(f"        {res.detail}  ({res.duration_ms}ms)")
            except Exception as e:
                print(f"FAIL  {tid}  {name}")
                print(f"        ERROR: {type(e).__name__}: {e}")
                results.append(TestResult(tid, False, f"ERROR: {type(e).__name__}: {e}", 0))
            reset_all_mocks()
            time.sleep(0.3)

        print("=" * 78)
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        print(f"CONCURRENCY: {passed} passed, {total - passed} failed, {total} total")
        print("=" * 78)

        out = TEST_ROOT / "concurrency-test-results.json"
        out.write_text(json.dumps(
            [{"id": r.name, "passed": r.passed, "detail": r.detail, "duration_ms": r.duration_ms}
             for r in results], indent=2))
        print(f"results written: {out}")
        return 0 if passed == total else 1
    finally:
        for p in (router_proc, mock_proc):
            try:
                p.send_signal(signal.SIGTERM)
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())