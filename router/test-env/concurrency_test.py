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

KIMI_MODEL = "moonshotai/kimi-k3"
NEMOTRON_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

# The test config uses cooldown_seconds=1 / max_cooldown_seconds=2, so
# waiting this long after a failing test guarantees no leftover cooldown
# from the previous test can leak into the next one.
COOLDOWN_DRAIN_S = 3

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


def run_mixed_concurrent(tid=""):
    """C04: concurrent mix where one model's routes all fail, another succeeds.

    Planned outcome (router-level): every kimi-k3 route (mock-nvidia,
    mock-zydit-v1, mock-zydit-v2) returns 503 -> all 3 kimi requests fail
    3/3 within their own preference; nemotron succeeds 3/3. Because only two
    preferences exist, exhausted kimi requests are rescued by preference
    fallback kimi-k3 -> nemotron, so client HTTP status is 200 for all 6
    requests. Assert the PLANNED router-level outcomes from JSONL, not the
    client-visible success count (which would mis-read kimi as passing).
    """
    offset = mark()
    for p in MOCK_PORTS:
        set_mock(p, "503", model_behaviors={
            KIMI_MODEL: "503",
            NEMOTRON_MODEL: "success",
        })

    results = []
    errors = []

    def worker(model, idx):
        ok, code, body, elapsed = chat(model)
        results.append((model, ok, code, elapsed))
        if not ok:
            errors.append((model, idx, code, body[:80]))

    models = [KIMI_MODEL] * 3 + [NEMOTRON_MODEL] * 3
    threads = [threading.Thread(target=worker, args=(m, i)) for i, m in enumerate(models)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)

    nemo_ok = sum(1 for r in results if r[0] == NEMOTRON_MODEL and r[1])

    events = new_events(offset)
    kimi_transient = [e for e in events
                      if e.get("event") == "route_result"
                      and e.get("preference") == "kimi-k3"
                      and e.get("outcome") == "transient"]
    kimi_success = [e for e in events
                    if e.get("event") == "route_result"
                    and e.get("preference") == "kimi-k3"
                    and e.get("outcome") == "success"]
    kimi_fb = [e for e in events
               if e.get("event") == "preference_fallback"
               and e.get("from_preference") == "kimi-k3"]

    # Planned outcome: kimi routes fail 3/3 at router level, nemotron 3/3.
    passed = (nemo_ok == 3 and len(kimi_success) == 0
              and len(kimi_transient) >= 3 and len(kimi_fb) == 3)
    detail = (f"kimi routes {len(kimi_transient)} transient/3 failed (router-level), "
              f"nemotron {nemo_ok}/3 succeeded, "
              f"{len(kimi_fb)} preference fallbacks kimi->nemotron")
    if errors:
        detail += f" errors: {errors[:3]}"

    for p in MOCK_PORTS:
        set_mock(p, "success")
    return TestResult(tid, passed, detail, duration)


def run_cross_provider_concurrent(tid=""):
    """C05: concurrent requests across providers for the same model, with one
    provider down (mock-zydit-v1 = 503).

    Planned outcome: all 6 kimi-k3 requests succeed via healthy providers
    (mock-nvidia is first route; mock-zydit-v2 is fallback). The previous
    version expected 4/6 and ran while C04's cooldowns were still active,
    so every route was skipped as unhealthy and all 6 exhausted instantly.
    """
    offset = mark()
    set_mock(19990, "success")  # nvidia
    set_mock(19991, "503")      # zydit-v1 fails
    set_mock(19992, "success")  # zydit-v2 succeeds

    results = []

    def worker(provider_idx):
        ok, code, body, elapsed = chat(KIMI_MODEL)
        results.append((provider_idx, ok, code, elapsed))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)

    success_count = sum(1 for r in results if r[1])
    events = new_events(offset)
    exhausted = [e for e in events if e.get("event") == "request_exhausted"]
    passed = success_count == 6 and not exhausted
    detail = f"{success_count}/6 succeeded (zydit-v1=503, nvidia/zydit-v2 healthy)"

    set_mock(19990, "success")
    set_mock(19991, "success")
    set_mock(19992, "success")
    return TestResult(tid, passed, detail, duration)


def run_cross_key_concurrent(tid=""):
    """C06: concurrent requests exercising multi-key rotation on the SAME provider.

    Mock behavior 401_key_rotation rejects only `Bearer test-key-1`
    (MOCK_KEY_1); `Bearer test-key-2` (MOCK_KEY_2) succeeds. Planned outcome:
    each request's first key gets 401 -> router rotates to key2 -> success.
    All 4 succeed and telemetry shows nvidia commits with key_env=MOCK_KEY_2.
    """
    offset = mark()
    set_mock(19990, "401_key_rotation", model_behaviors={
        KIMI_MODEL: "401_key_rotation",
    })

    results = []

    def worker(idx):
        ok, code, body, elapsed = chat(KIMI_MODEL)
        results.append((idx, ok, code))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = int((time.time() - start) * 1000)

    success_count = sum(1 for r in results if r[1])
    events = new_events(offset)
    nvidia_results = [e for e in events
                      if e.get("event") == "route_result"
                      and e.get("provider") == "mock-nvidia"]
    rotated_ok = [e for e in nvidia_results
                  if e.get("outcome") == "success"
                  and e.get("key_env") == "MOCK_KEY_2"]
    exhausted = [e for e in events if e.get("event") == "request_exhausted"]
    passed = (success_count == 4 and len(rotated_ok) == 4 and not exhausted)
    detail = (f"{success_count}/4 succeeded with key rotation "
              f"(key_env=MOCK_KEY_2 on {len(rotated_ok)}/4 nvidia commits)")

    set_mock(19990, "success")
    return TestResult(tid, passed, detail, duration)


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
            "cooldown_seconds": 1,
            "max_cooldown_seconds": 2,
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
            ("C01", "10 concurrent to same model", lambda n: run_concurrent(KIMI_MODEL, 10, True, n)),
            ("C02", "20 rapid success", lambda n: run_rapid_success(n)),
            ("C03", "cross-model isolation", lambda n: run_cross_model_isolation(n)),
            ("C04", "mixed success/fail same provider", lambda n: run_mixed_concurrent(n)),
            ("C05", "cross-provider concurrent (6 workers)", lambda n: run_cross_provider_concurrent(n)),
            ("C06", "cross-key rotation under load", lambda n: run_cross_key_concurrent(n)),
        ]

        results = []
        for tid, name, fn in tests:
            reset_all_mocks()
            # Drain any cooldowns left by the previous test (C04's failures put
            # routes into cooldown; without waiting, later tests see every route
            # skipped as unhealthy and exhaust instantly).
            time.sleep(COOLDOWN_DRAIN_S)
            try:
                start = time.time()
                res = fn(tid)
                res.duration_ms = int((time.time() - start) * 1000)
                results.append(res)
                status = "PASS" if res.passed else "FAIL"
                print(f"{status}  {tid}  {name}")
                print(f"        {res.detail}  ({res.duration_ms}ms)")
            except Exception as e:
                print(f"FAIL  {tid}  {name}")
                print(f"        ERROR: {type(e).__name__}: {e}")
                results.append(TestResult(tid, False, f"ERROR: {type(e).__name__}: {e}", 0))
            reset_all_mocks()
            time.sleep(COOLDOWN_DRAIN_S)

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