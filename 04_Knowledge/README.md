# 04_Knowledge — Knowledge Base

CraftedWorkflows structured knowledge base. All operational knowledge lives here.

---

## Directory Structure

```
04_Knowledge/
├── Company/                    # Company foundation
│   ├── Core_Philosophy.md      # Mission, values, principles
│   ├── Origin_Story.md         # Founder journey, epiphany
│   ├── Value_Proposition.md    # Promise, differentiation
│   ├── Target_Market.md        # ICP, personas, qualification
│   └── README.md
├── Playbooks/                  # Operational playbooks
│   ├── Outreach/
│   │   ├── DM_Flows.md              # 5 DM flows + follow-ups
│   │   ├── Daily_Targeting_Playbook.md
│   │   ├── Lead_Sourcing.md         # Where to find leads
│   │   └── Lead_Qualification.md    # Scoring, qualification
│   ├── Sales/
│   │   ├── Sales_Call_Playbook.md      # Complete call script
│   │   ├── Pre_Call_Sequence.md        # 4-email sequence
│   │   ├── Objection_Handling.md       # 8 objections + responses
│   │   └── Roleplay_Scenarios.md       # 8 roleplay scenarios
│   ├── Marketing/
│   │   └── Content_Pillars.md     # 4 pillars (Goat Farmer)
│   ├── Outreach/
│   ├── Operations/
│   ├── Delivery/
│   └── Finance/
├── Offers/                     # Product/offer structures
│   └── Pricing_Packages.md      # System Jumpstart / Self-Running Revenue / Remove Founder
├── Research/                   # Market research, competitive intel
│   └── README.md
├── Templates/                  # Reusable templates
├── SOPs/                       # Standard operating procedures
├── Frameworks/                 # Strategic frameworks
├── Prompts/                    # AI prompts
├── Competitive_Intelligence/   # Competitor analysis
├── Market_Intelligence/        # Market trends, sizing
├── Case_Studies/               # Client success stories
├── Prompts/                    # AI prompts
├── Skills/                     # Skill library
├── Assets/                     # Static assets
├── Inbox/                      # RAW unprocessed input (DO NOT DELETE)
│   ├── craftedworkflows_master.md    # Master brain dump
│   ├── outbound_playbook.md          # DM flows + lead sourcing
│   └── sales_call_playbook.md        # Sales scripts + roleplays
└── README.md                   # This file
```

---

## Purpose of Each Folder

| Folder | Purpose | Git Tracked |
|--------|---------|-------------|
| `Company/` | Company identity, strategy, positioning | ✅ Yes |
| `Playbooks/` | Operational how-to guides | ✅ Yes |
| `Offers/` | Product packages, pricing | ✅ Yes |
| `Research/` | Market research, competitive intel | ✅ Yes |
| `Templates/` | Reusable document templates | ✅ Yes |
| `SOPs/` | Standard operating procedures | ✅ Yes |
| `Frameworks/` | Strategic frameworks | ✅ Yes |
| `Competitive_Intelligence/` | Competitor analysis | ✅ Yes |
| `Market_Intelligence/` | Market trends, sizing | ✅ Yes |
| `Case_Studies/` | Client success stories | ✅ Yes |
| `Prompts/` | AI prompts for operations | ✅ Yes |
| `Skills/` | Skill library for AI employees | ✅ Yes |
| `Assets/` | Static assets (images, logos) | ✅ Yes |
| `Inbox/` | **RAW input — never delete, never modify** | ✅ Yes |

---

## Naming Conventions

### Files
- **Format:** `Pascal_Case.md` (e.g., `Sales_Call_Playbook.md`)
- **Index files:** `README.md` (one per directory)
- **No spaces, no special chars**

### Directories
- **Format:** `Pascal_Case` (e.g., `Playbooks/`, `Competitive_Intelligence/`)
- **Singular** for category folders (e.g., `Playbooks/` not `Playbook/`)

### Cross-References
- Use **relative markdown links**: `[Link Text](./Relative/Path.md)`
- Always link, never duplicate content

---

## How to Ingest New Knowledge

### 1. Raw Input → Inbox
All raw input (meeting notes, brain dumps, articles) goes to `Inbox/` as-is.

### 2. Process Inbox → Structured KB
- Read Inbox file completely
- Extract concepts into appropriate `Playbooks/`, `Company/`, `Offers/`, etc.
- **Improve** existing docs instead of creating duplicates
- Cross-reference with relative links
- Leave Inbox file **untouched**

### 3. Update Index
- Update relevant `README.md` index files
- Update this `README.md` if structure changes

### 4. Commit & Push
- Logical commits per logical change
- Descriptive commit messages

---

## Ingestion Checklist

When processing an Inbox file:

- [ ] Read entire file completely
- [ ] Identify all distinct concepts/topics
- [ ] Map each to existing KB location (or create new)
- [ ] Improve existing docs (don't duplicate)
- [ ] Add cross-references
- [ ] Leave Inbox file untouched
- [ ] Update relevant README indexes
- [ ] Commit with descriptive message

---

## Knowledge Architecture Principles

1. **Documentation > Memory** — If it's not written, it doesn't exist
2. **Reference > Duplication** — Link, don't copy
3. **Structure > Chaos** — Everything has a home
4. **Version Control** — Every change tracked
5. **Inbox is Sacred** — Never modify, never delete

---

## Executive Runtime Integration

The executive runtime (`runtime/`) can discover knowledge by:

```python
from pathlib import Path

KB_ROOT = Path("04_Knowledge")

# Discover all playbooks
playbooks = list((KB_ROOT / "Playbooks").rglob("*.md"))

# Load company context
company_philosophy = (KB_ROOT / "Company" / "Core_Philosophy.md").read_text()
target_market = (KB_ROOT / "Company" / "Target_Market.md").read_text()

# Load specific playbook
dm_flows = (KB_ROOT / "Playbooks" / "Outreach" / "DM_Flows.md").read_text()
```

---

## Verification

Run this to verify KB structure:
```bash
find 04_Knowledge -type f -name "*.md" | head -20
```

Should show structured markdown files across all categories.