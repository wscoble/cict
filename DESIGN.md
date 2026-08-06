---
version: 1
name: cict-design-language
description: >
  A calm, utilitarian design language for a single-user cost-of-goods inventory
  tracker. Anchored on a navy brand, a light neutral canvas, and a small set of
  semantic components (page-header, card, table, kv, empty, btn) driven by CSS
  custom-property tokens. Built for progressive enhancement (Go + htmx): pages
  work as plain HTML, htmx swaps the body in place. No build step, no JS
  framework, no Tailwind — semantic HTML + one stylesheet. The aesthetic is
  "competent tool, not SaaS template": flat color, thin borders, subtle
  shadows, generous whitespace, money values colored only when they carry
  meaning.

colors:
  brand: "#1a3a5c"
  brand-dark: "#15304d"
  brand-soft: "#cde"
  canvas: "#f5f7fa"
  surface: "#ffffff"
  surface-2: "#f8fafc"
  surface-head: "#f0f4f8"
  border: "#e2e8f0"
  border-strong: "#cbd5e1"
  ink: "#1f2937"
  body: "#1f2937"
  muted: "#64748b"
  positive: "#15803d"
  positive-bg: "#dcfce7"
  positive-border: "#86efac"
  negative: "#b91c1c"
  negative-bg: "#fee2e2"
  negative-border: "#fca5a5"
  warning: "#b45309"
  on-primary: "#ffffff"

typography:
  display:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.2
  h1:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.3
  h2:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 400
    lineHeight: 1.5
  button:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.85rem"
    fontWeight: 500
    lineHeight: 1.4

rounded:
  xs: 4px
  sm: 6px
  md: 8px

spacing:
  "1": 0.25rem
  "2": 0.5rem
  "3": 0.75rem
  "4": 1rem
  "5": 1.5rem
  "6": 2rem

shadow:
  card: "0 1px 2px rgba(15,23,42,0.05)"
  raised: "0 1px 3px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.04)"

components:
  page-header:
    description: "h1 + optional page-actions row, top of every list/form page"
    layout: "flex; align-items: center; justify-content: space-between; gap: {spacing.3}; flex-wrap: wrap"
    margin-bottom: "{spacing.4}"
  page-actions:
    description: "right-aligned cluster of action buttons"
    layout: "flex; gap: {spacing.2}; flex-wrap: wrap"
  card:
    backgroundColor: "{colors.surface}"
    border: "1px solid {colors.border}"
    rounded: "{rounded.md}"
    padding: "{spacing.4}"
    margin-bottom: "{spacing.4}"
    shadow: "{shadow.card}"
  card-grid:
    layout: "grid; repeat(auto-fill, minmax(200px, 1fr)); gap: {spacing.4}"
  stat:
    description: "centered metric inside a card"
    textAlign: center
  stat-value:
    color: "{colors.brand}"
    fontSize: "2rem"
    fontWeight: 700
  stat-label:
    color: "{colors.muted}"
    fontSize: "{typography.small.fontSize}"
    marginTop: "{spacing.1}"
  table:
    width: 100%
    backgroundColor: "{colors.surface}"
    border: "1px solid {colors.border}"
    rounded: "{rounded.md}"
    shadow: "{shadow.card}"
  th:
    backgroundColor: "{colors.surface-head}"
    fontWeight: 600
    fontSize: "{typography.small.fontSize}"
  row-hover:
    backgroundColor: "{colors.surface-2}"
  btn:
    padding: "{spacing.2} {spacing.3}"
    rounded: "{rounded.xs}"
    fontSize: "{typography.button.fontSize}"
    border: "1px solid {colors.border-strong}"
    backgroundColor: "{colors.surface}"
    color: "{colors.ink}"
  btn-primary:
    backgroundColor: "{colors.brand}"
    color: "{colors.on-primary}"
    border-color: "{colors.brand}"
  btn-primary-hover:
    backgroundColor: "{colors.brand-dark}"
  btn-danger:
    color: "{colors.negative}"
    border-color: "{colors.negative-bg}"
  btn-danger-hover:
    backgroundColor: "{colors.negative-bg}"
  btn-sm:
    padding: "{spacing.1} {spacing.2}"
    fontSize: "0.8rem"
  kv:
    description: "definition list (dl/dt/dd) for detail key/value panes"
    layout: "grid; grid-template-columns: max-content 1fr; gap: {spacing.2} {spacing.4}"
  empty:
    description: "centered empty state (muted message + primary action)"
    textAlign: center
    padding: "{spacing.6} {spacing.4}"
    color: "{colors.muted}"
  metric-pos:
    color: "{colors.positive}"
    fontWeight: 600
  metric-neg:
    color: "{colors.negative}"
    fontWeight: 600
  form-actions:
    description: "submit + cancel row at the bottom of every form"
    layout: "flex; gap: {spacing.2}; margin-top: {spacing.4}"
  flash-success:
    backgroundColor: "{colors.positive-bg}"
    color: "{colors.positive}"
  flash-error:
    backgroundColor: "{colors.negative-bg}"
    color: "{colors.negative}"
  nav:
    backgroundColor: "{colors.brand}"
    color: "{colors.on-primary}"
    padding: "{spacing.3} 0"
  nav-link:
    color: "{colors.brand-soft}"
    fontWeight: 500
  nav-link-hover:
    color: "{colors.on-primary}"
