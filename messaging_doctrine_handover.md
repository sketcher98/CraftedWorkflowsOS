# Outreach Messaging Doctrine — Handover Breakdown

**What this is:** The playbook for how CraftedWorkflowsOS automates personalized direct messages to agency owners and founders. Five distinct DM flows, each with a specific psychological angle.

---

## 1. The 5 DM Flows (The Playbook)

| Flow | Angle | Voice | Best For |
|------|-------|-------|----------|
| **Founder Mirror** | "You feel seen, not sold to" | Observant, empathetic, zero pitch | Agency owners who've expressed pain about being overwhelmed, delivery, hiring, or time |
| **Permission Direct** | "Control + respect" | Direct, high-status, asks permission | Verified/high-revenue prospects who get pitched often |
| **Value First** | "Reciprocity" | Genuine curiosity, gives first | Small agencies, manual operators, time complaints |
| **Conversational** | "Casual, human, non-sales" | Peer-to-peer, Twitter-native | Creators, active engagers, community people |
| **Risk Audit** | "Loss aversion" | Diagnostic, authoritative pattern interrupt | Established agencies with teams, hiring signals, ops-heavy |

---

## 2. The Trigger Tags (What Routes Prospects)

### Flow Routing Rules (from `flow_router.py`)
**v2 Routing (rich evidence):** Uses comment text, source_post_author authority hints, and enriched profile data. Falls back to v1 when missing.

**v1 Routing (tags + bio only):**

| Tags / Signals → Flow | Founder Mirror | Permission Direct | Value First | Conversational | Risk Audit |
|----------------------|----------------|-------------------|-------------|----------------|------------|
| `agency_owner`, `founder`, `consultant`, `firm_owner`, `owner` | +2 | | | | |
| `verified`, `high_revenue`, `7-figure`, `6-figure`, `revenue` | | +2 | | | |
| `small_agency`, `manual_operator`, `overwhelm`, `delivery`, `process`, `hiring` | | | +2 | | |
| `creator`, `twitter_active`, `content_creator` | | | | +2 (+5 if "content creator") | |
| `established_agency`, `has_team`, `ops`, `employee`, `hiring` | | | | | +2 |
| `beginner`, `info_products_only`, `hype_only` | -5 | | | | |
| `enterprise`, `outsources_everything`, `only_wants_strategy` | | | -3 | | |
| `solo_early`, `no_team`, `no_ops` | | | | | -3 |
| Revenue signals ($10k+, 6/7-figure) | | +3 | | | +2 |
| Team signals (team, employee, hiring, staff) | +1 | | | | +3 |

---

## 3. The Messaging Sequences (Playbook Docs)

### Content Signal — 2-Touch
- **Touch 1 (Day 0):** "Saw your thread on [topic] — sharp take on [specific point]." → Value link to 1-pager
- **Touch 2 (Day 4):** Soft close. "If you implement and hit a wall, DM me. Otherwise, keep building."
- **Tags:** `content_signal`, `thread_author`, `educational_content`, `implementation_gap`

### Ghosted Lead Recovery — 3-Touch
- **Touch 1 (Day 0):** Audit hook. "Happy to show you the 3-step audit we run (takes 15 min). No pitch — just the gaps."
- **Touch 2 (Day 3):** Value add. 1-pager on top 5 proposal-to-deposit leaks
- **Touch 3 (Day 7):** Soft close. "reply 'sprint' and I'll send the brief"
- **Tags:** `ghosted`, `follow_up`, `proposal_leakage`, `manual_process`

### Hiring Signal — 3-Touch
- **Touch 1 (Day 0):** Founder bottleneck framing. "Most agencies hiring [role] at your stage are actually trying to solve: 'our delivery is bottlenecked on the founder.'"
- **Touch 2 (Day 3):** Proof via case study
- **Touch 3 (Day 7):** Workshop invite. "reply 'workshop'"
- **Tags:** `hiring`, `scaling`, `delivery_bottleneck`, `founder_in_delivery`

### Risk Audit — 3-Touch
- **Touch 1 (Day 0):** Audit hook. "Happy to run the 3-step audit (takes 15 min, async). No pitch — just the gaps."
- **Touch 2 (Day 3):** Custom finding from their public funnel
- **Touch 3 (Day 7):** Soft close. "reply 'audit'"
- **Tags:** `community_comment`, `risk_audit`, `proposal_leakage`, `manual_process`

### Warm Intro — 2-Touch
- **Touch 1 (Day 0):** Mutual connection + founder-in-delivery framing
- **Touch 2 (Day 5):** Soft close with public playbook link
- **Tags:** `warm_intro`, `mutual_connection`, `referral`, `scaling_past_50k`

---

## 4. The Training Data (labeled_bios.jsonl)

### Structure
- 105 prospect bios hand-labeled as **agency owner (1)** or **not (0)**
- Used to train a bio classifier (now embedded in `flow_router.py` via regex)

### Label 1 Patterns (Agency Owner)
- "Helping agency owners turn financial chaos into profitable growth"
- "Founder-led service agency doing $200K+/month?"
- "I run ops for founders and agencies who are drowning in deliverables"
- "Building Agentic AI systems for business"
- Revenue/team signals: "$200 million in email revenue for clients", "12 years building a marketing agency"

