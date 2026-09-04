# Refresh Policy

## Purpose

Defines when Hermes should re-read information mid-session. Goal: minimize
unnecessary reads while never acting on stale information.

> Never reload what is already understood. Refresh only what may have changed.

---

## Refresh Triggers

Refresh when any of these are true:
- A new session begins
- The CEO explicitly requests a refresh
- A monitored file has changed (Notion webhook, or file edit)
- A task needs information not currently loaded
- A decision depends on verifying current company state

## Static Context (load once per session)

Company Mission, Vision, Identity, Decision Principles, Execution Charter.
Only refresh if the underlying document changes.

## Dynamic Context (refresh whenever the task depends on it)

Company State, today's Notion snapshot (leads, inbox, active conversations),
active department queues.

## Reference Context (load only when directly relevant)

04_Knowledge playbooks, closed/archived Notion records, historical logs.

---

## Priority Order

1. Company Documentation (01_CEO, 02_COO, 03_Departments)
2. Current Notion State (leads, clients, projects, tasks — live data)
3. Historical Records (closed items, past logs)
4. General Model Knowledge

Higher always overrides lower. If Notion and a cached memory disagree,
Notion wins — it's the live source of truth.