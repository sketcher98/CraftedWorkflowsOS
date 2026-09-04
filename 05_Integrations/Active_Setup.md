# Integrations — Active Setup

All integrations are **already configured and verified**. Do NOT re-install,
re-authorize, or rebuild what is listed as active below.

---

## 1. Composio (70+ SaaS tools — the general workhorse)

**Status:** ✅ ACTIVE — 70 active connections across 68 toolkits
**Cache:** `~/.hermes/cache/composio_active_connections.json` (pre-fetched; don't re-poll)
**Use:** Notion (CRM read/write), Gmail (send email drafts on Tier-2 approve),
Drive, Slack, Calendar — any SaaS the task needs.

**Account user id:** `precious-cw` (already OAuth'd)

### How Hermes uses it
Native MCP calls: `mcp__composio__*` — do NOT use the raw Python SDK unless
explicitly requested. Native MCP is already wired into Hermes.

### Common action slugs (verify before assuming)
- `NOTION_QUERY_DATABASE`
- `NOTION_UPDATE_PAGE`
- `NOTION_CREATE_PAGE`
- `NOTION_SEARCH`
- `GMAIL_SEND_EMAIL`
- `GMAIL_FETCH_EMAILS`

If a slug 404s, list available tools first — Composio renames slugs over time.

---

## 2. Zernio (LinkedIn + Instagram)

**Status:** ✅ ACTIVE — both accounts connected
**Accounts:** LinkedIn `Precious Nwosu`, Instagram `craftedworkflows_`

**⚠️ USE THE SKILL, not raw stdio MCP.**
Load `hermes-business-ops` skill — it wraps the Zernio wrapper at
`~/zernio-runtime/bin/zernio-mcp-stdio` correctly.

### What runs through Zernio here
- LinkedIn DM drafting + sending (Tier 2 approval gate)
- Instagram DM drafting + sending (Tier 2 approval gate)
- Unified inbox reads (Tier 1)
- Social posting (drafted Tier 1, sent Tier 2)

---

## 3. Arcade_X (X/Twitter)

**Status:** ✅ ACTIVE — 39 tools verified (2026-08-27)
**Account:** `@PreciousNwosu_`
**Scopes:** tweet.read, users.read, follows.read, space.read, tweet.write,
like.write, retweet.write, list.read, list.write

**Mechanism:** Hermes native MCP — `mcp__arcade_x__*`. No wrapper needed.
**Re-auth:** `hermes mcp reauth arcade_x` if scopes change.

### What runs through Arcade_X here
- Tweet reads, search, user list reads (Tier 1 — autonomous)
- Tweet posting, retweets, likes (drafted by playbook; posted per execution
  charter — X is lower-risk than LinkedIn so tweet writes are Tier 1 for
  approved content; DMs remain Tier 2)

---

## 4. Notion Workspace — CraftedWorkflows (LIVE CRM)

**Status:** ✅ PROVISIONED — Workspace page exists, 5 linked DBs are live
**Layout:**
- Leads
- Clients
- Meetings
- Projects
- Tasks

All databases linked by relation per `Notion_Workspace_Schema.md`.
Database IDs stored in env vars (read from `~/.bashrc`):
`NOTION_DB_LEADS`, `NOTION_DB_MEETINGS`, `NOTION_DB_CLIENTS`,
`NOTION_DB_PROJECTS`, `NOTION_DB_TASKS`, `NOTION_PARENT_PAGE_ID`.

Hermes reads/writes via Composio `NOTION_*` actions.

---

## 5. Android Execution Layer (Termux:API + Tasker + AutoInput)

**Status:** 🟡 CODE READY — Deploy to Infinix Smart 6 (Android 11)

This is the **execution layer for API gaps** — only 4 operations need Android UI:
1. **LinkedIn DM send** — Zernio has no direct DM API
2. **X/Twitter DM send/reply** — Arcade X has no DM tool; cookie MCPs rejected
3. **X/Twitter media tweet** — Arcade X lacks media upload
4. **X/Twitter follow/unfollow** — Arcade X lacks follow tool

### Architecture
```
HERMES (Pipeline Intelligence)
    │
    ├─► Tier 1: SOURCE → SCORE → ASSIGN FLOW → DRAFT (Notion)
    │
    ├─► Tier 2: CEO APPROVES in session
    │
    ├─► If API can send (Zernio LI/IG post, Arcade X tweet, Composio email):
    │       → Execute native MCP → Update Notion "Sent" ✅
    │
    └─► If API CANNOT (LI DM, X DM, X media, X follow):
            → Write queue/xxx.json (file-based, restart-safe)
                    ↓
            TASKER (File monitor) → AUTO INPUT (UI) → RESULT/xxx.json
                    ↓
            HERMES (next run) → Reads results → Updates Notion → Deletes result file
```

### File Structure (in `~/CraftedWorkflowsOS/06_Android/`)
```
06_Android/
├── queue/                    # Pending Android actions (JSON files)
├── results/                  # Completed results (Hermes reads these)
├── state/
│   ├── last_run.json
│   ├── rate_limits.json      # Daily counters per action type
│   └── android_layer.log
├── scripts/
│   ├── process_queue.py      # Termux: called by Tasker (rate limits)
│   ├── linkedin_dm.py        # AutoInput flow for LI DM
│   ├── x_dm.py               # AutoInput flow for X DM
│   ├── x_media_post.py       # AutoInput flow for X media tweet
│   └── x_follow.py           # AutoInput flow for X follow
├── tasker/
│   ├── Android_Queue_Processor.tsk.xml
│   ├── LinkedIn_DM_Flow.tsk.xml
│   ├── X_DM_Flow.tsk.xml
│   ├── X_Media_Post_Flow.tsk.xml
│   └── X_Follow_Flow.tsk.xml
├── termux_boot/
│   └── boot.sh               # Termux:Boot → starts queue watcher
└── config.json               # All selectors, delays, caps, spintax
```

### Rate Limits (Hard-coded, per day)
| Action | Limit | Min Delay | Max Delay | Active Hours |
|--------|-------|-----------|-----------|--------------|
| LinkedIn DM | 5 | 15 min | 45 min | 08:00-23:00 |
| X DM | 5 | 10 min | 30 min | 08:00-23:00 |
| X Follow/Unfollow | 10 | 5 min | 20 min | 08:00-23:00 |
| X Media Tweet | 10 | 10 min | 30 min | 08:00-23:00 |

### Recovery Strategy (All 4)
- Queue file persists → survives reboot (file-based state)
- Tasker reads queue on Termux:Boot → processes sequentially
- Toast detection: "rate limited" / "something went wrong" → pause 1hr, retry
- Result file written → Hermes reads on next run, updates Notion
- Hard caps enforced in `config.json` + `process_queue.py`

### Deployment Checklist
- [ ] Termux:API app installed
- [ ] Termux:Boot app installed
- [ ] Tasker app installed (paid)
- [ ] AutoTools plugin installed
- [ ] AutoInput plugin installed
- [ ] Copy `06_Android/tasker/*.tsk.xml` to Tasker (import)
- [ ] Enable Tasker "Accessibility Service" for AutoInput
- [ ] Grant Termux:API permissions in Android settings
- [ ] Run `chmod +x 06_Android/termux_boot/boot.sh`
- [ ] Test one queue item manually: `python3 06_Android/scripts/linkedin_dm.py <queue_file>`

---

## 6. LLM — No Groq

Hard rule: **never use Groq** as the model backend inside Hermes. Its free
tier TPM is unusable in-agent (crashes on trivial calls). Hermes's own
configured model is the only LLM call allowed for reasoning/drafting.

For cheap scoring batch jobs (ICP scoring, reply classification), use the
Hermes model directly — it's already paid-for per-token in this session.
Don't add a second inference provider.

---

## 7. Telegram (approval channel — OPTIONAL later)

Not required for Commercial department v1. Approval gate currently happens
in this Hermes session. Add Telegram bot later if you want unattended
nightly runs.

---

## Hard Don'ts

- ❌ Do NOT `pip install` anything without explicit user approval
- ❌ NEVER install Python 3.14 — it breaks Termux Hermes
- ❌ Don't reauthorize Composio/Zernio/Arcade_X unless tokens actually fail
- ❌ Don't recreate Notion databases — they're live
- ❌ Don't switch the LLM provider implicitly