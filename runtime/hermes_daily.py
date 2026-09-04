#!/usr/bin/env python3
"""
Hermes Daily Run — Commercial Department Execution Engine.

Each run (scheduled via Termux cron or manual):
  1. PROSPECTING (Tier 1, autonomous): pulls "New" leads from Notion, scores
     + assigns DM flow per 04_Knowledge/Playbooks/Outreach, writes back to
     Notion directly. No approval needed — internal record-keeping.
  2. OUTREACH DRAFTING (Tier 1 draft / Tier 2 send): drafts a DM per assigned
     flow for "Queued" leads, writes the draft to Notion as "Pending Review",
     and logs for CEO review in this session (Telegram optional).
  3. PROCESS APPROVALS (Tier 2 gate): reads CEO decisions from session context
     (Approve/Edit/Deny). Approve -> sends via Composio Gmail (LinkedIn/IG
     sends via Zernio, still Tier 2). Deny -> archives. Edit -> waits for
     corrected text.
  4. ANDROID RESULTS: reads completed Android queue results, updates Notion.
  5. CHECKPOINT: saves run state to runtime/cache/checkpoint.json for next
     session's hot/warm cache restore.

Run this on a schedule (see SETUP.md). Deliberately NOT a persistent agent
loop — a scheduled batch job is more reliable on a phone than a long-running
process Android can kill at any time.

NO GROQ. Uses Hermes's own model for all LLM calls.
"""

import json
import os
import glob
from datetime import datetime
from pathlib import Path

import requests

# ---- Config (read from env / bashrc) ----
COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY")
USER_ID = os.environ.get("COMPOSIO_USER_ID", "precious-cw")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # optional
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")      # optional

DB_IDS = {
    "Leads": os.environ.get("NOTION_DB_LEADS"),
    "Meetings": os.environ.get("NOTION_DB_MEETINGS"),
    "Clients": os.environ.get("NOTION_DB_CLIENTS"),
    "Projects": os.environ.get("NOTION_DB_PROJECTS"),
    "Tasks": os.environ.get("NOTION_DB_TASKS"),
}

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = BASE_DIR / "04_Knowledge"
DEPT_DIR = BASE_DIR / "03_Departments" / "Commercial"
CHECKPOINT_FILE = Path(__file__).resolve().parent / "cache" / "checkpoint.json"

# Android Layer paths
ANDROID_QUEUE_DIR = BASE_DIR / "06_Android" / "queue"
ANDROID_RESULTS_DIR = BASE_DIR / "06_Android" / "results"
ANDROID_STATE_DIR = BASE_DIR / "06_Android" / "state"

# ---- Hermes model call (uses configured provider in Hermes, NOT Groq) ----
def call_hermes_model(system_prompt: str, user_prompt: str) -> str:
    """
    Call the configured Hermes model directly. This replaces the Groq call.
    In practice, this is invoked via the Hermes agent's own inference, so
    this function is a placeholder — the actual call happens through Hermes
    native tooling. For standalone testing, you'd swap in your provider.
    """
    # In Hermes: this is a native model call, not an HTTP request.
    # The agent running this script IS the LLM — it just reasons directly.
    # If you need a tool call pattern, use the Hermes skill system.
    raise NotImplementedError(
        "In Hermes, the agent itself is the model. Don't call HTTP; "
        "reason directly and write Notion via Composio MCP."
    )

# ---- Notion via Composio MCP (native calls in Hermes) ----
# In Hermes, use: mcp__composio__NOTION_QUERY_DATABASE, etc.
# This stub is for reference — actual calls are native MCP.
def notion_query(database_id: str, filter_obj: dict = None) -> dict:
    """Query a Notion database via Composio."""
    # Native: mcp__composio__NOTION_QUERY_DATABASE
    # args: {"database_id": database_id, "filter": filter_obj}
    return {"data": {"results": []}}

def notion_update_page(page_id: str, properties: dict) -> dict:
    """Update a Notion page via Composio."""
    # Native: mcp__composio__NOTION_UPDATE_PAGE
    # args: {"page_id": page_id, "properties": properties}
    return {"ok": True}

def notion_create_page(database_id: str, properties: dict) -> dict:
    """Create a Notion page via Composio."""
    # Native: mcp__composio__NOTION_CREATE_PAGE
    # args: {"database_id": database_id, "properties": properties}
    return {"id": "new_page_id"}