---

# CICT Design Language

A cost-of-goods inventory tracker for a single maker. It tracks ingredients,
computes per-unit cost, records sales, and shows margins. The user is one
person running a small batch operation — they want a tool that gets out of the
way, not a "platform." Every screen should answer one question fast and let
them move on.

## Overview

CICT is **progressive enhancement over semantic HTML**: every page is a real
HTML document that works without JavaScript, and htmx (`hx-boost` on `<body>`)
swaps the body in place on link/form clicks so navigation feels snappy without a
full reload. There is no client framework, no Tailwind, no build step — one
`<style>` block of tokens and components in `templates/base.html`, plus semantic
HTML in each page template.

The design language is **a tool, not a SaaS template**. Flat color, thin
borders, subtle shadows, generous whitespace. Money is colored *only* when the
sign carries meaning (gross profit positive/negative, margin shown). Decoration
that doesn't carry information is removed. The nav is a single navy bar; the
canvas is a light neutral; cards are the grouping unit.

## Colors

### Brand
- `brand` `#1a3a5c` — primary actions, nav, stat values, links. One brand color, used everywhere something needs to feel "CICT."
- `brand-dark` `#15304d` — primary button hover/active only.
- `brand-soft` `#cde` — nav link text (against navy).

### Surface
- `canvas` `#f5f7fa` — page background. A *cool-toned* off-white, not pure `#fff` (pure white reads as cold/unfinished).
- `surface` `#ffffff` — cards, tables, inputs.
- `surface-2` `#f8fafc` — table row hover, ghost-button hover.
- `surface-head` `#f0f4f8` — table `<th>` background.

### Text
- `ink`/`body` `#1f2937` — primary text.
- `muted` `#64748b` — labels, captions, empty-state copy.

### Semantic
- `positive` `#15803d` + `positive-bg` `#dcfce7` — money positive, success flash.
- `negative` `#b91c1c` + `negative-bg` `#fee2e2` — money negative, danger button, error flash.
- `warning` `#b45309` — reserved (currently unused; for low-stock warnings).

**Discipline:** every color traces to a token. No inline hex in templates. Five
different blues across one file means colors were invented inline — consolidate
to the token.

## Typography

### Font Family
A single system stack — `system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`.
No web font is loaded. The tool reads as native to whatever platform the user is
on; a custom display face would be costume on a utility app.

### Hierarchy
- `display` 2rem/700 — stat values (the numbers that matter).
- `h1` 1.5rem/700 — page title (one per page).
- `h2` 1.15rem/600 — section title within a page.
- `body` 0.95rem/400 — default text, table cells, form labels.
- `small` 0.85rem — muted captions, table headers, buttons.
- `button` 0.85rem/500 — button labels.

### Principles
- **One family, weight+size do the work.** No second typeface; hierarchy is size and weight, not face.
- **Numbers lead.** The most important value on a stat card is the number, not the label — so the number is `display` weight and the label is `small` muted.
- **Don't shout.** No all-caps headers, no tracking-out on labels. A utility app earns trust by being quiet.

