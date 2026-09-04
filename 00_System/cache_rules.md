# Cache Rules

The principle: **never reload what is already understood — refresh only
what may have changed.** This is what keeps a single session from re-reading
the entire OS on every run.

---

## Hot Cache — load once per session, rarely changes

- `02_COO/identity.md`
- `01_CEO/company.md`
- `01_CEO/decision_principles.md`
- `00_System/execution_charter.md`

## Warm Cache — reload if the checkpoint says it's stale, or the task touches it

- `02_COO/company_state.md`
- Active department profiles (`03_Departments/Commercial/**`)
- Today's Notion snapshot (leads in "New"/"Queued" status, open conversations)

## Cold Storage — load only when the specific task needs it

- `04_Knowledge/**` — pull the one playbook the task requires, not the folder
- Closed/archived leads, past meeting notes, historical logs in Notion

---

## Rule of Thumb

If you're about to read a file "just in case," stop — that's what caused
the original bloat. Load Hot always. Load Warm if the checkpoint is missing
or the task is state-dependent. Load Cold only by name, only when cited by
a task.