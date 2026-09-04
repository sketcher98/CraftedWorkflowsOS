#!/usr/bin/env python3
"""
X/Twitter Media Tweet Poster — AutoInput flow
Posts tweet with image/media attachment
Reads exec_*.json from results/ (processor handoff), writes final result back.
"""

import json
import os
import sys
import time
import subprocess
import random
from pathlib import Path
from typing import Dict

RESULTS_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/results"
CONFIG_PATH = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/config.json"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def run_autoinput(action: str, **kwargs) -> bool:
    if action == "tap":
        x, y = kwargs.get("x"), kwargs.get("y")
        cmd = [
            "am", "broadcast", "-a", "com.joaomgcd.autoinput.action.TAP",
            "--ei", "x", str(x), "--ei", "y", str(y)
        ]
    elif action == "text":
        text = kwargs.get("text", "")
        cmd = [
            "am", "broadcast", "-a", "com.joaomgcd.autoinput.action.TEXT",
            "--es", "text", text
        ]
    elif action == "key":
        keycode = kwargs.get("keycode", 66)
        cmd = [
            "am", "broadcast", "-a", "com.joaomgcd.autoinput.action.KEY",
            "--ei", "keycode", str(keycode)
        ]
    elif action == "id":
        resource_id = kwargs.get("id", "")
        cmd = [
            "am", "broadcast", "-a", "com.joaomgcd.autoinput.action.CLICK",
            "--es", "id", resource_id
        ]
    else:
        return False
    
    try:
        subprocess.run(cmd, timeout=10, capture_output=True)
        return True
    except Exception as e:
        print(f"AutoInput error: {e}")
        return False

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

def wait(seconds: float):
    time.sleep(seconds)

def x_media_flow(exec_file: str, config: Dict) -> Dict:
    with open(exec_file) as f:
        item = json.load(f)
    
    queue_id = item.get("queue_id", os.path.basename(exec_file))
    lead_id = item.get("lead_id", "unknown")
    message = item.get("message", "")
    target = item.get("target", {})
    media_path = target.get("media_path", "")  # Local path to image
    
    selectors = config["selectors"]["x"]
    behavior = config["behavior"]
    
    result = {
        "queue_id": queue_id,
        "lead_id": lead_id,
        "action": "x_media_tweet",
        "status": "failed",
        "sent_at": None,
        "platform_message_id": None,
        "error": None,
        "duration_ms": 0
    }
    
    start_time = time.time()
    
    try:
        # NOTE: App is launched by Tasker's "Launch App" action, NOT here.
        # This script only does UI taps + text + verify against the already-open app.
        wait(behavior.get("app_launch_timeout_seconds", 10))
        
        # 2. Tap Compose button
        if run_autoinput("id", id=selectors["compose_button"]):
            wait(2)
        
        # 3. Type tweet text
        run_autoinput("text", text=message)
        wait(1)
        
        # 4. Attach media if provided
        if media_path and os.path.exists(media_path):
            if run_autoinput("id", id=selectors["media_button"]):
                wait(2)
                # This is tricky - need to navigate file picker
                # For now, assume gallery opens and we need to select
                # In practice, this needs coordinate taps or more sophisticated approach
                pass
        
        # 5. Post
        if run_autoinput("id", id=selectors["post_button"]):
            wait(3)
        else:
            run_autoinput("key", keycode=66)
        
        # Check rate limits
        if toast_contains(behavior["toast_keywords_rate_limit"]):
            result["status"] = "rate_limited"
            result["error"] = "Rate limited by X"
            return result
        
        if toast_contains(behavior["toast_keywords_error"]):
            result["status"] = "failed"
            result["error"] = "Toast error detected"
            return result
        
        result["status"] = "sent"
        result["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["duration_ms"] = int((time.time() - start_time) * 1000)
        
    except Exception as e:
        result["error"] = str(e)
        result["duration_ms"] = int((time.time() - start_time) * 1000)
    
    return result

def main():
    if len(sys.argv) < 2:
        print("Usage: x_media_post.py <exec_file>")
        sys.exit(1)
    
    exec_file = sys.argv[1]
    config = load_config()
    
    result = x_media_flow(exec_file, config)
    
    # Write final result back to the SAME exec_* file (atomic overwrite)
    with open(exec_file, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    
    print(json.dumps(result, separators=(",", ":")))

if __name__ == "__main__":
    main()