# ---- Telegram (optional) ----
def telegram_send(text: str, buttons: list = None):
    """Send a Telegram message with inline keyboard."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json=payload, timeout=15,
    )

def telegram_get_updates(offset: int) -> list:
    """Poll Telegram for callback queries."""
    if not TELEGRAM_BOT_TOKEN:
        return []
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 5}, timeout=15,
    )
    return resp.json().get("result", [])

# ---- Context loading (Hot + Warm cache per 00_System/cache_rules.md) ----
def load_context() -> dict:
    def read(path: str) -> str:
        p = BASE_DIR / path
        return p.read_text() if p.exists() else ""

    return {
        "philosophy": read("04_Knowledge/Company/Core_Philosophy.md"),
        "value_prop": read("04_Knowledge/Company/Value_Proposition.md"),
        "website_gt": read("04_Knowledge/Company/Website_Ground_Truth.md"),
        "target_market": read("04_Knowledge/Company/Target_Market.md"),
        "dm_flows": read("04_Knowledge/Playbooks/Outreach/DM_Flows.md"),
        "daily_targeting": read("04_Knowledge/Playbooks/Outreach/Daily_Targeting_Playbook.md"),
        "qualification": read("04_Knowledge/Playbooks/Outreach/Lead_Qualification.md"),
        "lead_sourcing": read("04_Knowledge/Playbooks/Outreach/Lead_Sourcing.md"),
    }

# ---- Checkpoint (Hot/Warm cache persistence) ----
def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return {"telegram_update_offset": 0, "pending_review": {}}

def save_checkpoint(cp: dict):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(cp, indent=2))

# ---- Android Queue Integration ----
def needs_android_execution(channel: str, action_type: str) -> bool:
    """
    Determine if an action requires Android UI execution.
    These are the 4 gaps where no API exists.
    """
    android_gaps = {
        "linkedin": ["dm"],
        "x": ["dm", "media_tweet", "follow", "unfollow"],
        "instagram": ["dm"],
    }
    return action_type in android_gaps.get(channel.lower(), [])

def write_android_queue(queue_type: str, lead_id: str, message: str, target: dict, **kwargs) -> str:
    """
    Write a queue file for Android execution.
    Returns the queue file path.
    """
    ANDROID_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    
    queue_id = f"{queue_type}_{lead_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    queue_file = ANDROID_QUEUE_DIR / f"{queue_id}.json"
    
    item = {
        "id": queue_id,
        "type": queue_type,
        "lead_id": lead_id,
        "target": target,
        "message": message,
        "created_at": datetime.now().isoformat(),
        "priority": "normal",
        "retry_count": 0,
        "max_retries": 3,
        **kwargs
    }
    
    queue_file.write_text(json.dumps(item, separators=(",", ":")))
    return str(queue_file)

def read_android_results() -> list:
    """
    Read all completed Android results.
    Returns list of result dicts.
    """
    results = []
    result_files = glob.glob(str(ANDROID_RESULTS_DIR / "*_result.json"))
    
    for rf in result_files:
        try:
            result = json.loads(Path(rf).read_text())
            results.append((rf, result))
        except Exception as e:
            print(f"Failed to read result {rf}: {e}")
    
    return results

def process_android_results(results: list):
    """
    Process Android results: update Notion, clean up result files.
    """
    for result_file, result in results:
        lead_id = result.get("lead_id")
        status = result.get("status")
        action = result.get("action", "unknown")
        
        if not lead_id or lead_id == "unknown":
            print(f"Skipping result without lead_id: {result}")
            continue
        
        if status == "sent":
            # Update Notion to Sent
            notion_update_page(lead_id, {
                "Status": {"select": {"name": "Sent"}},
                "Sent At": {"date": {"start": result.get("sent_at", datetime.now().isoformat())}},
                "Sent Via": {"select": {"name": f"Android: {action}"}},
            })
            print(f"[ANDROID] Updated {lead_id} to Sent via {action}")
            
            # Telegram notification
            telegram_send(f"✅ Sent via Android ({action}): {lead_id}")
        
        elif status == "rate_limited":
            # Re-queue for tomorrow
            telegram_send(f"⚠️ Rate limited on {action} for {lead_id} — will retry tomorrow")
            # Re-create queue item
            # (Would need original item data; for now just alert)
        
        elif status == "failed":
            telegram_send(f"❌ Failed {action} for {lead_id}: {result.get('error', 'Unknown error')}")
            notion_update_page(lead_id, {
                "Status": {"select": {"name": "Failed"}},
                "Last Error": {"rich_text": [{"text": {"content": result.get("error", "Unknown")}}]},
            })
        
        # Clean up result file
        try:
            Path(result_file).unlink()
        except:
            pass

# ---- Phase 1: Prospecting (Tier 1 - autonomous) ----
def run_prospecting(ctx: dict):
    """
    Prospecting Specialist: score new leads, assign flow, move to Queued.
    Uses: Target_Market.md, Lead_Qualification.md, DM_Flows.md
    """
    if not DB_IDS["Leads"]:
        print("NOTION_DB_LEADS not set")
        return

    new_leads = notion_query(DB_IDS["Leads"], {
        "property": "Status", "select": {"equals": "New"}
    })
    rows = new_leads.get("data", {}).get("results", [])

    for row in rows:
        page_id = row["id"]
        props = row["properties"]
        name = props.get("Name", {}).get("title", [{}])[0].get("plain_text", "")
        company = props.get("Company", {}).get("rich_text", [{}])[0].get("plain_text", "")
        title = props.get("Title", {}).get("rich_text", [{}])[0].get("plain_text", "")

        system_prompt = f"""You are the Prospecting Specialist at CraftedWorkflows.
