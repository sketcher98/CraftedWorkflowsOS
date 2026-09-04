#!/usr/bin/env python3
"""
gap_report.py — Queryable per-gap observability for the Android layer.

Reads result files and rate limit state, joins with canonical_gaps.json,
and prints a human-readable table (or JSON) showing throughput per gap.

Usage:
    python gap_report.py --week              # Last 7 days
    python gap_report.py --today             # Current day from rate_limits.json
    python gap_report.py --since 2026-09-01  # Since specific date
    python gap_report.py --gap 3             # Filter to gap_index 3
    python gap_report.py --json              # Machine-readable output
    python gap_report.py --failures          # Show only failed/rate-limited
"""

import json
import glob
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

BASE = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/06_Android")
RESULTS_DIR = BASE / "results"
STATE_DIR = BASE / "state"
RATE_LIMITS_FILE = STATE_DIR / "rate_limits.json"
CONFIG_FILE = BASE / "config.json"
CANONICAL_GAPS_FILE = BASE / "schema" / "canonical_gaps.json"


def load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def parse_since(s: str) -> datetime:
    """Parse --since argument: '7d', '30d', '2026-09-01', or 'today'."""
    now = datetime.now()
    s = s.lower().strip()
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s.endswith("d"):
        try:
            days = int(s[:-1])
            return now - timedelta(days=days)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        print(f"Invalid --since value: {s}", file=sys.stderr)
        sys.exit(2)


def build_action_to_gap_map(canonical: Dict) -> Dict[str, int]:
    """Map action names (e.g., 'x_dm') to gap_index using canonical gaps."""
    # Action names from config.json rate_limits keys and queue items
    mapping = {
        "linkedin_dm": 1,
        "x_dm": 2,
        "x_media_tweet": 3,
        "x_follow": 4,
        "linkedin_connect": 5,
        "linkedin_people_search": 6,
        "instagram_dm": 7,
    }
    return mapping


def scan_results(since: datetime) -> List[Dict]:
    """Read all result files, filter by sent_at >= since."""
    results = []
    seen = set()
    for pattern in ("*.json", "exec_*.json", "*_result.json"):
        for path in RESULTS_DIR.glob(pattern):
            # Deduplicate by real path
            real = path.resolve()
            if real in seen:
                continue
            seen.add(real)

            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                continue

            sent_at_str = data.get("sent_at") or data.get("timestamp")
            if not sent_at_str:
                continue
            try:
                sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
                if sent_at.tzinfo is not None:
                    sent_at = sent_at.replace(tzinfo=None)
            except Exception:
                continue

            if sent_at >= since:
                results.append(data)
    return results


def aggregate_by_gap(results: List[Dict], action_to_gap: Dict[str, int]) -> Dict[int, Dict]:
    """Aggregate counts per gap_index."""
    agg = {}
    for r in results:
        action = r.get("action", "unknown")
        gap_index = action_to_gap.get(action)
        if gap_index is None:
            continue
        bucket = agg.setdefault(gap_index, {"fired": 0, "sent": 0, "failed": 0, "rate_limited": 0, "durations": []})
        bucket["fired"] += 1
        status = r.get("status", "unknown")
        if status == "sent":
            bucket["sent"] += 1
        elif status == "failed":
            bucket["failed"] += 1
        elif status == "rate_limited":
            bucket["rate_limited"] += 1
        dur = r.get("duration_ms")
        if isinstance(dur, (int, float)):
            bucket["durations"].append(dur)
    return agg


def load_rate_limits() -> Dict:
    return load_json(RATE_LIMITS_FILE, {"date": "", "counts": {}})


def load_config() -> Dict:
    return load_json(CONFIG_FILE, {})


def load_canonical_gaps() -> Dict:
    return load_json(CANONICAL_GAPS_FILE, {"gaps": {}})


