# desktop/src — Web Sync Notes

No monorepo/symlink sharing between `web/` and `desktop/` (unreliable on
Windows — see CLAUDE.md's Day 30 setup notes). The files below are manual
copies (or, where noted, hand-written re-implementations of the same logic)
and must be checked by hand whenever their `web/` source changes.

## Verbatim copies — re-copy on change

| desktop/src path | web/ source |
|---|---|
| `types/*.ts` (10 files) | `web/src/types/*.ts` |
| `api/*.ts` (9 files) | `web/src/api/*.ts` |
| `utils/constants.ts` | `web/src/utils/constants.ts` |
| `utils/errors.ts` | `web/src/utils/errors.ts` |
| `utils/formatters.ts` | `web/src/utils/formatters.ts` |
| `utils/drivers.ts` | `web/src/utils/drivers.ts` |
| `stores/authStore.ts` | `web/src/stores/authStore.ts` |
| `lib/utils.ts` | `web/src/lib/utils.ts` |
| `components/ui/*.tsx` (11 files: button, card, checkbox, dialog, form, input, label, select, separator, sonner, switch) | `web/src/components/ui/*.tsx` |
| `components/shared/ErrorBoundary.tsx` | `web/src/components/shared/ErrorBoundary.tsx` |
| `components/circuit/AnimatedDriverDots.tsx` | `web/src/components/circuit/AnimatedDriverDots.tsx` (render-behind interpolation buffer for live dots; delay derives from `hooks/useDriverPositions.ts`'s `POSITIONS_POLL_INTERVAL_MS`, which is 2s on desktop vs 1s on web) |
| `components/strategy/StrategyOverviewGrid.tsx` | `web/src/components/strategy/StrategyOverviewGrid.tsx` (byte-identical — never previously listed here, an existing documentation gap closed alongside the Checkpoint 5 `PitWindowCard.tsx` work below, not something that changed this session) |
| `index.css` | `web/src/index.css` |
| `../tailwind.config.js` | `web/tailwind.config.js` |
| `../postcss.config.js` | `web/postcss.config.js` |
| `../public/fonts/*.woff2` (3 files) | `web/public/fonts/*.woff2` |
| `../public/favicon.svg` | `web/public/favicon.svg` |

## Copied and adapted — re-diff on change, don't blind-overwrite

| desktop/src path | web/ source | What's different |
|---|---|---|
| `pages/SimulatorPage.tsx` | `web/src/pages/SimulatorPage.tsx` | `useCurrentRace` live-mode detection removed entirely (no `/race/:sessionId` route to arrive from in desktop); `useSessionStore` → `useRaceContextStore`; session/driver both prefill from race context instead of just session; adds a desktop-only "Export Results" CSV button in Step 4. All 4-step wizard logic, `PlanExplanationCard`, and the Recharts bar chart are otherwise identical. |
| `components/strategy/PitWindowCard.tsx` | `web/src/components/strategy/PitWindowCard.tsx` | Same recommended_compound/confidence_score/explanation.narrative fields (Checkpoint 5, core-feature-rebuild — replaced the old client-side SHAP top-contribution formatting). Deliberately simpler than web's post-Checkpoint-5 version: always sources from `usePitWindow`'s on-demand REST recompute — desktop has no WebSocket lap-completion stream anywhere in its codebase (confirmed: it's REST-polling throughout, e.g. `hooks/useDriverPositions.ts`/`useSessionGaps.ts`/`useLiveDriverTelemetry.ts`), so there's no `isReplayActive`/`usePitRecommendation`-style unification to port — web's own pre-Checkpoint-5 replay branch never existed here either, which is why this file needed no branch removed, only the response-shape update. Placed as a full (non-compact) card at the top of `LiveRacePage.tsx`'s right rail for the selected driver, mirroring web's `RacePage.tsx`. |

## New files — not copies, but mirror web hook logic

These were hand-written for desktop (hooks aren't copied — see CLAUDE.md's
Day 30 shared-code-strategy note) but replicate the same react-query
patterns as their web equivalents. If the web hook's logic changes
(cache keys, poll intervals, response shape handling), check these too.

| desktop/src path | web/ logic mirrored |
|---|---|
| `hooks/useDrivers.ts` | `web/src/hooks/useDrivers.ts` |
| `hooks/useSessionGaps.ts` | `web/src/hooks/useSessionGaps.ts` |
| `hooks/useDriverLaps.ts` | `web/src/hooks/useDriverLaps.ts` |
| `hooks/useStrategy.ts` | `web/src/hooks/useStrategy.ts` (desktop's `useUndercut` additionally polls every 15s, for the notification hook) |
| `hooks/useAuth.ts` | `web/src/hooks/useAuth.ts` (desktop-local: login only, no register/logout/updateProfile/changePassword — no UI for those yet) |

## Desktop-only, no web equivalent

`stores/raceContextStore.ts`, `hooks/useRaceContextBridge.ts`,
`hooks/useNeighborDrivers.ts`, `hooks/useTrayStatus.ts`,
`hooks/useUndercutNotifications.ts`, `components/overlay/RaceOverlay.tsx`,
`utils/csvExport.ts`, `pages/LoginPage.tsx` (desktop-local, no router).