ICP: {ctx['target_market']}
Qualification rules: {ctx['qualification']}
DM Flows reference: {ctx['dm_flows']}
Assign an ICP Score (0-100) and a Flow (Flow 1-5) per the playbook.
Also determine best channel: LinkedIn, X, or Email.
Reply as JSON only: {{"icp_score": int, "flow": "Flow N", "channel": "LinkedIn|X|Email"}}"""

        user_prompt = f"Lead: {name}, {title} at {company}"

        # In Hermes: the agent IS the model — reason directly here
        # For standalone reference, the prompt above is what the agent evaluates
        print(f"[PROSPECTING] Would score: {name} at {company}")
        print(f"  System: {system_prompt[:200]}...")
        print(f"  User: {user_prompt}")

        # Placeholder: agent writes result directly via Notion MCP
        # notion_update_page(page_id, {
        #     "ICP Score": {"number": result["icp_score"]},
        #     "Flow Assigned": {"select": {"name": result["flow"]}},
        #     "Channel": {"select": {"name": result["channel"]}},
        #     "Status": {"select": {"name": "Queued"}},
        # })
        # print(f"Scored {name}: {result}")

# ---- Phase 2: Outreach Drafting (Tier 1 draft, Tier 2 send-gate) ----
def run_outreach_drafting(ctx: dict, checkpoint: dict):
    """
    Outreach Specialist: draft opening DM for queued leads, log to Notion
    as Pending Review, notify CEO (Telegram or session context).
    """
    if not DB_IDS["Leads"]:
        return

    queued = notion_query(DB_IDS["Leads"], {
        "property": "Status", "select": {"equals": "Queued"}
    })
    rows = queued.get("data", {}).get("results", [])

    for row in rows:
        page_id = row["id"]
        props = row["properties"]
        name = props.get("Name", {}).get("title", [{}])[0].get("plain_text", "")
        company = props.get("Company", {}).get("rich_text", [{}])[0].get("plain_text", "")
        flow = props.get("Flow Assigned", {}).get("select", {}).get("name", "Flow 1")
        channel = props.get("Channel", {}).get("select", {}).get("name", "LinkedIn")

        system_prompt = f"""You are the Outreach Specialist at CraftedWorkflows.
