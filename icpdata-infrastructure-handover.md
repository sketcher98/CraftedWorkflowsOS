# MHC Pipeline Infrastructure Handover

**Pipeline location:** `/data/data/com.termux/files/home/icpdata`  
**Database:** `data/mhc_registry.db` (99 prospects tracked)  
**Date:** 2026-10-20

---

## Architecture Overview

```
Twitter/X Sources → Ingest → MHC v6 Classification → Notion Sync → Auto-DM → Followup Engine
                                    ↓
                            Daily Human Review Gate
```

---

## Core Scripts (per-file breakdown)

### `scripts/notion_sync.py` (309 lines)
**Purpose:** Sync prospects from mhc_registry to 5 Notion databases (Leads, Clients, Meetings, Projects, Tasks).

- **DB IDs hardcoded:** Leads/clients/meetings/projects/tasks
- **Key functions:**
  - `sync_prospect_to_notion(conn, registry_id)` - Creates/updates lead in Notion
  - `update_lead_status()` - Updates status (New → DM Sent → Replied)
  - `on_dm_sent()` / `on_reply_received()` - Updates Notion when DM/reply events occur
- **Self-correcting:** Stores `notion_page_id` back to registry to avoid re-syncs

### `scripts/notion_sync_wrapper.py` (62 lines)
**Purpose:** Cron-safe wrapper with job_ledger guard and bounded batches.

- Runs every 2 hours
- Syncs only `notion_page_id IS NULL` rows (max 30 per run)
- Calls `job_ledger.should_run()` to prevent duplicate runs within time window
- Marks synced rows with `notion_synced_at`

### `scripts/tally_poller.py` (315 lines)
**Purpose:** Poll Tally form (3jrBM1) for leads, classify via MHC v6, sync to Notion.

- **Question IDs:** 9ZJg1X (name), erxjAQ (email), 08bQg9 (business_type), etc.
- **Flow:** Composio TALLY_GET_FORM_RESPONSES → extract_metrics() → run_mhc() → upsert_db()
- **State:** Tracks last_submission_id in `data/tally_poller_state.json`
- **Note:** Currently incomplete - needs Composio integration to actually fetch responses

### `scripts/tally_poller_wrapper.py` (105 lines)
**Purpose:** Cron wrapper for Tally poller.

- Runs every 15 min (0.25 hours)
- Tries direct Tally API first; falls back to needing Composio
- Calls `tally_poller.process_submissions()` with fetched data

### `scripts/watchdog.py` (194 lines)
**Purpose:** Gateway health monitor (runs every 15 min) and job catch-up.

- **Health checks:**
  1. `pgrep -f 'gateway run --force'` for process presence
  2. `curl` to `http://127.0.0.1:8642/v1/models` for API responsiveness
- **Actions:**
  - If gateway dead → restart via `gateway-start` → wait 120s for port 8642
  - Alerts Telegram on restart/failure
  - Runs catch-up for missed jobs (nightly_ingest, notion_sync, auto_sender)

### `scripts/dashboard.py` (119 lines)
**Purpose:** One-command pipeline status dashboard.

- Shows funnel: prospects → replies → DMs → Notion sync
- Queue states by route (auto/human/ghosted_recovery)
- Today's activity vs daily caps (6 replies, 5 DMs)
- Follow-back watchlist (replied+followed, DM not yet deliverable)
- Recent activity timeline

### `scripts/daily_loop.py` (102 lines)
**Purpose:** Full daily pipeline run (manual/one-shot).

**Stages:** ingest → reply → dm → followups → sync → dashboard
- Filtered out HTTP log noise
- Supports `--skip <stage>` and `--only <stage>`

### `scripts/job_ledger.py` (82 lines)
**Purpose:** Track job run times to enable self-healing.

- **Table:** `job_runs(job_name TEXT PRIMARY KEY, last_run_at TEXT, last_result TEXT)`
- `should_run(conn, job, every_hours, once_per_day)` - Returns True if due
- `mark_run(conn, job, result)` - Updates last_run_at
- Enables jobs to catch up even if gateway was down during scheduled window

### `scripts/install_cron.py` (93 lines)
**Purpose:** Install/remove/show cron jobs.

**Schedule:**
- Ingest: every 4 hours (pain/growth searches)
- Auto-sender: every 2 hours (9am-9pm)
- Notion sync: every 2 hours
- Watchdog: every 15 minutes
- Weekly unfollow: Sunday 9am
- Daily emotion rotation: 7am

### `scripts/composio_env.py` (59 lines)
**Purpose:** Single-source credential loading for Composio.

- Loads from `config/composio.env` (COMPOSIO_API_KEY, COMPOSIO_USER_ID, COMPOSIO_MCP_URL)
- Precedence: os.environ → config file
- All scripts call `load_composio_env()` at startup

### `scripts/schema_patch.sql` (33 lines)
**Purpose:** Additive schema extensions for mhc_registry.

**Added columns:**
- `source_type`, `source_post_*` (discovery evidence)
- `enriched_*` (twitter_mcp profile lookup)
- `qualification_*` (MHC v6 full-evidence scoring)
- `notion_page_id`, `last_synced_at` (Notion sync tracking)
- Indexes for source_type, source_post_author, qualification_score, notion_page_id

