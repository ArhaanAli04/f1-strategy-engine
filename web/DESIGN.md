---
name: F1 Strategy Engine
description: Real-time F1 race strategy and telemetry dashboard for engineers, analysts, and enthusiasts
colors:
  background-near-black: "oklch(0.141 0.005 285.823)"
  card-surface: "oklch(0.21 0.006 285.885)"
  foreground: "oklch(0.985 0 0)"
  muted-foreground: "oklch(0.705 0.015 286.067)"
  border-hairline: "oklch(1 0 0 / 10%)"
  primary-action: "oklch(0.92 0.004 286.32)"
  row-void: "#0a0a0a"
  row-recede: "#141414"
  pill-surface: "#2a2a2a"
  status-positive: "#10B981"
  status-negative: "#EF4444"
typography:
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.4
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.2
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  card:
    backgroundColor: "{colors.card-surface}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.lg}"
    padding: "24px"
  button-primary:
    backgroundColor: "{colors.primary-action}"
    textColor: "{colors.background-near-black}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  timing-row:
    backgroundColor: "{colors.card-surface}"
    textColor: "{colors.foreground}"
    padding: "6px 6px"
---

# Design System: F1 Strategy Engine

## Overview

**Creative North Star: "The Pit Wall Terminal"**

This is instrumentation, not a marketing surface — a pit-wall engineer's live display, not a consumer dashboard. The canvas is near-black by default (shadcn's Zinc dark `--background`, oklch(0.141 0.005 285.823) ≈ #09090b) and stays out of the way; color is not decoration, it is data — a driver's team hex, a tyre compound's FIA color, a gain/loss sign. Density is a feature: the timing tower holds 22 drivers × 5 fields, the sector heatmap holds 22 × 4 cells, and nothing here is allowed to ask for more room than a real broadcast timing screen would give it.

The system today is genuinely flat: shadcn's default `Card` uses only `shadow-sm`, and most visual separation comes from 10%-opacity hairline borders and background-tone steps (near-black → card-surface → pill-surface), not elevation. Motion is scarce and earns its place — a FLIP reorder when timing-tower positions change, a 1.8s linear glide when a circuit-map dot moves, `isAnimationActive={false}` on every Recharts chart. Nothing animates just to look alive.

**Key Characteristics:**
- Near-black canvas, data supplies the color, not the chrome
- Flat by default — hairline borders and tone steps over shadows
- Dense, tabular, broadcast-timing-screen layout
- Motion only where it tracks a real state change (position, live value)

## Colors

The palette is intentionally quiet: one neutral zinc scale for every surface and control, with color reserved entirely for what the data means.

