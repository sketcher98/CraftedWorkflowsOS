#!/usr/bin/env python3
"""
Android Queue Processor — Termux side
Reads queue/*.json, enforces rate limits, delegates to AutoInput via Tasker.
Zero business logic — only queue management + rate limiting.
"""

import json
import os
import sys
import time
import glob
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

CONFIG_PATH = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/config.json"
STATE_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/state"
QUEUE_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/queue"
RESULTS_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/results"
RATE_LIMITS_FILE = os.path.join(STATE_DIR, "rate_limits.json")
LAST_RUN_FILE = os.path.join(STATE_DIR, "last_run.json")
VALIDATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate_queue.py")

def subprocess_run(args):
    return subprocess.run(args, capture_output=True, text=True)

def _item_valid(queue_file: str) -> bool:
    if not os.path.exists(VALIDATOR):
        print("WARN: validate_queue.py missing, skipping schema gate")
        return True
    r = subprocess_run([sys.executable, VALIDATOR, queue_file])
    return r.returncode == 0

def load_config() -> Dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)

def load_json(filepath: str, default: Any = None) -> Any:
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath) as f:
            return json.load(f)
    except:
        return default

def save_json(filepath: str, data: Any) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp = filepath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, filepath)

def get_rate_limits(config: Dict) -> Dict:
    today = datetime.now().strftime("%Y-%m-%d")
    limits = load_json(RATE_LIMITS_FILE, {"date": today, "counts": {}})
    if limits.get("date") != today:
        limits = {"date": today, "counts": {}}
        for action, cfg in config["rate_limits"].items():
            limits["counts"][action] = 0
        save_json(RATE_LIMITS_FILE, limits)
    return limits

def check_rate_limit(action: str, config: Dict, limits: Dict) -> bool:
    limit_cfg = config["rate_limits"].get(action, {})
    daily_limit = limit_cfg.get("daily_limit", 0)
    current = limits["counts"].get(action, 0)
    return current < daily_limit

def increment_rate_limit(action: str, limits: Dict) -> None:
    limits["counts"][action] = limits["counts"].get(action, 0) + 1
    save_json(RATE_LIMITS_FILE, limits)

def is_active_hours(config: Dict, action: str) -> bool:
    now = datetime.now().hour
    cfg = config["rate_limits"].get(action, {})
    start = cfg.get("active_hours_start", 0)
    end = cfg.get("active_hours_end", 23)
    return start <= now <= end

def get_min_delay(config: Dict, action: str) -> int:
    return config["rate_limits"].get(action, {}).get("min_delay_minutes", 5) * 60

def get_max_delay(config: Dict, action: str) -> int:
    return config["rate_limits"].get(action, {}).get("max_delay_minutes", 15) * 60

def pick_spintax(config: Dict, key: str) -> str:
    import random
    variants = config["spintax"].get(key, [""])
    return random.choice(variants)

def process_queue_item(queue_file: str, config: Dict, limits: Dict) -> Optional[Dict]:
    if not _item_valid(queue_file):
        print(f"Schema validation failed, discarding {queue_file}")
        return {"status": "failed", "error": "schema validation failed"}

    try:
        with open(queue_file) as f:
            item = json.load(f)
    except Exception as e:
        print(f"Failed to read {queue_file}: {e}")
        return {"status": "failed", "error": str(e)}

    action = item.get("action", "unknown")
    lead_id = item.get("lead_id", "unknown")

    if not is_active_hours(config, action):
        print(f"Outside active hours for {action}, re-queue for tomorrow")
        return None

    if not check_rate_limit(action, config, limits):
        print(f"Rate limit reached for {action}")
        return None

    message = item.get("message", "")
    if "{name}" in message:
        name = item.get("target", {}).get("name", "there")
        message = message.replace("{name}", name)
    
    if action == "linkedin_dm" and not any(message.startswith(op.split("{")[0].strip()) for op in config["spintax"]["linkedin_dm_openers"]):
        opener = pick_spintax(config, "linkedin_dm_openers").replace("{name}", item.get("target", {}).get("name", "there"))
        message = f"{opener}\n\n{message}"
        item["message"] = message
    elif action == "x_dm" and not any(message.startswith(op.split("{")[0].strip()) for op in config["spintax"]["x_dm_openers"]):
        opener = pick_spintax(config, "x_dm_openers")
        message = f"{opener}\n\n{message}"
        item["message"] = message

    with open(queue_file, "w") as f:
        json.dump(item, f, separators=(",", ":"))

    increment_rate_limit(action, limits)

    exec_file = os.path.join(RESULTS_DIR, f"exec_{os.path.basename(queue_file)}")
    exec_data = {
        "queue_id": item.get("id", os.path.basename(queue_file)),
        "action": action,
        "lead_id": lead_id,
        "status": "ready_for_execution",
        "message": item["message"],
        "target": item.get("target", {}),
        "timestamp": datetime.now().isoformat()
    }
    save_json(exec_file, exec_data)
    
    os.remove(queue_file)
    
    print(f"Prepared execution: {action} for {lead_id}")
    return {"status": "handled"}

def main():
    config = load_config()
    limits = get_rate_limits(config)

    queue_files = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
    if not queue_files:
        print("No queue items")
        return

    print(f"Found {len(queue_files)} queue items")

    for qf in queue_files:
        result = process_queue_item(qf, config, limits)
        if result is None:
            print(f"Re-queue: {os.path.basename(qf)}")
            continue
        
        if result.get("status") == "handled":
            continue
            
        if result.get("status") == "failed":
            result_file = os.path.join(RESULTS_DIR, os.path.basename(qf))
            save_json(result_file, result)
            os.remove(qf)
            continue

    save_json(LAST_RUN_FILE, {
        "last_run": datetime.now().isoformat(),
        "items_processed": len(queue_files)
    })

if __name__ == "__main__":
    main()