---

## CLI Tools (in `cli/`)

| Command | Purpose |
|---------|---------|
| `mhc` | Interactive dashboard menu (status, run, review, cron, logs) |
| `mhc-run` | Full daily pipeline (shortcut) |
| `mhc-review` | Human gate - review queued prospects one-at-a-time |
| `mhc-cron` | Interactive cron control (enable/disable/status) |
| `mhc-status` | Dashboard only (no menu) |
| `mhc-logs` | Tail pipeline activity log (auto_sender_run.log) |

**Key paths:**
- Python venv: `~/.hermes/hermes-agent/venv/bin/python`
- LD_PRELOAD: `/data/data/com.termux/files/usr/lib/libpython3.13.so`
- Twitter MCP: `~/.hermes/twitter-mcp`, `~/.config/twitter-mcp/cookies.json`

---

## Databases

### `data/mhc_registry.db` (192KB, 99 rows)
**Core table:** `mhc_registry` - prospect lifecycle tracking

**Key columns:**
- `handle`, `bio`, `icp_score`, `tags`, `confidence`, `route`
- `source`, `source_type`, `source_post_*` (discovery evidence)
- `queue_position` (DM order)
- `qualification_*` (MHC v6 full-evidence)
- `dm_sent_at`, `dm_flow`, `dm_text`, `dm_error`
- `reply_first_at`, `reply_text`, `public_reply_text`
- `notion_page_id`, `last_synced_at`
- `followup_*`, `email_*` (multi-channel)

**Related tables:**
- `job_runs` - cron job execution tracking

### `accounts.db` (12KB, 1 row)
**Purpose:** Twitter account credentials.

**Schema:** `accounts(username, password, email, email_password, user_agent, active, locks, headers, cookies, proxy, error_msg, stats, last_used, _tx, mfa_code)`

**Note:** Password/email_password are stored in plaintext. Used by MCP/auth flows.

### `data/twscrape.db` (0 bytes - empty)
**Purpose:** Placeholder for Twitter scraping session data. Currently unused.

---

## Hermes Cron Jobs

| Job Name | ID | Status |
|----------|-----|--------|
| MHC Nightly Ingest | 310983658b10 | ✅ Enabled |
| MHC Reply-First Daily | 0b7e0cfa9d41 | ⏸️ Disabled |
| MHC Auto-Sender (DM) | cd79ed8912db | ⏸️ Disabled |
| MHC Followup Engine | f3e9e1df8744 | ⏸️ Disabled |
| MHC Notion Sync | 00d7df34ef46 | ⏸️ Disabled |

**Control:** `mhc-cron enable|disable|status`

---

## Configuration Files

### `config/composio.env`
- **Purpose:** Single source of truth for Composio credentials
- **Contains:** COMPOSIO_API_KEY, COMPOSIO_USER_ID, COMPOSIO_MCP_URL
- **Never committed:** Listed in .gitignore

### `.gitignore`
- Ignores: `__pycache__/`, `*.pyc`, `logs/`, `accounts.db`, `data/twscrape.db`

---

## Key Workflows

### Lead Ingestion
1. Twitter/X search & comment mining → `nightly_ingest.py` (not read, but referenced)
2. Enrichment via `quick_batch.py` (tweet context)
3. MHC v6 classification: `mhc_classifier_v6.py` (not read, but imported)

### DM Gate (476-gated)
- Auto-sender sends DMs **only to followers**
- Gate enforced at `auto_sender.py` (not read)
- 5/DM cap enforced by job_ledger

### Notion Sync
- Prospect → sync_prospect_to_notion() → Creates lead in Notion Leads DB
- DM/reply events → update_lead_status()
- Tracks via `notion_page_id` field

---

## Operational Notes

**Gateway dependency:** All active cron jobs require gateway to be running. Check with:
```bash
ls ~/.hermes/.runtime/gateway.pid  # should exist
curl http://127.0.0.1:8642/v1/models  # should return 200/401
```

**Self-healing:** watchdog.py catches up missed jobs if gateway was down.

**Manual gates:**
- Review prospects: `mhc review`
- Control cron: `mhc-cron`

**Logs:** `~/icpdata/logs/auto_sender_run.log`

---

## Missing/Unread Files (for future investigation)

- `scripts/nightly_ingest.py`
- `scripts/reply_first.py`
- `scripts/auto_sender.py`
- `scripts/followup_engine.py`
- `scripts/review_cli.py`
- `scripts/quick_batch.py`
- `scripts/mhc_classifier_v6.py`
- `scripts/emotion_scheduler.py`
- `scripts/follow_manager.py`
- `scripts/dream100.py`
- `scripts/comment_mine_v3.py`

---

## Handover Summary

**What this is:** Twitter prospecting pipeline that:
1. Ingests prospects from Twitter searches/comments
2. Classifies them with MHC v6
3. Auto-DMs followers (5/day cap)
4. Syncs to Notion
5. Tracks multi-channel followups

**Self-healing design:** watchdog + job_ledger enable catch-up even if gateway goes down.

**Manual intervention points:** Daily review gate (`mhc review`) + weekly unfollow.