def format_table(rows: List[Dict], show_failures: bool = False) -> str:
    """Format as a fixed-width table."""
    if not rows:
        return "No data in selected window."

    # Header
    lines = []
    lines.append("GAP REPORT")
    lines.append("═" * 112)
    header = f"{'Gap':>3} │ {'Platform/Capability':<28} │ {'Fired':>5} │ {'Sent':>4} │ {'Failed':>6} │ {'RateLim':>7} │ {'Limit':>5} │ {'Used%':>6} │ {'Avg ms':>7}"
    lines.append(header)
    lines.append("─" * 112)

    total_fired = total_sent = total_failed = total_ratelim = 0

    for row in rows:
        gap = row["gap_index"]
        platform = row["platform"]
        capability = row["capability"]
        fired = row["fired"]
        sent = row["sent"]
        failed = row["failed"]
        rate_limited = row["rate_limited"]
        limit = row["limit"]
        used_pct = row["used_pct"]
        avg_ms = row["avg_ms"]

        label = f"{platform}/{capability}"
        limit_str = str(limit) if limit > 0 else "—"
        used_str = f"{used_pct:.0f}%" if used_pct > 0 else "—"
        avg_str = f"{avg_ms/1000:.1f}s" if avg_ms > 0 else "—"

        line = f"{gap:>3} │ {label:<28} │ {fired:>5} │ {sent:>4} │ {failed:>6} │ {rate_limited:>7} │ {limit_str:>5} │ {used_str:>6} │ {avg_str:>7}"
        lines.append(line)

        total_fired += fired
        total_sent += sent
        total_failed += failed
        total_ratelim += rate_limited

    lines.append("─" * 112)
    total_line = f"{'':>3} │ {'TOTAL':<28} │ {total_fired:>5} │ {total_sent:>4} │ {total_failed:>6} │ {total_ratelim:>7} │ {'':>5} │ {'':>6} │ {'':>7}"
    lines.append(total_line)

    if show_failures:
        lines.append("")
        lines.append("FAILURE DETAILS")
        lines.append("─" * 112)
        # Would need to collect failed items with lead_ids - kept simple for now

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Android layer gap report")
    parser.add_argument("--since", default="7d", help="Time window: '7d', '30d', '2026-09-01', 'today'")
    parser.add_argument("--today", action="store_true", help="Alias for --since today")
    parser.add_argument("--week", action="store_true", help="Alias for --since 7d")
    parser.add_argument("--gap", type=int, help="Filter to single gap_index (1-7)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--failures", action="store_true", help="Show failure details")
    args = parser.parse_args()

    # Resolve time window
    if args.today:
        since = parse_since("today")
    elif args.week:
        since = parse_since("7d")
    else:
        since = parse_since(args.since)

    # Load data
    canonical = load_canonical_gaps()
    action_to_gap = build_action_to_gap_map(canonical)
    gaps_data = canonical.get("gaps", {})
    config = load_config()
    rate_limits = load_rate_limits()

    # Current day's rate limit counts (for 'Limit' and 'Used%' columns)
    today = datetime.now().strftime("%Y-%m-%d")
    current_counts = rate_limits.get("counts", {}) if rate_limits.get("date") == today else {}

    config_limits = config.get("rate_limits", {})

    # Scan results
    results = scan_results(since)
    agg = aggregate_by_gap(results, action_to_gap)

    # Build rows joining agg + canonical + config + rate_limits
    rows = []
    for gap_index in range(1, 8):
        if args.gap and gap_index != args.gap:
            continue

        gap_info = gaps_data.get(str(gap_index), {})
        platform = gap_info.get("platform", "?")
        capability = gap_info.get("capability", "?")

        bucket = agg.get(gap_index, {"fired": 0, "sent": 0, "failed": 0, "rate_limited": 0, "durations": []})

        # Map gap_index to action name for rate limit lookup
        action_name = {v: k for k, v in action_to_gap.items()}.get(gap_index, "")
        limit = config_limits.get(action_name, {}).get("daily_limit", 0)
        used = current_counts.get(action_name, 0)
        used_pct = (used / limit * 100) if limit > 0 else 0

        durations = bucket["durations"]
        avg_ms = sum(durations) / len(durations) if durations else 0

        rows.append({
            "gap_index": gap_index,
            "platform": platform,
            "capability": capability,
            "fired": bucket["fired"],
            "sent": bucket["sent"],
            "failed": bucket["failed"],
            "rate_limited": bucket["rate_limited"],
            "limit": limit,
            "used": used,
            "used_pct": used_pct,
            "avg_ms": avg_ms,
        })

    if args.json:
        print(json.dumps({"since": since.isoformat(), "gaps": rows}, indent=2))
    else:
        print(format_table(rows, show_failures=args.failures))


if __name__ == "__main__":
    main()