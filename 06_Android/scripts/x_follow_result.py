#!/usr/bin/env python3
"""
X/Twitter Follow/Unfollow Result Handler
Called by Tasker AFTER the 3-layer UI chain completes.
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict

RESULTS_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/results"
CONFIG_PATH = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/config.json"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def toast_contains(keywords: list) -> bool:
    try:
        result = subprocess.run(
            ["logcat", "-d", "-t", "10", "*:E"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout.lower()
        return any(kw.lower() in output for kw in keywords)
    except:
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: x_follow_result.py <exec_file> [status]")
        print("  status: sent | failed | rate_limited")
        sys.exit(1)
    
    exec_file = sys.argv[1]
    override_status = sys.argv[2] if len(sys.argv) > 2 else None
    
    config = load_config()
    behavior = config["behavior"]
    
    with open(exec_file) as f:
        item = json.load(f)
    
    queue_id = item.get("queue_id", os.path.basename(exec_file))
    lead_id = item.get("lead_id", "unknown")
    action_type = item.get("follow_action", "follow")
    
    if override_status:
        status = override_status
    elif toast_contains(behavior["toast_keywords_rate_limit"]):
        status = "rate_limited"
    elif toast_contains(behavior["toast_keywords_error"]):
        status = "failed"
    else:
        status = "sent"
    
    result = {
        "queue_id": queue_id,
        "lead_id": lead_id,
        "action": f"x_{action_type}",
        "status": status,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if status == "sent" else None,
        "platform_message_id": None,
        "error": "Rate limited by X" if status == "rate_limited" else ("Toast error detected" if status == "failed" else None),
        "duration_ms": 0
    }
    
    with open(exec_file, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    
    print(json.dumps(result, separators=(",", ":")))

if __name__ == "__main__":
    main()