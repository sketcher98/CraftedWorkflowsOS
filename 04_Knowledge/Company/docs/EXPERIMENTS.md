# Experiments

## Experiment Cards

| ID | Hypothesis | Primary Metric | Secondary Metrics | Guardrail | ICE (I/C/E) | Status |
|---|---|---|---|---|---|---|
| EXP-001 | Customer language in headline ("Stop Hiring...") will lift audit bookings because it mirrors the visitor's internal frustration | Audit form completions / sessions | Time on page, Scroll depth | Bounce rate | 9/9/10 | 🔴 To build |
| EXP-002 | Adding "Or we pay you for wasting your time" to guarantee bar will lift high-intent clicks by reducing risk perception | CTA clicks / sessions | Audit form starts | Form abandonment | 8/8/8 | 🔴 To build |
| EXP-003 | Moving case study metrics above fold (sticky sidebar) will increase trust and reduce bounce | Bounce rate | Time on page, Case study clicks | Page load speed | 7/8/9 | 🟡 Designed |
| EXP-004 | Video testimonial autoplay (muted) on hover will increase testimonial engagement vs. static cards | Testimonial video plays / views | CTA clicks after testimonial | Page weight | 6/7/7 | 🟡 Designed |
| EXP-005 | Adding "3 spots left this week" (real capacity) to final CTA will lift urgency without fake scarcity | Audit bookings / week | Form completion rate | Trust complaints | 8/9/6 | 🟡 Designed |
| EXP-006 | Progressive disclosure of offers (show Tier 1 first, expand to 2/3 on click) will reduce decision paralysis | Tier 2/3 clicks | Time on offers section | Tier 1 conversion | 7/7/8 | 🟢 Planned |
| EXP-007 | Exit survey on audit form abandon: "What stopped you?" will surface new objections for O/CO table | New objections identified | Survey completion rate | Annoyance signals | 9/10/9 | 🟢 Planned |

## Experiment Backlog

- Test hero image vs. no hero image (type-only hero)
- Test long-form vs. abbreviated FAQ (progressive disclosure)
- Test CTA button copy variations ("Book Free Audit" vs "Diagnose Bottlenecks" vs "Get My Blueprint")
- Test social proof density (5 logos vs. 10 logos vs. logos + ratings)
- Test guarantee placement (near CTA vs. dedicated section vs. inline in offers)
- Test "Not for you" disqualifier visibility (expand vs. collapse)

## Experiment Process

### Before Launch
- [ ] Hypothesis documented with research basis
- [ ] Primary metric defined (decides winner)
- [ ] Secondary metrics defined (monitoring)
- [ ] Guardrail metric defined (must not decrease)
- [ ] Sample size calculated (baseline, MDE, 80% power, 95% sig)
- [ ] Duration set (min 1 full business cycle = 2 weeks)
- [ ] Variants built and QA'd
- [ ] Randomization verified

### During Experiment
- [ ] No peeking at interim results
- [ ] Full business cycle(s) completed
- [ ] Technical issues monitored

### After Experiment
- [ ] Raw numbers documented
- [ ] Confidence interval calculated
- [ ] Practical significance assessed (not just statistical)
- [ ] Learnings documented: what worked, what didn't, why
- [ ] Decision: Implement / Iterate / Kill
- [ ] Winning insights scaled to other channels (ads, email, sales)

## Tracking & Reporting Template

```markdown
## Experiment: [ID] - [Title]

**Variant A (Control):** [Description]
**Variant B (Treatment):** [Description]

**Dates:** [Start] to [End]
**Sample Size:** [N per variant]
**Traffic Source:** [All / Organic / Paid / etc.]

### Results
| Metric | Control | Treatment | Diff | Sig | Practical Sig |
|--------|---------|-----------|------|-----|---------------|
| Primary | X% | Y% | +Z% | p= | Yes/No |
| Secondary 1 | | | | | |
| Guardrail | | | | | |

### Learning
[What this teaches us about visitors]

### Decision
[Implement / Iterate / Kill] — [Reason]

### Applicable To
[Other pages, ad copy, email sequences, sales scripts]
```