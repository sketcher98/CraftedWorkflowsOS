# Design System

## Design Direction

**Brand Essence:** CONTROL — the calm authority of systems replacing chaos

**Visual Tension:** Rigid structure (grids, systems) vs. organic flow (animation, growth)

**Signature Moment:** Hero type that fills the viewport — "STOP HIRING" in massive display type that drops to reveal the subhead with a choreographed stagger. The word "STOP" hits first, then "HIRING" — a deliberate pause that makes visitors read it twice.

**Technical Ambition:** 60fps scroll-driven narrative with Lenis smooth scroll, zero-JS fallback for core content, sub-200KB font payload.

---

## Typography

### Font Pairing

**Display: Space Grotesk Variable (wght 300-700)**
- Geometric, tech-forward, distinctive at massive scale
- Negative tracking at display sizes (-0.04em)
- Free/Google Font — zero licensing friction

**Body: DM Sans Variable (wght 400-600)**
- Clean, modern, excellent readability at 16-18px
- Pairs naturally with Space Grotesk (both geometric humanist)
- Variable font: single file, full weight range

### Fluid Type Scale (clamp)

```css
:root {
  /* Base: 16px at 320px → 18px at 1440px */
  --text-base: clamp(1rem, 0.929rem + 0.357vw, 1.125rem);
  --text-sm: clamp(0.875rem, 0.821rem + 0.268vw, 1rem);
  --text-lg: clamp(1.125rem, 1.018rem + 0.536vw, 1.375rem);
  --text-xl: clamp(1.375rem, 1.214rem + 0.804vw, 1.719rem);
  --text-2xl: clamp(1.719rem, 1.482rem + 1.188vw, 2.148rem);
  --text-3xl: clamp(2.148rem, 1.786rem + 1.813vw, 2.685rem);
  --text-4xl: clamp(2.685rem, 2.196rem + 2.446vw, 3.356rem);
  
  /* Hero sizes - dramatic scale */
  --text-hero: clamp(5rem, 2rem + 12vw, 18rem);     /* "STOP HIRING" */
  --text-display: clamp(3.5rem, 1.5rem + 8vw, 10rem); /* Subhead */
}

/* Tracking scales inversely with size */
.text-hero { letter-spacing: -0.04em; line-height: 0.9; }
.text-display { letter-spacing: -0.03em; line-height: 1.0; }
.text-heading { letter-spacing: -0.02em; line-height: 1.1; }
.text-body { letter-spacing: 0; line-height: 1.6; }
.text-small { letter-spacing: 0.01em; line-height: 1.5; }
```

### Measure & Hierarchy

- **Body max-width:** 65ch (optimal reading measure)
- **Headline control:** Manual line breaks at key breakpoints via `<wbr>` and `&shy;`
- **No orphans:** `text-wrap: balance` on all headlines

### Font Loading Strategy (Sub-200KB)

```html
<!-- Preload critical fonts -->
<link rel="preload" href="/fonts/SpaceGrotesk-Variable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/fonts/DMSans-Variable.woff2" as="font" type="font/woff2" crossorigin>

<!-- Inline critical @font-face -->
<style>
  @font-face {
    font-family: 'Space Grotesk';
    src: url('/fonts/SpaceGrotesk-Variable.woff2') format('woff2-variations');
    font-weight: 300 700;
    font-display: swap;
  }
  @font-face {
    font-family: 'DM Sans';
    src: url('/fonts/DMSans-Variable.woff2') format('woff2-variations');
    font-weight: 400 600;
    font-display: swap;
  }
</style>
```

**Subset:** Latin-only (U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+2074, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD) — ~45KB each variable font.

---

## Tokens

### Spacing Scale

```css
:root {
  --space-0: 0;
  --space-1: 0.25rem;   /* 4px */
  --space-2: 0.5rem;    /* 8px */
  --space-3: 0.75rem;   /* 12px */
  --space-4: 1rem;      /* 16px */
  --space-5: 1.5rem;    /* 24px */
  --space-6: 2rem;      /* 32px */
  --space-8: 3rem;      /* 48px */
  --space-10: 4rem;     /* 64px */
  --space-12: 6rem;     /* 96px */
  --space-16: 8rem;     /* 128px */
  --space-20: 12rem;    /* 192px */
}
```

### Color Palette

