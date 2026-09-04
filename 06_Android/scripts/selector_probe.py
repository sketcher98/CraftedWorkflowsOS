#!/usr/bin/env python3
"""
Selector Health Probe — Verifies Android UI resource IDs still exist.
Runs at boot / daily / on-demand. Writes state/selectors_ok.json.
Termux-side, no Tasker dependency — can be called from Hermes directly.
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List

CONFIG_PATH = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/config.json"
STATE_DIR = "/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android/state"
SELECTORS_STATE_FILE = os.path.join(STATE_DIR, "selectors_ok.json")

def load_config() -> Dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_json(filepath: str, data: Any) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp = filepath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, filepath)

def load_json(filepath: str, default: Any = None) -> Any:
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath) as f:
            return json.load(f)
    except:
        return default

def dump_ui_tree(package: str) -> Dict:
    """
    Dump the UI tree for a package using uiautomator dump.
    Returns parsed JSON or empty dict on failure.
    """
    try:
        # Use uiautomator dump (requires screen on, unlocked)
        # Dumps to /sdcard/window_dump.xml, we'll parse it
        dump_path = "/sdcard/window_dump.xml"
        subprocess.run(
            ["uiautomator", "dump", dump_path],
            timeout=15, capture_output=True
        )
        # Read and parse XML (basic extraction of resource-ids)
        # For simplicity, we'll use a shell command to extract IDs
        result = subprocess.run(
            ["grep", "-o", 'resource-id="[^"]*"', dump_path],
            capture_output=True, text=True, timeout=5
        )
        ids = set()
        for line in result.stdout.strip().split("\n"):
            if line:
                # Extract the ID between quotes
                import re
                m = re.search(r'resource-id="([^"]*)"', line)
                if m:
                    ids.add(m.group(1))
        return {"package": package, "ids": list(ids), "timestamp": time.time()}
    except Exception as e:
        return {"package": package, "ids": [], "error": str(e), "timestamp": time.time()}

def dump_ui_tree_via_dumpsys(package: str) -> Dict:
    """
    Alternative: use dumpsys activity to get UI tree.
    More reliable on some devices.
    """
    try:
        result = subprocess.run(
            ["dumpsys", "activity", "top"],
            capture_output=True, text=True, timeout=10
        )
        # Parse for the package's window
        output = result.stdout
        if package not in output:
            return {"package": package, "ids": [], "error": "package not in top activity", "timestamp": time.time()}
        
        # Now dump the specific window
        dump_path = "/sdcard/window_dump.xml"
        subprocess.run(["uiautomator", "dump", dump_path], timeout=10, capture_output=True)
        
        result = subprocess.run(
            ["grep", "-o", 'resource-id="[^"]*"', dump_path],
            capture_output=True, text=True, timeout=5
        )
        ids = set()
        for line in result.stdout.strip().split("\n"):
            if line:
                import re
                m = re.search(r'resource-id="([^"]*)"', line)
                if m:
                    ids.add(m.group(1))
        return {"package": package, "ids": list(ids), "timestamp": time.time()}
    except Exception as e:
        return {"package": package, "ids": [], "error": str(e), "timestamp": time.time()}

def probe_selectors(config: Dict) -> Dict[str, Any]:
    """Probe all selectors for both X and LinkedIn apps."""
    results = {
        "timestamp": time.time(),
        "checked": {},
        "all_ok": True,
        "missing": []
    }
    
    # Launch each app, wait, dump UI, check selectors
    for platform in ("x", "linkedin"):
        if platform not in config["selectors"]:
            continue
            
        package = "com.twitter.android" if platform == "x" else "com.linkedin.android"
        selectors = config["selectors"][platform]
        
        print(f"Probing {platform} ({package})...")
        
        # Launch app via termux-open-url deep-link. This is the ONLY launch path
        # that works from Termux on Android 11 (am start activity names are
        # obfuscated/hidden for X and LinkedIn; MAIN/LAUNCHER intent is blocked).
        # termux-open-url resolves the web URL to the installed app via VIEW intent.
        launch_urls = {
            "x": "https://x.com/home",
            "linkedin": "https://www.linkedin.com/feed/"
        }
        try:
            url = launch_urls.get(platform, "")
            if url:
                subprocess.run(["termux-open-url", url], timeout=5)
                print(f"  (launched {platform} via {url})")
        except Exception as e:
            print(f"  (launch warning: {e})")
        
        # Wait for UI to settle
        time.sleep(8)
        
        # Dump UI tree
        ui_data = dump_ui_tree(package)
        found_ids = set(ui_data.get("ids", []))
        
        # Check each selector
        for sel_name, sel_id in selectors.items():
            found = sel_id in found_ids
            results["checked"][f"{platform}.{sel_name}"] = {
                "resource_id": sel_id,
                "found": found,
                "platform": platform
            }
            if not found:
                results["all_ok"] = False
                results["missing"].append({
                    "platform": platform,
                    "selector": sel_name,
                    "resource_id": sel_id,
                    "severity": "critical" if "button" in sel_name or "send" in sel_name else "warning"
                })
                print(f"  MISSING: {platform}.{sel_name} = {sel_id}")
            else:
                print(f"  OK: {platform}.{sel_name} = {sel_id}")
        
        # Go home
        subprocess.run(["am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"], timeout=3)
        time.sleep(1)
    
    return results

def main():
    print("=== Selector Health Probe ===")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    config = load_config()
    
    # Run probe
    results = probe_selectors(config)
    
    # Save state
    save_json(SELECTORS_STATE_FILE, results)
    
    # Print summary
    total = len(results["checked"])
    missing = len(results["missing"])
    print(f"\n=== SUMMARY ===")
    print(f"Total selectors checked: {total}")
    print(f"Missing: {missing}")
    print(f"All OK: {results['all_ok']}")
    
    if results["missing"]:
        print("\nMISSING SELECTORS (will cause automation failures):")
        for m in results["missing"]:
            print(f"  [{m['severity'].upper()}] {m['platform']}.{m['selector']} = {m['resource_id']}")
    
    # Exit code: 0 if all OK, 1 if missing
    sys.exit(0 if results["all_ok"] else 1)

if __name__ == "__main__":
    main()