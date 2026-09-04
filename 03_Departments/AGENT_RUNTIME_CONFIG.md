# Agent Runtime Config — CraftedWorkflowsOS

*Generated from existing 03_Departments docs. Maps each employee (already authored)
to a runtime mechanism. Adds configuration ONLY — no org changes, no profile
creates, no doc edits. Grounded in `05_Integrations/Provider_Capability_Registry.md`
and `05_Integrations/Active_Setup.md`.*

---

## Architecture Decision

**One Hermes profile per department. Director = the profile identity. Employees =
subagents (`delegate_task`) or cron jobs (`cronjob`), never separate profiles.**

Rationale (from `Active_Setup.md` + device constraints in `06_Android/config.json`):
- All integrations are API-key-auth at profile level (Zernio, Composio, Arcade_X).
  Subagents inherit the parent's tools; separate profiles would need re-auth/dup keys.
- Infinix Smart 6 / 2GB RAM: N profiles = N× memory+context tax. One profile per
  department is the only layout that survives the device.
- Every employee's escalation chain is already `Specialist → Director → COO → CEO`,
  which is exactly `subagent → profile → this session`.

---

## LEGEND

| Tag | Mechanism | Hermes primitive |
|-----|-----------|------------------|
| `SUBAGENT` | Spawned on-demand by Director/COO; isolated context, returns summary | `delegate_task` |
| `CRON` | Scheduled autonomous run; fresh session; output to local | `cronjob` |
| `SUBAGENT+CRON` | Both: on-demand for variable work + scheduled for the cadence | both |

Classification keys (must meet ALL to be `CRON`): fixed cadence (daily/weekly/
monthly), no founder-approval loop in the path, output is a file/DB-write not a
live conversation.

---

## DEPARTMENT MAP

### Commercial — Director profile: `commercial`
9 employees, all `SUBAGENT` (daily outbound engine, sequential handoffs, one pipeline)

