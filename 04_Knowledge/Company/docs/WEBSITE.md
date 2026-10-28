# Design Review: CraftedWorkflows Website
**Verdict:** NOT DONE (score 6/10)
**The One Thing:** Convert agency founders experiencing operational chaos into booked "Automation Escape Audit" calls.
**Keeps its promise?** Partial — messaging and structure are strong; execution details, failure states, and unseen surfaces need work.

---

## Cut List (What to Remove)

1. **Remove the "Our Recent Work" section label** — redundant with "Trusted and Recommended by" + case studies themselves speak.
2. **Collapse "Why Choose Us / Guarantees" + "Process Detail" into one section** — two sections explaining the same methodology fragments attention.
3. **Cut the footer social links to 3 max** — 5 links dilutes the single CTA.
4. **Remove "Most Popular" badge on Tier 2** — let the value speak; badges feel salesy.
5. **Delete the "Zero risk. Zero pressure." paragraph** — the guarantees and "diagnostic not sales" framing already say this.
6. **Remove the 🐐 emoji** — undermines the premium positioning.
7. **Cut the transitional CTA "Get the 5-Phase Blueprint" from hero** — competes with primary CTA above fold. Move to mid-page or exit intent only.

---

## Fix List (Ranked)

### 1. Focus States & Accessibility (Severity: Critical)
**Issue:** No visible, on-brand focus rings on interactive elements.
**Fix:** Add `outline: 2px solid var(--color-focus); outline-offset: 2px;` to all focusable elements. Test keyboard-only navigation end-to-end.
**Location:** Global — buttons, links, form inputs, cards.

### 2. Loading & Empty States (Severity: Major)
**Issue:** Tally form embed blocks render; no skeleton; no empty state for case studies.
**Fix:** 
- Lazy-load Tally iframe with `<iframe loading="lazy">` + skeleton placeholder ("Loading your audit form...")
- Design empty state for /case-studies: illustration + "No case studies published yet" + "Diagnose Your Bottlenecks" CTA
- Design 404 page: friendly copy + search + "Return to audit" link
- Design offline state: cached shell + "You're offline. Changes will sync when connected."

### 3. Mobile Hover Alternatives (Severity: Major)
**Issue:** Case study details, card hover effects, magnetic button only work on hover.
**Fix:** 
- Case studies: tap-to-expand accordion on mobile
- Cards: tap = expand (not hover)
- Magnetic button: reduce to simple scale on touch; keep magnetic on desktop only
- All hover micro-interactions need touch equivalents

### 4. Breadcrumbs & "You Are Here" (Severity: Major)
**Issue:** Interior pages (/case-studies, /offers, /about, /blueprint) have no breadcrumbs, no highlighted nav.
**Fix:** Add breadcrumb component on all interior pages: `Home > Case Studies > RemoteFirst Offboarding`. Highlight current nav item in header.

### 5. Search Visibility (Severity: Major)
**Issue:** No search in header for goal-driven users.
**Fix:** Add search icon in header (top-right) opening modal with typeahead. Index: case studies, FAQ, offers, methodology pages.

### 6. Form Undo & Escape (Severity: Major)
**Issue:** No undo after form submit; modals don't close on Escape.
**Fix:** 
- Toast after Tally form submit: "Audit booked! Undo →" (5s, cancels submission)
- All modals/drawers: close on Escape key, click backdrop, swipe down (mobile)

### 7. Descriptive Link Text (Severity: Minor)
**Issue:** "Click here" patterns in case study links.
**Fix:** Replace with "View case study: RemoteFirst — 45hrs/month saved with 1-click offboarding"

### 8. Color-Only Status Indicators (Severity: Minor)
**Issue:** Success/warning/error states use color only.
**Fix:** Add icon + text label: ✅ Success — Your audit is booked. ⚠️ Warning — Form incomplete. ❌ Error — Invalid email format.

### 9. Touch Target Verification (Severity: Minor)
**Issue:** Mobile CTA buttons and card hit areas may be <44x44px.
**Fix:** Audit all interactive elements on real devices; enforce `min-height: 44px; min-width: 44px` on touch targets.

### 10. Hero Type Balance (Severity: Cosmetic)
**Issue:** "STOP HIRING" word-break may orphan "HIRING" on narrow screens.
**Fix:** Test `text-wrap: balance` + manual `<wbr>` at key breakpoints; verify on 320px, 375px, 414px.

---

## Back of the Fence (Unseen Surfaces Failing the Bar)

| Surface | Current State | Required Standard |
|---|---|---|
| **404 Page** | Browser default | Designed: illustration + search + "Return to audit" CTA |
| **Tally Form Error** | Tally default | Branded error: "Something went wrong. Email craftedworkflows@gmail.com or try again." |
| **Form Success** | Tally default | Branded: "Audit booked! Check your email for confirmation. We'll send a prep questionnaire 24hrs before." |
| **Offline Page** | None | Cached shell + "You're offline. Your progress is saved." |
| **Loading Skeletons** | None | Hero skeleton, case study card skeletons, testimonial skeletons |
| **Email Confirmation** | Tally default | Branded HTML email matching site design |
| **PDF Report** | Not designed | Cover with name, category radar chart, 5-Phase roadmap, tier-specific CTA |
| **Abandonment Email** | Not designed | "You started the assessment — your progress is saved. Finish here →" |
| **Cookie/Privacy Banner** | Not designed | Minimal, dismissible, no dark patterns |
| **Print Stylesheet** | None | Clean print version of case studies and blueprint |

---

## Steps-to-Value Audit

| Step | Action | Current | Target |
|---|---|---|---|
| 1 | Land on homepage | ✅ | ✅ |
| 2 | Understand offer | ✅ (5-sec test passes) | ✅ |
| 3 | Click primary CTA | ✅ | ✅ |
| 4 | Complete Tally form | 7 fields | ≤5 fields (name, email, company, revenue, team) |
| 5 | Book call slot | Tally calendar | ✅ |
| 6 | Receive confirmation | Tally email | Branded email + prep questionnaire |
| 7 | Attend audit call | ✅ | ✅ |
| 8 | Receive blueprint | Manual | Automated Notion page + PDF |

**Verdict:** 8 steps to value. **Target: 3 steps.** Major reduction needed in form fields and post-booking automation.

---

## Demo Culture Check

- [ ] Working prototype on real device (phone + desktop)
- [ ] Tally form integration tested end-to-end
- [ ] Lenis smooth scroll + GSAP animations at 60fps on 4G Android
- [ ] Font loading <200KB verified (variable subset)
- [ ] All 7 experiment variants built and QA'd
- [ ] Scorecard assessment flow built and tested
- [ ] PDF generation working for all 3 tiers

---

## Final Verdict

**NOT DONE — 6/10**

The messaging, positioning, and visual design are at 9/10. The execution gaps (accessibility, failure states, mobile interactions, automation, unseen surfaces) drag the score down. This is a "polish the back of the fence" situation — the front looks great; the cabinet interior is plywood.

**Ship Criteria (all must pass):**
1. Keyboard navigation works perfectly with beautiful focus rings
2. All 10 back-of-fence surfaces designed and implemented
3. Mobile hover alternatives working on real devices
4. Tally form lazy-loaded with skeleton
5. Breadcrumbs + search on all interior pages
6. Steps-to-value ≤ 3 (reduce form fields, automate blueprint delivery)
7. All experiment variants ready to deploy
8. Scorecard funnel live and tracked

Once these 8 items are complete, re-review. If all pass: **INSANELY GREAT**.