### Primary
- **Primary Action** (oklch(0.92 0.004 286.32) ≈ #e7e7ea): shadcn's default primary token — near-white on near-black, used sparingly for primary buttons and focus rings. Not a "brand color"; the system deliberately has none.

### Neutral
- **Void** (oklch(0.141 0.005 285.823) ≈ #09090b): the base canvas (`--background`), and the app-wide `#0a0a0a` used directly for the timing tower's darkest zebra row — these two are visually identical and should be treated as the same color.
- **Recede** (`#141414`): the lighter zebra-stripe row in the sector heatmap and timing tower — one step up from Void.
- **Card Surface** (oklch(0.21 0.006 285.885) ≈ #242429): the shadcn `Card` background — the system's one "elevated" tone.
- **Pill Surface** (`#2a2a2a`): background for compact inline chips — the sector-time pill, the tyre icon disc. One step up from Card Surface.
- **Foreground** (oklch(0.985 0 0) ≈ #fcfcfc): primary text.
- **Muted Foreground** (oklch(0.705 0.015 286.067) ≈ #a8a8ad): secondary/label text, positions, timestamps.
- **Hairline Border** (oklch(1 0 0 / 10%), 10% white): the system's only structural separator between rows, panels, and cards.

### Data Colors (not design-system tokens — sourced from `utils/constants.ts` / the backend's team seed data)
- **Tyre compounds:** SOFT `#DA291C`, MEDIUM `#FFD12E`, HARD `#F0F0F0`, INTERMEDIATE `#43B02A`, WET `#0067AD` — real FIA broadcast colors, never restyled.
- **Team colors:** one hex per constructor (e.g. McLaren `#FF8000`, Ferrari `#E8002D`, Mercedes `#27F4D2`), applied to driver chips, timing-tower bars, and team logo tinting.
- **Status:** gain `#10B981` (emerald), loss `#EF4444` (red) — position-change bars in the strategy simulator.

### Named Rules
**The Data Supplies Color Rule.** Chrome (backgrounds, borders, body text) stays neutral zinc. The only colors on screen come from what the data means — a team, a compound, a gain or a loss. A UI element should never be colored "for branding"; if it's colored, ask what fact it's reporting.

## Typography

**Body Font:** ui-sans-serif / system-ui stack (no custom font is loaded — this is an honest gap, not a considered choice; a display face for headers is a reasonable future addition, not yet made).
**Data Font:** ui-monospace / SFMono-Regular / Menlo — used with `tabular-nums` on every numeric field that updates live (13 occurrences across 7 components: gaps, lap times, sector times, position counts, season stats).

**Character:** Plain and functional. The one deliberate typographic move is numeric, not decorative.

### Hierarchy
- **Title** (600–700 weight, `text-xl`–`text-2xl`): page headers (e.g. `DriverPage`'s driver name).
- **Body** (400 weight, `text-sm`): default UI text, labels, descriptions.
- **Label** (500–600 weight, `text-[10px]`–`text-xs`, uppercase + `tracking-wide` on stat labels): field labels, section headers (e.g. "WINS", "SECTOR VARIANCE").
- **Data** (monospace, `tabular-nums`, `text-xs`–`text-sm`): every live-updating number — lap times, gaps, sector times, positions.

### Named Rules
**The Tabular Numerals Rule.** Any number that can change between renders (a gap, a lap time, a position) is `font-mono tabular-nums`, full stop — so a value updating in place never causes neighboring text to reflow.

## Layout

Two shapes dominate: a fixed-width sidebar timing tower (`w-60`, one row per driver, `justify-between` fixed-width fields) and a scrollable main content column (`h-full overflow-y-auto`, `mx-auto max-w-4xl`/`max-w-6xl` content width). Row height is compact and consistent (`py-1.5`–`py-2`) — density is chosen deliberately over breathing room. Grids (driver roster, strategy wall) use `grid-cols-2` through `grid-cols-6` responsive steps, not card-per-row lists, to keep 22-driver surfaces scannable without excess scrolling.

## Elevation & Depth

Flat by default. `Card` carries only `shadow-sm`; depth is communicated by tone-stepping (Void → Recede → Card Surface → Pill Surface) and 10%-opacity hairline borders, not shadow. This matches a broadcast timing screen, which has no "elevation" concept at all.

### Named Rules
**The Flat-By-Default Rule.** Don't reach for a shadow to separate two surfaces. Step the background tone or add a hairline border first; a shadow on this canvas reads as a foreign, generic-SaaS tell.

## Shapes

Corners are modest and consistent: `rounded-lg` (10px) on cards, `rounded-md` (8px) on buttons/inputs, `rounded-full` on chips, dots, and the tyre icon disc. No large, illustrative border-radii — this is instrumentation, not a soft consumer surface.

## Components

### Buttons
- **Shape:** `rounded-md` (8px).
- **Primary:** near-white background, near-black text (`{colors.primary-action}` / `{colors.background-near-black}`), `px-4 py-2`.
- **Hover / Focus:** `hover:bg-primary/90`; focus ring via shadcn's `--ring` token, 2px offset.
- **Ghost / Outline:** transparent or bordered, `hover:bg-accent` — used for secondary actions (e.g. "Try Again", pit-stop row remove).

### Cards / Containers
- **Corner Style:** `rounded-lg` (10px).
- **Background:** Card Surface (oklch(0.21 0.006 285.885)).
- **Shadow Strategy:** `shadow-sm` only — see Elevation & Depth.
- **Border:** hairline (10% white) on most cards; borderless on nested/flat contexts like the timing tower.

### Data Rows (signature component — timing tower / sector heatmap)
The system's most distinctive pattern: a dense, fixed-field-width row (`flex justify-between`) with a team-color bar or logo as the leading identity marker, `tabular-nums` monospace values right-aligned, and zebra striping between Void and Recede. Selected/active rows use a `ring-2 ring-ring` treatment rather than a background-color change, so the zebra pattern underneath stays legible.

### Inputs / Fields
- **Style:** `border-input` hairline, `bg-background`, `rounded-md`.
- **Focus:** 2px `ring` in the shadcn `--ring` token, offset from the border.

### Navigation
No persistent top navbar exists yet — `NavBar.tsx` is a minimal header (title + alerts bell) shown on every authenticated route via `AuthGuard`. See the nav-architecture audit for whether this needs to grow (settings icon, dashboard link, login/logout affordance).

## Do's and Don'ts

### Do:
- **Do** keep the canvas near-black (`{colors.background-near-black}`) and let team/compound/status colors carry all the meaning.
- **Do** use `tabular-nums` monospace on every value that updates live.
- **Do** step background tone (Void → Recede → Card Surface → Pill Surface) instead of reaching for a shadow.
- **Do** animate only real state changes (timing-tower reorder, circuit-map dot movement) — `isAnimationActive={false}` is the default on charts.

### Don't:
- **Don't** introduce gradients, especially purple-to-blue — no gradient exists anywhere in the current system and none should be added.
- **Don't** build generic SaaS/Material-style nested "card-in-card" containers; this system is flat rows and hairlines, not stacked elevation.
- **Don't** use light-mode data tables or components — the app is permanently dark (`class="dark"` on `<html>`, no toggle).
- **Don't** add decorative motion (bounce/elastic easing, hover-scale flourishes, spinner-style loading gimmicks) — every animation must track a real, meaningful state change.
