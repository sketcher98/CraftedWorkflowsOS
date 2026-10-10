# CraftedWorkflowsOS

The operating system for CraftedWorkflows — an AI systems company that helps founders build businesses that run through systems instead of constant human effort.

---

## Quick Links

- [Work Delivery Standards](docs/standards/work-delivery.md) — Canonical rules for all work performed by/through Hermes
- [COO Identity](02_COO/identity.md) — Hermes role, mission, philosophy, authority
- [Company State](02_COO/company_state.md) — Current reality: revenue, bottlenecks, priorities, integrations
- [Execution Charter](00_System/execution_charter.md) — Tier 1/2/3 authority, what executes vs. what waits
- [Mission Loop](00_System/mission_loop.md) — Leverage pre-flight: pick the right work, then clear the path
- [Decision Principles](01_CEO/decision_principles.md) — 10 principles governing revenue, automation, execution, founder, technology
- [Director Standard](03_Departments/DIRECTOR_STANDARDS.md) — Operating model for every department director

---

## System Structure

```
CraftedWorkflowsOS/
├── 00_System/           # Core operating logic
│   ├── execution_charter.md
│   ├── mission_loop.md
│   ├── boot_sequence.md
│   ├── cache_rules.md
│   └── refresh_policy.md
├── 01_CEO/              # Company constitution
│   ├── company.md
│   └── decision_principles.md
├── 02_COO/              # COO operations
│   ├── identity.md
│   └── company_state.md
├── 03_Departments/      # Department playbooks
│   ├── DIRECTOR_STANDARDS.md
│   ├── ORG_CHART.md
│   ├── Executive_Team.md
│   └── AGENT_RUNTIME_CONFIG.md
├── 04_Knowledge/        # Reference knowledge base
│   └── README.md
├── 05_Integrations/     # Live integration configs
│   ├── Active_Setup.md
│   ├── Notion_Workspace_Schema.md
│   └── Provider_Capability_Registry.md
└── docs/
    └── standards/
        └── work-delivery.md    # <-- Canonical work rules (this doc links here)
```

---

## Active Department

**Commercial** — Revenue engine. Generates predictable revenue, protects pipeline health, maximizes qualified conversations, increases client acquisition.

See `03_Departments/Commercial/` for active playbooks.

---

## Revenue Goal

**$10,000/month** | Current: $0

Active offers:
- **System Jumpstart** — $1,497 one-time
- **Install Self-Running Revenue** — $3,497 one-time (most popular)
- **Remove Founder from Daily Ops** — $4,997/month (3-month minimum)

---

## Integrations (Live)

| Platform | Via | Status |
|----------|-----|--------|
| Notion | Composio | 5 linked DBs: Leads, Clients, Meetings, Projects, Tasks |
| Gmail (send) | Composio | Active |
| 70+ SaaS tools | Composio | On-demand |
| LinkedIn + Instagram | Zernio (HTTP MCP) | Post/DM drafts; sends = Tier 2 |
| X/Twitter | twitter_mcp (stdio) | 62 tools, @PreciousNwosu_ |

---

## Operating Mode

Target: 80–90% hands-off. CEO reviews Tier 2 (Telegram approve/edit/deny) and handles Tier 3. Everything else runs unattended.

Dispatcher: 60s tick. `max_in_progress: 1` (2GB RAM device).

---

## Hermes Agent Profile

**Active profile:** `commercial`  
**Data root:** `~/.hermes/profiles/commercial/`  
**Skills auto-loaded:** `provider-registry`, `craftedworkflows-coo`, `termux-safety`, `craftedworkflows-marketing`

---

## Kanban Cockpit

```bash
cw              # TUI (full board)
cw quick        # One-shot commands
cw nudge        # Force dispatch tick
```

---

## Safety First

This system runs on Android/Termux/ARMv7 with Python 3.13. The `termux-safety` skill (merged: stop-condition + installation-safety + config-edits + backup-recovery) governs all environment changes. **Hard stop rules apply.** See `docs/standards/work-delivery.md` Sections 3 and 9.

---

*Built by Hermes (COO) for Precious Nwosu (CEO).*