### Label 0 Patterns (Not Agency Owner)
- Job titles, personal brands, non-agency services
- "Principal at prt"
- "Helping men reprogram themselves"
- "Former reporter & non-profit staffer, now retired"

---

## 5. The Auto-Sender Pipeline (auto_sender_run.log)

### Run Structure
```
[Auto-Sender] Daily budget: 5 DMs remaining
[Auto-Sender] Sending to 5 prospects...
    🔍 Validating @JoshuaChronos...
    ✅ @JoshuaChronos validated
    Sending to @JoshuaChronos (score=100)...
    ✓ Sent (DM ID: ...)
    🔍 Validating @mislavbeslic...
    ✗ Failed: [timeout/network]
    🔄 SOFT FAIL - will retry
    ...
[Auto-Sender] Complete. Sent 3 DMs this run.
```

### Failure Types
- **SOFT FAIL:** Timeout/network issues → will retry
- **HARD FAIL:** `403 Forbidden: Sender is not verified to send message requests` → will never retry

### Key Constraints
- Daily budget of 5 DMs
- Validates prospects via X API before sending
- Uses 100% score (all qualified)
- Twitter API requires verification for DMs

---

## 6. Sept 24 Update: flow_router.py Changes

**Major changes from backup (20260924) to current:**

### DM Generation Overhaul
**Before (v1):** Hardcoded opener (`"Hey {name}"`), structured observations from limited context, pain mirrors in direct questions.

**After (v2):** 
- `_quote_from_tweet()` extracts distinctive sentences from prospect's own tweets (scores: pain keywords +3, numbers +2, first-person +2)
- `_pick_opener()` uses deterministic hash to vary opener style per prospect
- `_easy_closer()` generates binary choice questions anchored in prospect's signals (no open diagnosis)
- All flows now call these helpers for human-like variation

### Context Extraction Enhancements
- Added `product` extraction from bio lines (`"Product $Nk/m"`)
- Added `authority_flow_hint` mapping from source_post_author (e.g., "KarlSakas" → "risk_audit")
- Enhanced comment analysis for `comment_pain`, `comment_buying_signal`, `comment_owner_signal`
- Added enriched profile support (verified, followers_count)

### Routing Logic
- v2 evidence-aware scoring: comment pain gets +2-3, source authority gets +1-3, enriched verified gets +3
- Better negative signal handling (drops designers/SEO specialists from most flows)
- Falls back to v1 when no rich evidence (comment text, source author, enriched profile)

### Revenue Extraction
**Before:** Simple patterns (`$\d+[km]?/mo`, `\d+[- ]?figure`, `\d+\+?\s*clients?`)

**After:** Extended patterns including `$\d+[km]?\d*(?:\.\d+)?[km]?/m(?:o|onth)?`, `$\d+(?:\.\d+)?[KM]??\s*(?:/|per\s)\s*(?:mo|month)`, `$\d+[KM]?\+?\s*(?:arr|mrr)?`

---

## 7. Key Implementation Details

### Company Extraction (Strict Filtering)
Filters out:
- Government agencies (gov, ministry, commission, authority)
- Generic entities (efcc, naiew, fbi, un, who, imf, difference)
- Email domains (gmail, outlook, yahoo, hotmail)
- Tech buzzwords (ai, llm, seo, geo, api, ui, ux)

### Pain Signal Mapping
8 pain categories: overwhelm, delivery, hiring, process, time, growth, ops, scale
Each has keyword triggers for detection in bio, tweet context, and comments

### Closer Question Strategy (from _easy_closer)
- Binary choices anchored in their quotes ("Client stuff or team stuff?")
- Product/scale owners: "Solo on [product], or is there a team now?"
- Pain-anchored binaries from pain map
- Zero-effort fallback: "Still enjoying the ride?"

---

## 8. Files Summary

| File | Purpose |
|------|---------|
| `scripts/content_signal_sequence.md` | 2-touch content trigger flow |
| `scripts/ghosted_recovery_sequence.md` | 3-touch lead recovery flow |
| `scripts/hiring_signal_sequence.md` | 3-touch hiring signal flow |
| `scripts/risk_audit_sequence.md` | 3-touch risk audit flow |
| `scripts/warm_intro_sequence.md` | 2-touch warm intro flow |
| `data/labeled_bios.jsonl` | 105 prospect training examples (agency vs non-agency) |
| `logs/auto_sender_run.log` | Production run log showing validation, sending, and failure patterns |
| `scripts/flow_router.py` | Main routing and DM generation logic (v2) |

---

## 9. Notes for New Operators

- **DO NOT** use em dashes (AI tell) — use regular hyphens or colons
- **DO NOT** use generic templates — every DM must reference specific signals
- **Always** route to founder_mirror when evidence is thin
- **Never** send to prospects with `403` errors (sender unverified)
- **Daily limit:** 5 DMs per run
- **Soft failures** (timeout/network) retry; **hard failures** (403) do not

---

## 10. Next Steps / Open Questions

1. What's the response rate per flow? (tracking needed in separate analytics)
2. How does the bio classifier (labeled_bios.jsonl) integrate with auto_sender.py?
3. What's the enriched profile source (API? manual data? enrichment service?)
4. Are there follow-up sequences for replies, or just initial DMs?

---

*Document generated from codebase audit on 2026-10-20*