## Layout

### Spacing System
A 4px-rooted scale: `1` 0.25rem (4px), `2` 0.5rem (8px), `3` 0.75rem (12px), `4`
1rem (16px), `5` 1.5rem (24px), `6` 2rem (32px). Every margin/padding/gap is a
multiple of 4px. Off-scale values (`padding: 7px 15px`) feel chaotic — snap to
the scale.

### Grid & Container
A single `.container` at `max-width: 960px`, centered, `0 1rem` padding. CICT is
a single-user desktop tool; no multi-column marketing layout, no sidebar. The
960px width keeps tables readable without sprawling.

### Whitespace Philosophy
Generous. A page-header gets `margin-bottom: 1rem`; a card gets `margin-bottom:
1rem`; h2 gets `margin-top: 1.5rem`. Whitespace separates sections so the eye
doesn't have to work. A cramped tool feels stressful; a calm one feels
trustworthy.

## Elevation & Depth

Two shadows only:
- `shadow.card` `0 1px 2px rgba(15,23,42,0.05)` — cards, tables (resting state).
- `shadow.raised` — reserved (currently unused; for modals/popovers if added).

Shadows are subtle. A card is separated from the canvas by a thin border *and*
a 1px shadow; never a heavy drop shadow. Depth communicates "this is a
grouping," not "this is floating above the page."

## Shapes

### Border Radius Scale
- `xs` 4px — buttons, inputs, small chips.
- `sm` 6px — reserved.
- `md` 8px — cards, tables.

No `12px`+ radii. A utility tool with big rounded corners reads as a toy. 4–8px
is the range.

## Components

### Top Navigation
A single navy bar (`brand`), full-width, `0.75rem` vertical padding. Brand wordmark
left, four text links right (Dashboard, Inventory, Sales, Summary). Links are
`brand-soft`, hover → white. **Known gap:** no active-state indicator on the
current section — see Iteration Guide.

### Buttons
Four roles, one shape (`rounded.xs`):
- **Primary** (`btn-primary`) — navy fill, white text. One per page, the main
  action (Create, Save, Record). Hover → `brand-dark`.
- **Default** (`btn`) — white fill, `border-strong`, `ink` text. Secondary
  actions (Cancel, Edit, View).
- **Danger** (`btn-danger`) — white fill, `negative` text, `negative-bg` border.
  Destructive actions (Delete). Hover → `negative-bg` fill. Never a red fill —
  red fill shouts; a red-bordered button warns.
- **Ghost** (`btn-ghost`) — transparent, `brand` text. In-table links.

**Size:** `btn` 0.5/0.75rem padding; `btn-sm` 0.25/0.5rem for in-table actions.
**Discipline:** one primary per page. If two actions feel equally primary, one
isn't — demote it.

### Cards
`.card` — `surface` fill, 1px `border`, `rounded.md`, `shadow.card`, 1rem
padding. The grouping unit: forms, stat clusters, and the detail pane all live in
cards. A card with a border-top action row uses a top border to separate
actions from content.

### Inputs & Forms
Forms live inside a `.card`. Labels are `small` weight 500, `0.75rem` top margin
(first label flush). Inputs/select/textarea are full-width, 0.5/0.75rem
padding, 1px `border-strong`, `rounded.xs`. Focus → 2px `brand` outline + brand
border. A `.form-actions` row (flex gap, 1rem top margin) holds Submit (primary)
+ Cancel (default).

### Key/Value Pane (`.kv`)
Detail pages use a `<dl class="kv">` — a 2-column grid (`max-content 1fr`,
`0.5rem`/`1rem` gap): `dt` is the label (weight 600), `dd` is the value. Replaces
ad-hoc `<p><strong>K:</strong> V</p>` stacks. Missing values are `muted` ("None",
"N/A", "—"), not absent.

### Tables
Full-width, `surface` fill, 1px `border`, `rounded.md` (overflow hidden so the
radius clips rows), `shadow.card`. `th` is `surface-head`, `small`, weight 600.
Row hover → `surface-2`. Numeric columns are `text-right`. Last row has no
bottom border. Cell links are `brand`, weight 500.