| Employee | Mechanism | Capabilities (from Profile.md) | Provider route |
|----------|-----------|-------------------------------|----------------|
| Lead Intelligence | SUBAGENT | research, analysis, writing | browser (LI/X/web) → Firecrawl/Tavily/EXA |
| Prospecting | SUBAGENT | analysis, writing | Notion (leads DB) |
| Outreach | SUBAGENT | writing, analysis | LI DM = Android (Gap #1); X DM = Android (Gap #2) |
| Discovery | SUBAGENT | analysis, writing | Notion (meetings DB) + Gmail |
| Proposal | SUBAGENT | writing | Notion + Gmail |
| Pipeline | SUBAGENT | analysis | Notion (leads/clients DB) |
| Sales Engineer | SUBAGENT | analysis, writing | Notion + web |
| Account Strategist | SUBAGENT | analysis, writing | Notion (clients DB) |
| Deal Strategist | SUBAGENT | analysis, writing | Notion + web |

> Existing runtime: `runtime/hermes_daily.py` already executes Prospecting → Outreach
  (Tier1/Tier2). The 9 subagents above are the *decision-altitude* decomposition of
  that same pipeline; `hermes_daily.py` remains the batch executor. No conflict —
  subagents make the calls, the batch script does the mechanical CRM I/O.

### Creative — Director profile: `creative`
5 employees, all `SUBAGENT` (project-based, no fixed cadence)

| Employee | Mechanism | Capabilities |
|----------|-----------|--------------|
| Brand_Designer | SUBAGENT | design, analysis |
| UI_UX_Designer | SUBAGENT | design |
| Web_Experience_Designer | SUBAGENT | design |
| Motion_Video_Designer | SUBAGENT | design |
| Visual_Content_Designer | SUBAGENT | design |

> Note: `design` capability → Figma. Figma is NOT in the Provider_Capability_Registry's
  verified list. Until Figma is connected, Creative subagents draft specs/text-first;
  production assets need the Figma route added (not in scope of this config).

### Delivery — Director profile: `delivery`
6 employees, all `SUBAGENT` (client-triggered work)

| Employee | Mechanism | Capabilities |
|----------|-----------|--------------|
| Client_Onboarding_Specialist | SUBAGENT | writing, analysis |
| Client_Success_Manager | SUBAGENT | writing, analysis |
| Delivery_Quality_Engineer | SUBAGENT | analysis |
| Expansion_Referral_Specialist | SUBAGENT | analysis, writing |
| Project_Manager | SUBAGENT | writing, analysis |
| Technical_Delivery_Lead | SUBAGENT | analysis, writing |

### Engineering — Director profile: `engineering`
6 employees: 5 `SUBAGENT`, 1 `SUBAGENT+CRON`

| Employee | Mechanism | Capabilities | Cadence |
|----------|-----------|--------------|---------|
| Architect | SUBAGENT | analysis, writing | — |
| Backend | SUBAGENT | writing, analysis | — |
| Frontend | SUBAGENT | writing, analysis | — |
| DevOps | SUBAGENT+CRON | writing, analysis | deploys / infra health |
| QA | SUBAGENT | writing, analysis | — |
| Documentation | SUBAGENT | writing | — |

### Finance — Director profile: `finance`
6 employees: 4 `SUBAGENT`, 2 `CRON`

| Employee | Mechanism | Capabilities | Cadence |
|----------|-----------|--------------|---------|
| Revenue_Operations_Specialist | SUBAGENT | analysis | — |
| Pricing_Profitability_Analyst | SUBAGENT | analysis | — |
| Billing_Invoicing_Specialist | CRON | analysis, writing | daily invoice + collection scan |
| Cash_Flow_Forecasting_Analyst | CRON | analysis, writing | daily cash pos / weekly revenue / monthly burn |
| Financial_Controller | SUBAGENT | analysis | month-end close (event-driven) |
| Executive_Finance_Analyst | SUBAGENT | analysis, writing | board reporting (on-request) |

> Finance cron jobs are **detect/propose only** — they write forecast/burn artifacts
  to `memory/working/finance/` and escalate per the Cash_Flow profile's escalation
  table (runway < 12wk → Director, < 8wk → COO). No irreversible action is automated.

### Marketing — Director profile: `marketing`
7 employees: 6 `SUBAGENT`, 1 `SUBAGENT+CRON`

| Employee | Mechanism | Capabilities | Cadence |
|----------|-----------|--------------|---------|
| Founder_Brand_Architect | SUBAGENT+CRON | writing, analysis | weekly content calendar |
| Content_Strategist | SUBAGENT | writing, analysis, research | — |
| Content_Repurposing_Operator | SUBAGENT | writing | — |
| Email_Nurture_Specialist | SUBAGENT | writing | — |
| Authority_PR_Builder | SUBAGENT | writing, research | — |
| Lead_Magnet_Designer | SUBAGENT | writing | — |
| Sales_Enablement_Content | SUBAGENT | writing | — |

> Founder Brand Architect stays `SUBAGENT` *primary* (founder approval loop in
  `pending_approval` memory is a conversation, not a fire-and-forget write). CRON is
  secondary: post-performance analysis only. Never auto-publish founder content.

### Operations — Director profile: `operations`
6 employees: 4 `SUBAGENT`, 2 `CRON`

| Employee | Mechanism | Capabilities | Cadence |
|----------|-----------|--------------|---------|
| Automation_Internal_Tools_PM | SUBAGENT | writing, analysis | — |
| Automation_Systems_Coordinator | SUBAGENT | writing, analysis | — |
| SOP_Documentation_Librarian | SUBAGENT | writing | — |
| Internal_Communications_Rhythm_Manager | SUBAGENT | writing, analysis | — (crisis/urgent routes have approval gate → conversation) |
| Planning_Rhythm_Coordinator | CRON | writing, analysis | weekly / monthly / quarterly |
| Quality_Reliability_Engineer | CRON | analysis | hourly metrics / incident detect |

---

## CRON JOB DEFINITIONS (schedule only — no agent logic added here)

Final count: **45 employees** = 39 subagents + 4 cron-only + 2 dual (DevOps, Founder Brand).
Total cron jobs: **4** (2 finance + 2 operations). DevOps/Founder Brand cron is situational, not a scheduled job here.

| Job name | Schedule | Fires | Mechanism |
|----------|----------|-------|-----------|
| `finance-cash-forecast` | every day 08:00 | Cash_Flow_Forecasting_Analyst profile | CRON, local |
| `finance-billing-scan` | every day 09:00 | Billing_Invoicing_Specialist profile | CRON, local |
| `ops-quality-reliability` | every hour | Quality_Reliability_Engineer profile | CRON, local |
| `ops-planning-rhythm` | every monday 09:00 | Planning_Rhythm_Coordinator profile | CRON, local |

> These are DETECTION/REPORTING jobs. Each writes artifacts and escalates per the
  employee profile's escalation rules. They are deliberately NOT wired to `deliver`
  — they are local (this session cannot receive live delivery; see gateway notes).

---

## OWN-PROFILE ELEVATION GATE

Promote an employee from `SUBAGENT` → own profile ONLY when ALL THREE hold:
1. Separate credentials (different API key/workspace than their department).
2. Persistent role-specific memory that must be isolated from peers.
3. Independently addressable by external parties without Director mediation.

Current assessment: **none of the 44 employees meet all three.** First likely
candidates if it ever changes: Founder_Brand_Architect (if founder personal LI/X
accounts move to dedicated auth), DevOps (if a separate cloud account is provisioned).

---

## NOT CHANGED (by design)

- 44 employee `Profile.md` files — untouched
- 7 `*_Director.md` files — untouched
- `DIRECTOR_STANDARDS.md`, `ORG_CHART.md`, `Executive_Team.md` — untouched
- `runtime/hermes_daily.py`, `06_Android/*`, `05_Integrations/*` — untouched
- No new Hermes profiles created
- No cron jobs created yet (see next step — requires gateway for delivery)