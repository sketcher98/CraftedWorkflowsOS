#!/usr/bin/env python3
"""
validate_queue.py — schema-as-code gate for the Android layer queue.

Validates queue item JSON against queue_item.schema.json and cross-checks
the routing gap_index against canonical_gaps.json. Exits non-zero on failure.

Usage:
    validate_queue.py <queue_file.json>
    validate_queue.py --all            (validate every file in queue/)
    validate_queue.py --self-test      (run built-in pass/fail check)

No third-party deps: uses a minimal schema subset (required keys, enums,
ranges, types) sufficient for the envelope. Full JSON Schema semantics are
not needed here — the envelope is intentionally small and rigid.
"""

import json
import os
import sys
import glob

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(os.path.dirname(BASE), "schema")
QUEUE_ITEM_SCHEMA = os.path.join(SCHEMA_DIR, "queue_item.schema.json")
CANONICAL_GAPS = os.path.join(SCHEMA_DIR, "canonical_gaps.json")
QUEUE_DIR = os.path.join(os.path.dirname(BASE), "queue")

# Stable ordering for the 7 gaps (memory-as-code: never reorder).
REQUIRED_ROUTING_KEYS = ("gap_index", "provider", "fallback")
PROVIDERS = {"arcade", "zernio", "composio", "native_android"}
FALLBACKS = {"autoinput", "adb", "none"}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def _err(msg):
    print(f"VALIDATE FAIL: {msg}")
    return False


def validate_item(item, canonical):
    if not isinstance(item, dict):
        return _err("item is not a JSON object")

    # Required top-level keys
    for key in ("routing", "action", "message"):
        if key not in item:
            return _err(f"missing required key '{key}'")

    # Routing block
    routing = item["routing"]
    if not isinstance(routing, dict):
        return _err("'routing' must be an object")
    for key in REQUIRED_ROUTING_KEYS:
        if key not in routing:
            return _err(f"routing missing '{key}'")

    gap_index = routing["gap_index"]
    if not isinstance(gap_index, int) or not (1 <= gap_index <= 7):
        return _err(f"gap_index must be int 1-7, got {gap_index!r}")

    # Cross-check gap_index resolves in canonical gaps
    canonical_gap = canonical.get("gaps", {}).get(str(gap_index))
    if canonical_gap is None:
        return _err(f"gap_index {gap_index} not found in canonical_gaps.json")

    # Provider / fallback enums
    provider = routing["provider"]
    if provider not in PROVIDERS:
        return _err(f"provider {provider!r} not in {sorted(PROVIDERS)}")

    fallback = routing["fallback"]
    if fallback not in FALLBACKS:
        return _err(f"fallback {fallback!r} not in {sorted(FALLBACKS)}")

    # Warn (not fail) if routing disagrees with canonical mapping
    if provider != canonical_gap["provider"] and provider != "composio":
        # composio allowed as experimental override while #6 untested
        print(f"VALIDATE WARN: gap_index {gap_index} canonical provider is "
              f"'{canonical_gap['provider']}', item declares '{provider}'")

    if not isinstance(item["action"], str) or not item["action"]:
        return _err("'action' must be a non-empty string")

    if not isinstance(item["message"], str):
        return _err("'message' must be a string")

    return True


def validate_file(path, canonical):
    try:
        item = load_json(path)
    except json.JSONDecodeError as e:
        print(f"VALIDATE FAIL: {path} is not valid JSON: {e}")
        return False
    except FileNotFoundError:
        print(f"VALIDATE FAIL: {path} not found")
        return False

    ok = validate_item(item, canonical)
    status = "PASS" if ok else "FAIL"
    print(f"VALIDATE {status}: {os.path.basename(path)}")
    return ok


def self_test(canonical):
    good = {
        "routing": {"gap_index": 3, "provider": "native_android", "fallback": "autoinput"},
        "action": "x_media_tweet",
        "lead_id": "lead_001",
        "message": "Launch post",
        "target": {"handle": "someuser", "media_urls": ["https://example.com/a.png"]},
    }
    bad_missing_routing = {"action": "x_dm", "message": "hi"}
    bad_gap_index = {"routing": {"gap_index": 99, "provider": "arcade", "fallback": "none"},
                     "action": "x_dm", "message": "hi"}
    bad_enum = {"routing": {"gap_index": 1, "provider": "aws", "fallback": "none"},
                "action": "x_dm", "message": "hi"}

    print("[self-test] expect PASS, FAIL, FAIL, FAIL")
    results = [
        ("good", validate_item(good, canonical)),
        ("missing routing", validate_item(bad_missing_routing, canonical)),
        ("bad gap_index", validate_item(bad_gap_index, canonical)),
        ("bad enum", validate_item(bad_enum, canonical)),
    ]
    expected = [True, False, False, False]
    all_ok = True
    for (name, got), exp in zip(results, expected):
        mark = "ok" if got == exp else "MISMATCH"
        if got != exp:
            all_ok = False
        print(f"  {name}: got={got} expect={exp} [{mark}]")
    return all_ok


def main():
    args = sys.argv[1:]

    try:
        canonical = load_json(CANONICAL_GAPS)
    except Exception as e:
        print(f"VALIDATE FATAL: cannot load canonical_gaps.json: {e}")
        sys.exit(2)

    if "--self-test" in args:
        sys.exit(0 if self_test(canonical) else 1)

    if "--all" in args:
        files = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
        if not files:
            print("No queue items to validate")
            sys.exit(0)
        ok = all(validate_file(p, canonical) for p in files)
        sys.exit(0 if ok else 1)

    if not args:
        print(__doc__)
        sys.exit(2)

    ok = all(validate_file(p, canonical) for p in args)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()