**Dark Mode (Primary):**
```css
:root {
  /* Near-blacks — warm, not pure */
  --color-bg: #0a0a0a;           /* Primary background */
  --color-bg-elevated: #111827;  /* Cards, surfaces */
  --color-border: #1f2937;       /* Subtle borders */
  
  /* Text hierarchy */
  --color-text-primary: #fafaf9;    /* Headlines, primary copy */
  --color-text-secondary: #d4d4d8;  /* Body copy */
  --color-text-tertiary: #a1a1aa;   /* Muted, captions */
  --color-text-inverse: #0a0a0a;    /* On accent */
  
  /* Signature accent — warm orange/amber */
  --color-accent: #f97316;          /* Primary CTA, key highlights */
  --color-accent-hover: #ea580c;
  --color-accent-muted: rgba(249, 115, 22, 0.15); /* Backgrounds, borders */
  
  /* Functional */
  --color-success: #22c55e;
  --color-warning: #f59e0b;
  --color-error: #ef4444;
  
  /* Focus — visible AND beautiful */
  --color-focus: #f97316;
}
```

**Light Mode:**
```css
@media (prefers-color-scheme: light) {
  :root {
    --color-bg: #fafaf9;
    --color-bg-elevated: #ffffff;
    --color-border: #e4e4e7;
    --color-text-primary: #18181b;
    --color-text-secondary: #3f3f46;
    --color-text-tertiary: #71717a;
    --color-text-inverse: #fafaf9;
    --color-accent: #ea580c;
    --color-accent-hover: #dc2626;
    --color-accent-muted: rgba(234, 88, 12, 0.1);
    --color-focus: #ea580c;
  }
}
```

### Shadows (Elevation Scale)

```css
:root {
  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  --shadow-2xl: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  
  /* Colored shadows for accent moments */
  --shadow-accent: 0 20px 40px -12px rgba(249, 115, 22, 0.4);
}
```

---

## Components

### Buttons

| Variant | Styles | Usage |
|---------|--------|-------|
| **Primary** | `bg-accent text-inverse px-8 py-4 rounded-none font-medium` | Main CTA — "Diagnose Your Operational Bottlenecks" |
| **Secondary** | `border-2 border-accent text-accent px-8 py-4 rounded-none font-medium hover:bg-accent hover:text-inverse` | Transitional CTA — "Get the Blueprint" |
| **Ghost** | `text-text-primary px-4 py-2 hover:text-accent transition-colors` | Nav links, footer links |

**Magnetic Effect (JS):** Subtle follow on hover (0.3x mouse offset), elastic return on leave.

### Cards

```css
.card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  padding: var(--space-6);
  transition: transform 0.4s var(--ease-out-expo), 
              box-shadow 0.4s var(--ease-out-expo),
              border-color 0.3s ease;
}

.card:hover {
  transform: translateY(-8px);
  box-shadow: var(--shadow-xl);
  border-color: var(--color-accent-muted);
}
```

**Case Study Card:** Image bleeds top edges, content padded, metrics row at bottom.

### Forms

- Inputs: Full width, `bg-bg border-border text-text-primary placeholder-text-tertiary px-4 py-3`
- Focus: `outline-none ring-2 ring-focus ring-offset-2 ring-offset-bg`
- Labels: `text-sm font-medium text-text-secondary mb-1.5 block`
- Error: `text-error text-sm mt-1` (inline, not modal)

### Navigation

- Fixed header, `backdrop-blur-md bg-bg/80 border-b border-border`
- Logo: "CraftedWorkflows" in Space Grotesk Medium
- Links: Ghost button style
- Mobile: Slide-down drawer (no hamburger animation library — 15 lines CSS/JS)

---

## Motion & Animation

### Easing Tokens

```css
:root {
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-in-out-quint: cubic-bezier(0.83, 0, 0.17, 1);
  --duration-fast: 150ms;
  --duration-normal: 300ms;
  --duration-slow: 500ms;
  --duration-slower: 800ms;
  --duration-slowest: 1200ms;
}
```

### Page Load Choreography

```javascript
// GSAP timeline (runs after Lenis init)
const tl = gsap.timeline({ defaults: { ease: "expo.out" } });

tl.set(".page-content", { visibility: "visible" })
  // Hero title words stagger
  .from(".hero-title .word", {
    y: 120,
    opacity: 0,
    duration: 1.2,
    stagger: 0.08
  })
  // Subhead
  .from(".hero-subtitle", {
    y: 40,
    opacity: 0,
    duration: 0.8
  }, "-=0.6")
  // Primary CTA
  .from(".hero-cta", {
    y: 30,
    opacity: 0,
    duration: 0.6
  }, "-=0.4")
  // Nav cascade
  .from(".nav-item", {
    y: -20,
    opacity: 0,
    duration: 0.5,
    stagger: 0.05
  }, "-=0.8")
  // Trust badges
  .from(".trust-badge", {
    y: 30,
    opacity: 0,
    duration: 0.6,
    stagger: 0.06
  }, "-=0.5");
```