Voice/values: {ctx['philosophy']}
Value prop: {ctx['value_prop']}
DM Flows: {ctx['dm_flows']}
Website ground truth: {ctx['website_gt']}
Write the opening message (step 1 only) for {flow} on {channel}.
Reply with ONLY the message text — no labels, no extra commentary."""

        user_prompt = f"Lead: {name} at {company}"

        print(f"[OUTREACH] Would draft {flow} for: {name} at {company} ({channel})")
        print(f"  System: {system_prompt[:200]}...")
        print(f"  User: {user_prompt}")

        # Placeholder: agent writes draft to Notion
        # notion_update_page(page_id, {"Status": {"select": {"name": "Drafted"}}})
        # checkpoint["pending_review"][page_id] = {
        #     "name": name, "company": company, "flow": flow, "channel": channel,
        #     "draft": draft_text, "target": {"name": name, "company": company}
        # }

        # Notify CEO
        # telegram_send(f"Draft ready — {name} ({company}) — {flow}\n\n{draft_text}", buttons=[...])

# ---- Phase 3: Process Approvals (Tier 2 gate) ----
def process_approvals(checkpoint: dict):
    """Poll for CEO decisions on pending review items."""
    updates = telegram_get_updates(checkpoint.get("telegram_update_offset", 0))
    for update in updates:
        checkpoint["telegram_update_offset"] = update["update_id"] + 1
        callback = update.get("callback_query")
        if not callback:
            continue

        action, page_id = callback["data"].split(":", 1)
        pending = checkpoint["pending_review"].get(page_id)
        if not pending:
            continue

        if action == "approve":
            channel = pending.get("channel", "LinkedIn")
            flow = pending.get("flow", "Flow 1")
            draft = pending.get("draft", "")
            target = pending.get("target", {})
            
            # Check if this needs Android execution
            if needs_android_execution(channel, flow.lower().replace("flow ", "dm")):
                # Write Android queue instead of direct API
                queue_type = f"{channel.lower()}_dm"
                write_android_queue(
                    queue_type=queue_type,
                    lead_id=page_id,
                    message=draft,
                    target=target
                )
                notion_update_page(page_id, {"Status": {"select": {"name": "Queued for Android"}}})
                telegram_send(f"✅ Queued for Android execution: {pending['name']} via {channel} DM")
            else:
                # Direct API execution (Zernio for LI/IG posts, Arcade X for tweets, Composio for email)
                notion_update_page(page_id, {"Status": {"select": {"name": "Sent"}}})
                telegram_send(f"Approved: {pending['name']} — sending via {channel}")
            
            del checkpoint["pending_review"][page_id]

        elif action == "deny":
            notion_update_page(page_id, {"Status": {"select": {"name": "Archived"}}})
            telegram_send(f"Archived: {pending['name']}")
            del checkpoint["pending_review"][page_id]

        elif action == "edit":
            telegram_send(f"Reply with corrected text for {pending['name']}, starting with EDIT:{page_id}")

        # Handle plain-text edit reply
        message = update.get("message", {})
        text = message.get("text", "")
        if text.startswith("EDIT:"):
            _, rest = text.split(":", 1)
            edit_page_id, corrected = rest.split(" ", 1)
            if edit_page_id in checkpoint["pending_review"]:
                checkpoint["pending_review"][edit_page_id]["draft"] = corrected
                telegram_send(f"Updated. Reply Approve/Deny again when ready.")

# ---- Phase 4: Android Results Processing ----
def process_android_results_phase():
    """Read and process completed Android queue results."""
    results = read_android_results()
    if results:
        print(f"[ANDROID] Processing {len(results)} completed actions")
        process_android_results(results)
    else:
        print("[ANDROID] No completed results")

def main():
    if not (COMPOSIO_API_KEY and DB_IDS["Leads"]):
        print("Missing required env vars - see 05_Integrations/Active_Setup.md")
        print("Required: COMPOSIO_API_KEY, NOTION_DB_LEADS, NOTION_DB_MEETINGS, etc.")
        return

    checkpoint = load_checkpoint()
    ctx = load_context()

    print(f"\n=== HERMES DAILY RUN — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    print(f"Context loaded: {len([k for k,v in ctx.items() if v])} warm files")

    # Phase 1: Prospecting (Tier 1)
    run_prospecting(ctx)

    # Phase 2: Outreach Drafting (Tier 1 draft)
    run_outreach_drafting(ctx, checkpoint)

    # Phase 3: Process Approvals (Tier 2)
    process_approvals(checkpoint)

    # Phase 4: Android Results (read completed Android actions)
    process_android_results_phase()

    save_checkpoint(checkpoint)
    telegram_send(f"Hermes run complete — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Done.")

if __name__ == "__main__":
    main()