### Empty States (`.empty`)
A real centered component, not a muted paragraph: `2rem`/`1rem` padding, `muted`
copy, then a `btn-primary` (or `btn-sm btn-primary` for sub-sections) that takes
the user to the creating page. "No inventory items yet." + "Add your first
item." Every empty surface has a path forward.

### Money / Metrics
`.metric--pos` (positive green, weight 600) and `.metric--neg` (negative red,
weight 600) color a money value **only** when the sign carries meaning: gross
profit (revenue − cost), margin. Do not color every dollar value — color is
signal, not decoration.

## Do's and Don'ts

### Do
- Trace every color, size, and radius to a token.
- Put one primary action per page; demote the rest to default.
- Wrap every form in a card; every detail pane in a card.
- Use `.kv` for key/value panes; use `.empty` for empty lists.
- Color money only when the sign matters.
- Snap all spacing to the 4px scale.
- Keep tables readable: numeric columns right-aligned, hover row, no zebra striping.

### Don't
- Don't use gradients. Flat color from the palette, always.
- Don't prepend emoji to headings, buttons, or list items.
- Don't use `border-left: 4px solid` as a default card style — that's the SaaS-template tell. Reserve left borders for callouts/alerts.
- Don't use pure `#FFFFFF` on `#000000` — the canvas is a toned off-white, text is a toned near-black.
- Don't invent a second typeface. One family, weight+size do the work.
- Don't radius past 8px. Big radii read as a toy.
- Don't color every dollar value. Color is signal, not decoration.
- Don't float a form bare on the canvas — wrap it in a card.

## Responsive Behavior

### Breakpoints
CICT is desktop-first (single-user tool). The `.container` is `max-width: 960px`
and the `.page-header`/`.page-actions`/`.card-grid` all `flex-wrap: wrap`, so the
layout degrades gracefully on narrow viewports without dedicated breakpoints.
No mobile-specific layout is designed yet — see Known Gaps.

### Touch Targets
Buttons are `0.5rem`/`0.75rem` padding ≈ 32px tall — under the 44px touch minimum.
On a touch surface this is a known gap; on desktop with a mouse it's fine. See
Iteration Guide.

### Collapsing Strategy
The `.page-header` wraps the actions below the title on narrow widths
(`flex-wrap: wrap`). The `.card-grid` collapses to one column below `~200px`
minmax. Tables scroll horizontally if the viewport is narrower than the table
(not yet wrapped — see Known Gaps).

## Iteration Guide

When changing the design:
1. **Change a token, not a value.** Edit `--brand` in `base.html`; don't recolor
   one button. Token changes propagate; value changes diverge.
2. **Add a component, not a one-off.** If a third page needs the same shape,
   it's a component — name it, add it to this doc, use it everywhere.
3. **Run the design-review skill before shipping UI.** The `ai-slop-check` +
   `polish-pass` skills (vendored in the factory) catch tropes and gaps this doc
   can't foresee.
4. **Update this doc when the language changes.** A DESIGN.md that drifts from
   the CSS is worse than no doc — it lies to the builder.

## Known Gaps

Closed by the design-review pass (ai-slop-check + polish-pass):
- ~~No nav active-state~~ → fixed via inline `aria-current` script + CSS.
- ~~No `:focus-visible` styling~~ → fixed (2px brand outline, 2px offset on all interactive elements).
- ~~No `prefers-reduced-motion`~~ → fixed (defensive media query kills future transitions).
- ~~Touch targets under 44px~~ → fixed via `@media (pointer: coarse)` bump.
- ~~No `:active`/`:disabled` on buttons~~ → fixed.
- ~~Duplicate primary on dashboard~~ → fixed (quick-actions card demoted to default).
- ~~Flash border colors were inline hex (ai-slop Rule 7)~~ → fixed (`--positive-border`/`--negative-border` tokens).

These remain:
- **No dark mode.** The token set is light-only. A `prefers-color-scheme: dark`
  override is a future token pass.
- **No mobile layout.** Tables scroll horizontally on phones; no card-collapse
  for wide tables.