### Scroll Reveals (IntersectionObserver + CSS)

```css
[data-reveal] {
  opacity: 0;
  transform: translateY(60px);
  transition: opacity 0.8s var(--ease-out-expo), 
              transform 0.8s var(--ease-out-expo);
}

[data-reveal].is-visible {
  opacity: 1;
  transform: translateY(0);
}

[data-reveal="stagger"] > * {
  opacity: 0;
  transform: translateY(40px);
  transition: opacity 0.6s var(--ease-out-expo), 
              transform 0.6s var(--ease-out-expo);
}
```

### Smooth Scroll (Lenis)

```javascript
import Lenis from '@studio-freight/lenis';

const lenis = new Lenis({
  duration: 1.2,
  easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
  smooth: true,
  direction: 'vertical'
});

lenis.on('scroll', ScrollTrigger.update);
gsap.ticker.add((time) => lenis.raf(time * 1000));
gsap.ticker.lagSmoothing(0);
```

### Respect Reduced Motion

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

---

## Layout System

### Grid

```css
:root {
  --grid-columns: 12;
  --grid-gutter: clamp(1.5rem, 3vw, 3rem);
  --grid-margin: clamp(1.5rem, 5vw, 6rem);
  --content-max: 65ch;
}

.grid {
  display: grid;
  grid-template-columns: repeat(var(--grid-columns), 1fr);
  gap: var(--grid-gutter);
  padding-inline: var(--grid-margin);
  max-width: 1800px;
  margin-inline: auto;
}
```

### Editorial Grid (Long-form sections)

```css
.editorial-grid {
  display: grid;
  grid-template-columns:
    [full-start] minmax(var(--grid-margin), 1fr)
    [content-start] min(var(--content-max), 100%)
    [content-end] minmax(var(--grid-margin), 1fr)
    [full-end];
}

.editorial-grid > * {
  grid-column: content;
}

.editorial-grid > .wide {
  grid-column: full-start / full-end;
}
```

### Hero Layout

```css
.hero {
  min-height: 100svh;
  display: grid;
  place-items: end center;
  padding: var(--grid-margin);
  padding-bottom: 15vh;
  position: relative;
}

.hero-title {
  grid-column: 1 / -1;
  text-align: center;
  max-width: none;
}

@media (min-width: 1024px) {
  .hero {
    display: grid;
    grid-template-columns: 1fr 1fr;
    align-items: end;
    gap: var(--grid-gutter);
  }
  .hero-title {
    grid-column: 1 / -1;
    text-align: left;
  }
}
```

---

## UX Audit Findings

| Issue | Heuristic | Severity (0-4) | Fix | Status |
|---|---|---|---|---|
| Tally form embed may block page load | Visibility of System Status | 3 | Lazy-load iframe; show skeleton with "Loading audit form..." | 🔴 Open |
| Mobile CTA buttons need 44x44px min | Flexibility & Efficiency | 2 | Verify all CTAs meet minimum; test on real devices | 🟡 Pending |
| Form validation inline on blur | Error Prevention | 2 | Implement blur validation + ARIA; specific messages ("Enter a valid email") | 🟡 Pending |
| Focus states must be visible + beautiful | Consistency & Standards | 3 | Design on-brand focus rings (WCAG AA); `outline: 2px solid var(--color-focus)` | 🔴 Open |
| Scroll performance on low-end devices | Aesthetic & Minimalist | 2 | Test Lenis on 4G/Android; add `will-change` cleanup after animations | 🟡 Pending |
| Hero text wrapping on narrow screens | Match System to Real World | 2 | Verify `text-wrap: balance` + manual breaks at key breakpoints | 🟢 Done |
| No clear "you are here" on interior pages | Trunk Test / User Control | 3 | Breadcrumbs + highlighted nav + page title matching link | 🔴 Open |
| Search not visible in header | Recognition Over Recall | 3 | Add visible search icon/box in header for goal-driven users | 🔴 Open |
| No undo for form actions | User Control & Freedom | 3 | Add "Undo" toast after form submit; escape to cancel modals | 🟡 Pending |
| Loading states missing for async actions | Visibility of System Status | 3 | Skeleton screens for case studies, spinner for form submit, progress for scroll | 🔴 Open |
| Link text "Click here" used | Don't Make Me Think | 2 | Replace with descriptive text ("View case study: RemoteFirst offboarding") | 🟡 Pending |
| No skip link for keyboard users | User Control & Freedom | 3 | Add hidden skip link (visible on focus) to main content | 🟡 Pending |
| Color-only status indicators | Aesthetic & Minimalist | 2 | Add icon + text labels for success/warning/error states | 🟡 Pending |
| Tiny tap targets on mobile cards | Flexibility & Efficiency | 2 | Ensure card hit areas 44x44px; test touch targets | 🟡 Pending |
| Hover-only case study details | Consistency & Standards | 3 | Move details to visible card or tap-to-expand on mobile | 🔴 Open |
| 404 page not designed | Error Recovery | 2 | Design helpful 404 with search + "Return to audit" link | 🟡 Pending |
| Empty state for no case studies | Recognition Over Recall | 2 | Illustration + "No case studies yet" + CTA to audit | 🟢 Designed |

