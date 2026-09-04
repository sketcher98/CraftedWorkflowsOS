# Employee Profile Template

All CraftedWorkflows employees MUST follow this exact structure.

---

## Template

```markdown
# [Employee Name]

---

## Identity

**Role:** [Title]
**Department:** [Department]
**Reports To:** [Director Name]
**Capability Tier:** [Core | Specialist | Senior]

---

## Mission

[One paragraph: what this employee exists to achieve for CraftedWorkflows]

---

## Responsibilities

**Owns:**
- [Primary responsibility 1]
- [Primary responsibility 2]
- [Primary responsibility 3]

**Supports:**
- [Supporting responsibility 1]
- [Supporting responsibility 2]

---

## Inputs

**From 04_Knowledge:**
- `[path/to/playbook.md]` — [what they extract]
- `[path/to/playbook.md]` — [what they extract]

**From Memory:**
- `working_memory.current_objective` — [purpose]
- `longterm.clients.{client_id}` — [purpose]
- `episodic.{event_type}` — [purpose]
- `preferences.{category}` — [purpose]

**From Runtime:**
- `context.company.icp` — [purpose]
- `context.company.offers` — [purpose]
- `context.checkpoint.last_commercial_run` — [purpose]

---

## Outputs

**Artifacts Produced:**
- `[artifact_name]` → saved to `memory/working/{path}` or `memory/longterm/{path}`
- `[artifact_name]` → saved to `memory/working/{path}`

**Memory Writes:**
- `working_memory.{key}` = [value description, trigger]
- `longterm.{layer}.{key}` = [value description, trigger]
- `episodic.{event_type}.{id}` = [value description, trigger]
- `preferences.{category}.{key}` = [value description, trigger]

---

## KPIs

**Primary (must hit):**
- Metric 1: Target
- Metric 2: Target

**Secondary (should hit):**
- Metric 3: Target
- Metric 4: Target

**Never Optimize For:**
- Vanity metric 1
- Vanity metric 2

---

## Decision Authority

**Can Decide Autonomously:**
- [Decision scope 1]
- [Decision scope 2]

**Must Escalate To Director:**
- [Escalation trigger 1]
- [Escalation trigger 2]

**Must Escalate To COO:**
- [Escalation trigger 1]

---

## Escalation Rules

| Trigger | Escalate To | Timeout | Context Required |
|---------|-------------|---------|------------------|
| [Condition] | [Role] | [Time] | [What to include] |

---

## Memory Usage

**Reads (on task start):**
- Layer: Key pattern — Purpose

**Writes (on task completion):**
- Layer: Key pattern — Purpose — Trigger

**Retention:**
- Working: [TTL or event-based]
- Long-term: [Retention rule]
- Episodic: [Retention rule]

---

## Capability Declarations

```yaml
capabilities:
  - name: "capability_name"
    description: "What this capability achieves"
    provider_preference: "auto|browser|gpt|perplexity"
    required_context:
      - "context_key_1"
      - "context_key_2"
    output_format: "json|markdown|text"
    memory_write: "layer.key_pattern"
```

---

## Tools

**Primary:**
- `capability:research` → provider: auto
- `capability:writing` → provider: auto
- `capability:analysis` → provider: auto

**Platform Access:**
- LinkedIn (via browser capability)
- X/Twitter (via browser capability)
- CRM (via memory longterm.clients)
- Website Research (via browser capability)

**Future:**
- Apollo (enrichment)
- Clay (workflow automation)
- Perplexity Deep Research
- OpenAI Deep Research

---

## Playbooks

**Primary (executes daily):**
- `04_Knowledge/Playbooks/[Category]/[Playbook].md` — [When used]

**Secondary (executes situationally):**
- `04_Knowledge/Playbooks/[Category]/[Playbook].md` — [When used]

**Reference (consults for decisions):**
- `04_Knowledge/Company/[Document].md` — [What decision it informs]

---

## Dynamic Decision Logic

The employee reasons over playbooks at runtime to choose:

```python
# Pseudocode - employee implements this logic
def select_approach(context):
    icp_match = match_icp(context.prospect, context.company.icp)
    platform = select_platform(context.prospect, context.playbooks.outreach)
    flow = select_dm_flow(context.prospect, context.playbooks.outreach)
    qualification = select_qualification_method(context.prospect, context.playbooks.sales)
    offer = select_offer(context.prospect, context.company.offers)
    followup = select_followup_cadence(context.prospect, context.playbooks.outreach)
    handoff = determine_handoff(context.prospect, context.playbooks.sales)
    return ExecutionPlan(icp_match, platform, flow, qualification, offer, followup, handoff)
```

**Selection Criteria:**

| Decision | Based On | Playbook Reference |
|----------|----------|-------------------|
| ICP Match | Company size, revenue, pain signals, role | `04_Knowledge/Company/Target_Market.md` |
| Platform | Prospect activity, content type | `04_Knowledge/Playbooks/Outreach/DM_Flows.md` |
| Search Strategy | Emotion, niche, buying signals | `04_Knowledge/Playbooks/Outreach/Daily_Targeting_Playbook.md` |
| DM Flow | Prospect tier, relationship status | `04_Knowledge/Playbooks/Outreach/DM_Flows.md` |
| Qualification Method | Deal stage, complexity | `04_Knowledge/Playbooks/Sales/Sales_Call_Playbook.md` |
| Offer | Revenue, pain depth, urgency | `04_Knowledge/Offers/Pricing_Packages.md` |
| Follow-up Cadence | Flow type, response pattern | `04_Knowledge/Playbooks/Outreach/DM_Flows.md` |
| Sales Strategy | Objection type, stakeholder map | `04_Knowledge/Playbooks/Sales/Objection_Handling.md` |
| Handoff | Close probability, complexity | `04_Knowledge/Playbooks/Sales/Sales_Call_Playbook.md` |

---

## Handoff Rules

| To Employee | Trigger | Artifact Passed | Context Required |
|-------------|---------|-----------------|------------------|
| [Employee] | [Condition] | [Memory key or file] | [What they need to know] |

---

## Success Metrics

**Weekly Review:**
- [Metric 1]: Target
- [Metric 2]: Target

**Monthly Review:**
- [Metric 3]: Target
- [Metric 4]: Target

**Quarterly Review:**
- [Metric 5]: Target

---

## Communication Style

- [Style trait 1]
- [Style trait 2]
- [Style trait 3]

---

## Verification Checklist

- [ ] All paths reference 04_Knowledge (no hardcoded content)
- [ ] Capability declarations match runtime provider registry
- [ ] Memory writes use correct layer/key patterns
- [ ] Escalation paths follow org chart
- [ ] KPIs align with department KPIs
- [ ] Playbook references are current
- [ ] Handoff rules are bidirectional (receiver acknowledges)
```

---

## Usage Instructions

1. **Copy this template** to new employee Profile.md
2. **Replace ALL bracketed sections** with employee-specific content
3. **Reference 04_Knowledge by relative path** — never duplicate content
4. **Declare capabilities** that exist in runtime provider registry
5. **Memory keys** must follow naming conventions: `layer.category.key`
6. **Commit** with message: `feat(employee): Add [Employee Name] to [Department]`