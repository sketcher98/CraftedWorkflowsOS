# Notion Workspace Schema

Live CraftedWorkflows Crm. All 5 databases relation-linked. Hermes reads and
writes via Composio `NOTION_*` actions. **Database IDs live in env vars in
`~/.bashrc`.**

---

## Leads

| Property | Type | Notes |
|---|---|---|
| Name | title | |
| Company | rich_text | |
| Title | rich_text | |
| Email | email | |
| LinkedIn | url | |
| Status | select | New, Queued, Drafted, Sent, Replied, Booked, Won, Lost, Archived |
| Source | select | LinkedIn Comment Mining, LinkedIn Search, X Search, X Comment Mining, Referral, Inbound |
| ICP Score | number | 0-100 (computed by Lead Intelligence Specialist) |
| Flow Assigned | select | Flow 1, Flow 2, Flow 3, Flow 4, Flow 5 |
| Channel | select | LinkedIn, X, Email |
| Date Added | date | |
| → Meetings | relation | |
| → Client | relation | if converted |

## Clients

| Property | Type | Notes |
|---|---|---|
| Name | title | |
| Company | rich_text | |
| Status | select | Active, Paused, Churned |
| Tier | select | Jumpstart, Goldilocks, Visionary |
| Monthly Value | number | |
| Start Date | date | |
| → Projects | relation | |
| → Meetings | relation | |
| → Origin Lead | relation | |

## Meetings

| Property | Type | Notes |
|---|---|---|
| Title | title | |
| Date | date | |
| Type | select | Discovery, Delivery, Check-in |
| Outcome | rich_text | |
| → Lead | relation | |
| → Client | relation | |
| → Follow-up Tasks | relation | |

## Projects

| Property | Type | Notes |
|---|---|---|
| Name | title | |
| Status | select | Planning, Active, Delivered, Paused |
| Start Date | date | |
| Deadline | date | |
| → Client | relation | |
| → Tasks | relation | |

## Tasks

| Property | Type | Notes |
|---|---|---|
| Title | title | |
| Status | select | To Do, In Progress, Done, Blocked |
| Owner | select | Hermes, CEO |
| Priority | select | Urgent, High, Normal, Low |
| Due Date | date | |
| → Project | relation | |
| → Meeting | relation | |
| → Lead | relation | |

---

## Who writes what

- **Prospecting Specialist** → creates/updates Leads (Status, ICP Score, Flow)
- **Outreach Specialist** → updates Leads (Sent/Replied), creates Meetings
  on booked calls
- **Discovery Specialist** → updates Meetings (Outcome), creates follow-up Tasks
- **Hermes (COO)** → creates Tasks from blockers, updates `company_state.md`,
  moves items in/out of Inbox
- **CEO (Precious)** → moves Clients/Projects forward, signs off Tier 2 sends