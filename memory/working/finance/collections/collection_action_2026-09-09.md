# Collection Actions — 2026-09-09

**Generated:** 2026-09-09 (Cron: Billing & Invoicing Specialist)
**Source:** Aging Report 2026-09-09
**Decision Logic:** `manage_collections()` from Profile.md

---

## Collection Plan: EMPTY

**No overdue invoices exist.** All aging buckets are $0.

| Invoice ID | Client | Amount | Days Overdue | Action | Due | Owner |
|------------|--------|--------|--------------|--------|-----|-------|
| — | — | — | — | — | — | — |

---

## Decision Logic Applied

```python
aging = calculate_aging(invoices_issued)  # → empty
actions = []
for invoice in aging.overdue:             # → no iterations
    behavior = client_payment_behavior.get(invoice.client, {})
    days_overdue = (today - invoice.due_date).days
    # action determination skipped (no invoices)
```

**Result:** `CollectionPlan(actions=[], aging_summary={current:0, 1_30:0, 31_60:0, 61_90:0, 90_plus:0})`

---

## Escalation Check

| Trigger | Condition | Status |
|---------|-----------|--------|
| Invoice not sent > 24h | Any invoice unsent | ❌ No invoices |
| Payment overdue > 30 days | Any invoice > 30 days overdue | ❌ No invoices |
| Dispute > $5k or > 14 days | Any dispute | ❌ No disputes |
| Write-off needed | Any bad debt candidate | ❌ No candidates |
| Legal/compliance issue | Any regulatory issue | ❌ None |

**No escalations to Finance Director or COO required.**

---

## Next Run

Will re-evaluate when:
- Revenue Operations signals `deal_booking` complete (Stage 5 Closed Won)
- Delivery Project Manager signals `milestone_complete`
- Recurring billing date for Active Retainer Client

**Status:** ✅ Collections workflow idle — awaiting first invoice trigger.