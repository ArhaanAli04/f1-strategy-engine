# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React 19, Tailwind v3, shadcn/ui (Zinc base color), Recharts for charts, Framer Motion available as a dependency but not yet used in any component. TanStack Query for data fetching/caching, Zustand for UI state, react-router-dom v6, react-hook-form for forms.

## Users

Primary users are F1 race engineers, data analysts, and F1 enthusiasts following a race weekend in real time. They are watching a live or upcoming session (practice, qualifying, race) and need to read dense, fast-changing telemetry and strategy data at a glance — timing gaps, tyre compounds, sector times, pit windows, undercut/overcut threats — the same category of information shown on an actual pit wall or broadcast timing screen.

## Product Purpose

A real-time F1 race strategy and telemetry platform. It ingests live timing/telemetry from FastF1, runs ML models (XGBoost/LightGBM tire-degradation and pit-window predictors, a Monte Carlo race simulator) to predict optimal pit windows and undercut/overcut probabilities, and surfaces all of it live during a race weekend. Success is a user being able to glance at the dashboard mid-session and immediately understand track position, tyre state, and the strategic threats/opportunities in play — the way a pit wall engineer would.

## Positioning

Unlike a generic F1 stats or timing-only site, the product's mechanism is live ML-driven strategy prediction layered on top of live telemetry: pit-window predictions with SHAP explanations, undercut/overcut probability scores against specific rivals, and Monte Carlo race simulation for "what pit strategy wins from here" — not just a live gap table.

## Operating Context

Used during an active race weekend (FP1–FP3, Qualifying, Race), often side-by-side with the real broadcast, on desktop-sized screens (web today; a Tauri desktop app and Expo mobile app consume the same backend but are separate, unstyled-by-this-skill clients). Sessions run for periods of 1–2 hours with continuously updating data (8s telemetry polling, WebSocket lap-completion events, 2s driver-position polling for the circuit map).

## Capabilities and Constraints

Key live surfaces: a 22-driver timing tower (position, team color, code, gap-to-leader, tyre compound, updates via WebSocket + polling with a FLIP reorder animation), a sector-time heatmap (22 drivers × 4 time columns: lap/S1/S2/S3, purple/green/yellow session-best/personal-best/slower classification), lap-time line charts colored by tyre compound, a circuit map with live team-colored driver dots plus a 270°-arc SVG telemetry gauge (speed/gear/throttle/brake/DRS), a strategy wall (22-driver grid of pit-window predictions), and a 4-step Monte Carlo strategy simulator.

Constraints: no results/points/standings table exists in the backend, so no season standings, wins, or points are shown from our own data (season stats, when shown, come from a separate public Ergast/Jolpica API call, not the backend). Cold ML-inference paths can take several seconds; the UI must communicate a loading/pending state rather than looking frozen.

## Brand Commitments

Voice/personality: precise, technical, high-stakes, professional — the interface should read like an actual F1 pit wall display or team dashboard, not a consumer product. No existing logo/wordmark beyond team constructor logos (real, licensed-in-spirit team marks used per-driver).

## Evidence on Hand

This is a real, running production system, not a mockup: FastAPI backend with 122 passing backend tests, live FastF1 ingestion, trained ML models, and a Kubernetes/Docker deployment path. The web app already implements and ships all surfaces listed above against live data.

## Product Principles

- Scanability and data density outrank decoration — this is an instrument panel, not a marketing page.
- Every visual choice should read as functional instrumentation (what does this color/motion communicate about the race), never as flourish.
- Real-time correctness and perceived responsiveness matter as much as visual polish; a stale-looking live number is a worse failure than a plain one.
- Match the mental model of real F1 broadcast/pit-wall timing screens (dark, high-contrast, compact rows) over generic dashboard conventions.
