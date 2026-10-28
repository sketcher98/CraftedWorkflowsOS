#!/usr/bin/env python3
"""Archive router dir residue (backup/pre/rollback/pre/known-good files) to a single zip."""
import os, shutil, zipfile
from pathlib import Path

router_dir = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/router")
archive_path = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/router-residue-archive-20261028.zip")

# Patterns to archive
patterns = [
    "*.bak*", "*.before*", "*.rollback*", "*.pre-*", "*known-good*", "*working-baseline*",
    "config.json.bak*", "config.json.before*", "config.json.rollback*", "config.json.pre-*",
    "main.go.bak*", "main.go.before*", "main.go.rollback*", "main.go.pre-*", "main.go.bad-*",
    "router.pre-*", "router.log.before*", "router.log.bak*", "router-events.jsonl.bak*",
]

files = []
for pat in patterns:
    files.extend(router_dir.glob(pat))

# Deduplicate
files = list(set(files))
print(f"Found {len(files)} files to archive")

with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for f in files:
        arcname = f.name
        zf.write(f, arcname)

print(f"Archive created: {archive_path} ({archive_path.stat().st_size/1024/1024:.1f} MB)")

# Verify
with zipfile.ZipFile(archive_path, 'r') as zf:
    print(f"Archive contains {len(zf.namelist())} files")

# Now delete the originals
for f in files:
    try:
        f.unlink()
        print(f"Deleted: {f.name}")
    except Exception as e:
        print(f"Failed to delete {f.name}: {e}")

print("Residue cleanup complete")
