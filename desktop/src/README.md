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
| `hooks/useRaceBySession.ts`, `hooks/useCountdown.ts` | `web/src/hooks/` same names (Day 6b CP6 — byte-identical; needed by the ported `CircuitMapPanel`) |
| `hooks/useDemoReplay.ts` | `web/src/hooks/useDemoReplay.ts` (Day 6b CP5 — byte-identical; `types/demo.ts`/`api/demo.ts` are covered by the `types/*`/`api/*` rows) |
| `hooks/useLiveTelemetry.ts` | `web/src/hooks/useLiveTelemetry.ts` (Day 6b CP4 — byte-identical; it imports desktop's own `hooks/useWebSocket.ts`, below) |
| `components/telemetry/LapTimeChart.tsx` | `web/src/components/telemetry/LapTimeChart.tsx` (Day 6b CP4: stops at the current lap during a replay) |
| `components/strategy/PitWindowCard.tsx` | `web/src/components/strategy/PitWindowCard.tsx` (Day 6b CP4: was an adapted, REST-only copy; now web's `usePitRecommendation` version — stored predictions during a replay) |
| `components/strategy/UndercutThreatPanel.tsx` | `web/src/components/strategy/UndercutThreatPanel.tsx` (Day 6b CP4: gains web's replay rows) |
| `components/shared/HistoricalDataBanner.tsx` | `web/src/components/shared/HistoricalDataBanner.tsx` (since Day 6b: desktop now knows the race name/date, so the old adapted copy without them was replaced) |
| `hooks/useResolvedSession.ts` | `web/src/hooks/useResolvedSession.ts` (Day 6b — a hook, but byte-identical: no browser or window concern in it) |
| `hooks/useLastIngestedSession.ts` | `web/src/hooks/useLastIngestedSession.ts` (Day 6b — same) |
| `index.css` | `web/src/index.css` |
| `../tailwind.config.js` | `web/tailwind.config.js` |
| `../postcss.config.js` | `web/postcss.config.js` |
| `../public/fonts/*.woff2` (3 files) | `web/public/fonts/*.woff2` |
| `../public/favicon.svg` | `web/public/favicon.svg` |

## Copied and adapted — re-diff on change, don't blind-overwrite

| desktop/src path | web/ source | What's different |
|---|---|---|
| `pages/SimulatorPage.tsx` | `web/src/pages/SimulatorPage.tsx` | Session (Day 6b): same as web — the live race when it has data, else the last ingested race, read-only — except the Dashboard's `sessionOverride` wins when set (web's `useSessionStore.selectedSessionId` plays that role there). Driver prefills from `raceContextStore.driverId`. Adds a desktop-only "Export Results" CSV button in Step 4. All 4-step wizard logic, `PlanExplanationCard`, and the Recharts bar chart are otherwise identical. |
| `components/circuit/CircuitMapPanel.tsx` | `web/src/components/circuit/CircuitMapPanel.tsx` | Day 6b CP6: web's version (outline from the session's own race via `useRaceBySession`, "historical" mode for an explicit session) with the `liveRaceSelectionStore` swap. Desktop passes `isExplicitSession` = a Dashboard override or a running replay. The old desktop copy always drew the upcoming race's circuit, so a replay showed the wrong track. |
| `main.tsx` | `web/src/main.tsx` | Day 6b CP6: same `QueryCache.onError` (honours `silentOn404`/`silentOn503`); otherwise desktop's own entry (Tauri overlay window, no router). Before, desktop toasted every failed query, e.g. "No live telemetry cached for car N" each poll during a replay. |
| `components/demo/ReplaySelectorPanel.tsx` | `web/src/components/demo/ReplaySelectorPanel.tsx` | Day 6b CP5: no router, so no `navigate(ROUTES.race(...))` after starting a replay — the panel is on `LiveRacePage` already, and `hooks/useRaceSession.ts` switches every page to the running replay's session. Otherwise identical. |
| `components/telemetry/LiveTimingTower.tsx` | `web/src/components/telemetry/LiveTimingTower.tsx` | Day 6b CP4: web's version (WebSocket last lap and tyre, stale-connection notice) with one swap: selection comes from `liveRaceSelectionStore` instead of web's `sessionStore`. |
| `components/telemetry/SectorHeatmap.tsx` | `web/src/components/telemetry/SectorHeatmap.tsx` | Day 6b CP4: web's version (stops at each driver's current lap during a replay) with the same `liveRaceSelectionStore` swap. |

## New files — not copies, but mirror web hook logic

These were hand-written for desktop (hooks aren't copied — see CLAUDE.md's
Day 30 shared-code-strategy note) but replicate the same react-query
patterns as their web equivalents. If the web hook's logic changes
(cache keys, poll intervals, response shape handling), check these too.

| desktop/src path | web/ logic mirrored |
|---|---|
| `hooks/useCurrentRace.ts` | `web/src/hooks/useCurrentRace.ts` (Day 6b; identical logic) |
| `hooks/useDrivers.ts` | `web/src/hooks/useDrivers.ts` |
| `hooks/useSessionGaps.ts` | `web/src/hooks/useSessionGaps.ts` |
| `hooks/useDriverLaps.ts` | `web/src/hooks/useDriverLaps.ts` |
| `hooks/useStrategy.ts` | `web/src/hooks/useStrategy.ts` — since Day 6b CP4 a copy of web's (including `useCurrentLapHistoryEntry`/`usePitRecommendation`) with one difference: desktop's `useUndercut` also polls every 15s, for the notification hook |
| `hooks/useWebSocket.ts` | `web/src/hooks/useWebSocket.ts` (Day 6b CP4) — same interface (`readyState`, `send`; no `lastCloseEvent`), but the WebView's built-in `WebSocket` with hand-written reconnect (1 s doubling to 30 s, reset on open) instead of the `reconnecting-websocket` package, so no new dependency |
| `hooks/useAuth.ts` | `web/src/hooks/useAuth.ts` (desktop-local: login only, no register/logout/updateProfile/changePassword — no UI for those yet) |

## Desktop-only, no web equivalent

`stores/raceContextStore.ts` (since Day 6b: only the optional session
override and "your driver", both saved across restarts),
`hooks/useRaceContextBridge.ts`, `hooks/useRaceSession.ts` (Day 6b: the
session every page uses — the override, else a running Demo Replay's
session, else `useResolvedSession`'s live or most recent completed race;
web pages use `useResolvedSession` directly and web's race page navigates
to a replay's session instead),
`hooks/useNeighborDrivers.ts`, `hooks/useTrayStatus.ts`,
`hooks/useUndercutNotifications.ts` (since Day 6b CP4: during a replay it
reads the stored prediction for the current lap, like the undercut panel,
and makes no live `/undercut` calls), `components/overlay/RaceOverlay.tsx`,
`utils/csvExport.ts`, `pages/LoginPage.tsx` (desktop-local, no router).