### Severity Summary
- **Severity 4 (Catastrophic):** 0
- **Severity 3 (Major):** 7 — Fix soon
- **Severity 2 (Minor):** 6 — Schedule fix
- **Severity 1 (Cosmetic):** 2 — Fix if time

### Fix Priority Order
1. Focus states (WCAG) + Skip link (accessibility)
2. Loading states + Lazy-load Tally form
3. Breadcrumbs + Search visibility + "You are here" indicators
4. Undo for form actions + Mobile hover alternatives
5. 404 page + Descriptive link text + Color-only status fix
6. Touch targets + Mobile card interactions

---

## Microinteraction Inventory

| Interaction | Trigger | Rules | Feedback | Loops | Fix | Status |
|---|---|---|---|---|---|---|
| Primary CTA hover | Mouse enter | Magnetic follow (0.3x) | Scale + background sweep | None | Implement GSAP magnetic | 🟡 Pending |
| Primary CTA leave | Mouse leave | Elastic return (0.5s) | Snap back | None | Elastic ease-out | 🟡 Pending |
| Card hover | Mouse enter | Lift -8px, shadow-xl, border-accent | Transform + shadow | None | CSS transition | 🟢 Designed |
| Link underline | Hover/focus | ScaleX 0→1 from center | Color + width | None | CSS ::after | 🟢 Designed |
| Form focus | Focus | Ring-2 ring-focus ring-offset-2 | Visible ring | None | CSS focus-visible | 🟢 Designed |
| Page load | DOM ready | Staggered reveal per choreography | Opacity + transform | Once | GSAP timeline | 🟡 Pending |
| Scroll reveal | Intersection | Fade up 60px | Opacity + transform | Once per element | IO + CSS | 🟡 Pending |
| Nav link hover | Hover | Text color → accent | Color transition | None | CSS transition | 🟢 Designed |
| Custom cursor | Opt-in only | Follows mouse, variants on interactive | Shape change | Continuous | Only if approved | ⚪ Opt-in |

---

## Performance Budget

| Metric | Target | Strategy |
|---|---|---|
| LCP | < 2.5s | Preload hero fonts, optimize hero image (AVIF), minimal critical CSS |
| CLS | < 0.1 | Reserve space for images/fonts, `aspect-ratio` on containers |
| TBT | < 200ms | Defer non-critical JS, code-split, Lenis + GSAP only on interaction |
| Font payload | < 200KB | Variable fonts, Latin subset, `font-display: swap` |
| Total JS | < 100KB gz | Lenis (3KB) + GSAP core (24KB) + custom (10KB) = ~37KB gz |

---

## Accessibility Commitments

- [ ] Semantic HTML5 structure (header, main, section, article, footer)
- [ ] Heading hierarchy (h1 → h2 → h3, no skips)
- [ ] Color contrast AA (4.5:1 text, 3:1 large text/UI)
- [ ] Focus indicators visible + beautiful (WCAG 2.4.7)
- [ ] `prefers-reduced-motion` respected globally
- [ ] Alt text for all images (decorative = empty alt)
- [ ] ARIA labels on icon-only buttons
- [ ] Form labels + error associations (`aria-describedby`)
- [ ] Skip link (hidden until focus)
- [ ] Language declaration (`lang="en"`)