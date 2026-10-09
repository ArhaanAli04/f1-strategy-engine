# mobile/src — Web Sync Notes

No monorepo/symlink sharing between `web/` and `mobile/` — same reasoning as
`desktop/src/README.md` (unreliable on Windows, see CLAUDE.md's Day 30 setup
notes). The files below are manual copies (or, where noted, hand-written
re-implementations of the same logic) and must be checked by hand whenever
their `web/` source changes.

## Verbatim copies — re-copy on change

**Drift audit, 2026-10-07; all re-synced by 2026-10-09:** five of these had
stopped matching web. `types/simulate.ts`, `api/strategy.ts` and
`utils/formatters.ts` were re-copied for the Simulator rework (2026-10-07);
`types/index.ts` and `types/telemetry.ts` (`DriverGap`'s nullable gaps,
`laps_behind` and `compound`), plus the new `types/demo.ts` and
`api/demo.ts`, in Day 6b-mobile CP1 (2026-10-09). Every file in this table
now matches web byte for byte.

| mobile/src path | web/ source |
|---|---|
| `types/*.ts` (11 files) | `web/src/types/*.ts` |
| `api/alerts.ts`, `auth.ts`, `circuit.ts`, `demo.ts`, `driver.ts`, `race.ts`, `strategy.ts`, `telemetry.ts` (8 files) | `web/src/api/*.ts` (same filenames). `race.ts` was re-synced 2026-10-07 to get `getRaceBySession`; `demo.ts` added 2026-10-09 (Day 6b-mobile CP1). |
| `utils/errors.ts` | `web/src/utils/errors.ts` |
| `utils/formatters.ts` | `web/src/utils/formatters.ts` |
| `utils/drivers.ts` | `web/src/utils/drivers.ts` |
| `utils/ergastDriverIds.ts` | `web/src/utils/ergastDriverIds.ts` (copied Day 32 Checkpoint 3 — was undeployed until `useDriverSeasonStats` was ported) |
| `stores/sessionStore.ts` | `web/src/stores/sessionStore.ts` |
| `stores/alertStore.ts` | `web/src/stores/alertStore.ts` |
| `hooks/useConstructorStandings.ts` | `web/src/hooks/useConstructorStandings.ts` (2026-10-07, for the roster order) |
| `hooks/useDemoReplay.ts` | `web/src/hooks/useDemoReplay.ts` (curated races, replay status, start, stop; Day 6b-mobile CP2, 2026-10-09) |

## Copied and adapted — re-diff on change, don't blind-overwrite

| mobile/src path | web/ source | What's different |
|---|---|---|
| `api/client.ts` | `web/src/api/client.ts` | Same axios instance, same request/response interceptor shape (attach bearer token, single-flight 401 refresh). Reads `accessToken` synchronously from `useAuthStore.getState()`, same as web — the store itself is now SecureStore-backed (see below), not the interceptor. |
| `stores/authStore.ts` | `web/src/stores/authStore.ts` | `persist`'s storage swapped from the default (localStorage) to a `StateStorage` adapter wrapping `expo-secure-store`'s async `getItemAsync`/`setItemAsync`/`deleteItemAsync`. `partialize` persists only `accessToken`/`refreshToken`/`expiresAt` — `user` is intentionally never persisted (SecureStore caps each item at ~2048 bytes on iOS; `user` has no fixed size bound and is cheap to refetch via `GET /auth/me` on launch instead). Adds a `hasHydrated` flag (`onRehydrateStorage`) — the root layout (`app/_layout.tsx`, Checkpoint 3) must gate rendering on this before reading `accessToken`, since SecureStore's read is async unlike localStorage's synchronous one. |
| `api/ergast.ts` | `web/src/api/ergast.ts` | Web's file plus one mobile-only function, `getSeasonDriverStandings(season)`: the whole drivers' championship in one request, for the Drivers tab's championship card (2026-10-07). Re-copy web's functions on change and keep this one. |
| `utils/constants.ts` | `web/src/utils/constants.ts` | Drops `CHART_TOOLTIP_STYLE` (styled Recharts' web-only `<Tooltip>` — no chart library wired up yet, victory-native is deferred to Day 32). `API_URL`/`WS_URL` read from `process.env.EXPO_PUBLIC_*` (Expo's built-in env inlining) instead of Vite's `import.meta.env`. `ROUTES` redefined entirely as Expo Router file-based paths (`/(tabs)/live`, `/(auth)/login`, etc.) instead of web's react-router path strings — same key names, different values/shape. `FALLBACK_TEAM_COLOR`, `COMPOUND_COLORS` and `SCENARIO_SERIES_COLORS` (added 2026-10-07 with its comment, for the Simulator) are unchanged. |

## New files — not copies, but mirror web hook logic

Hooks aren't copied (see below) but these were hand-written to replicate the
same react-query/zustand logic as their web equivalents — no browser APIs
were involved in either, so the two implementations are close to identical.
Check these too if the web hook's logic changes (cache keys, mutation
shapes, etc).

| mobile/src path | web/ logic mirrored |
|---|---|
| `hooks/useAuth.ts` | `web/src/hooks/useAuth.ts` |
| `hooks/useDrivers.ts` | `web/src/hooks/useDrivers.ts` |
| `hooks/useCurrentRace.ts` | `web/src/hooks/useCurrentRace.ts` |
| `hooks/useResolvedSession.ts` | `web/src/hooks/useResolvedSession.ts` |
| `hooks/useRaceSession.ts` | `desktop/src/hooks/useRaceSession.ts`, without the Dashboard override. The session Live, Strategy and Driver Detail show: a running Demo Replay's, else `useResolvedSession` (live, else most recent completed race). Web navigates its race page to the replay's session instead. Driver Detail hides its "last completed race" banner during a replay. Day 6b-mobile CP2, 2026-10-09. |
| `hooks/useLastIngestedSession.ts` | `web/src/hooks/useLastIngestedSession.ts`, without web's `meta.silentOn404` (mobile has no global error toast to silence). Added 2026-10-07 for the Simulator's session. |
| `hooks/useSessionGaps.ts` | `web/src/hooks/useSessionGaps.ts` |
| `hooks/useStrategy.ts` | `web/src/hooks/useStrategy.ts` (`usePitWindow`/`useStrategyOverview` ported first for the Strategy tab; `useSimulateStrategy`/`useSimulationResult` added Day 32 Checkpoint 4 for the Simulator screen. `useCurrentLapHistoryEntry`, `PitRecommendationView` and `usePitRecommendation` added Day 6b-mobile CP4 (2026-10-09), same logic as web's but reading lap events from `useSharedLiveTelemetry` instead of opening a WebSocket per card; `usePitWindow` gained web's `enabled` flag) |
| `hooks/useDriverSeasonStats.ts` | `web/src/hooks/useDriverSeasonStats.ts` (ported Day 32 Checkpoint 3, verbatim logic — pure react-query + fetch, no browser API to adapt). Since 2026-10-07 points come from the championship standing, which includes sprints, in all three clients. |
| `hooks/useStrategy.ts` `useUndercut` | web's `useUndercut`, same key and `enabled` flag (2026-10-07, for the Strategy tab's driver sheet). |
| `hooks/useRaceBySession.ts` | `web/src/hooks/useRaceBySession.ts`, plus an exported `raceBySessionQueryOptions` (mobile only) so the Alerts tab can look up every alert's race with `useQueries` on the same cache (2026-10-07). |
| `hooks/useDriverAnalysis.ts` | inline `useQuery` in `web/src/components/driver/StyleRadar.tsx` | web defines this query inline since `StyleRadar` is its only consumer; mobile's Driver Detail header also needs `archetype`, so it's a shared hook here — same queryKey, so react-query dedupes the request between the header and `StyleRadar` instead of firing it twice. |
| `hooks/useUpcomingRace.ts` | `web/src/hooks/useUpcomingRace.ts` |
| `hooks/useCircuitOutline.ts` | `web/src/hooks/useCircuitOutline.ts` |
| `hooks/useDriverLaps.ts` | `web/src/hooks/useDriverLaps.ts` |
| `hooks/useLiveTelemetry.ts` | `web/src/hooks/useLiveTelemetry.ts` — sits on top of the RN-native `useWebSocket.ts` below instead of `reconnecting-websocket`. Since Day 6b-mobile CP4 only `LiveTelemetryBridge` calls it: every call opens its own WebSocket. |
| `hooks/useSharedLiveTelemetry.ts`, `stores/liveTelemetryStore.ts`, `components/shared/LiveTelemetryBridge.tsx` | none (mobile only, Day 6b-mobile CP4, 2026-10-09, owner's option A) | The app's one live telemetry WebSocket. The bridge, mounted once in `app/_layout.tsx` beside the navigator (not around it, so a session change never remounts the screens), connects for `useRaceSession`'s session while signed in and publishes lap events to the zustand store; screens and components read them with `useSharedLiveTelemetry(sessionId)`, empty for any other session. The connection is rebuilt, with no events, whenever the session or the replay state changes, so a stopped replay doesn't leave its last laps behind. Web opens one WebSocket per component (its strategy wall about 20). |
| `hooks/useDriverPositions.ts` | `web/src/hooks/useDriverPositions.ts` |
| `hooks/useLiveDriverTelemetry.ts` | `web/src/hooks/useLiveDriverTelemetry.ts` (drops `meta: { silentOn503: true }` for the same reason noted below) |
| `hooks/useCountdown.ts` | inline `useCountdown` in `web/src/components/dashboard/UpcomingRaceCard.tsx` | web deliberately keeps two separate copies (see its own comment on why); mobile has a second consumer too (`CircuitMapPanel`, Checkpoint 6) so this one was extracted into a shared file instead of copied a third time — `UpcomingRaceCard.tsx` was refactored to import it. |

## Mobile-only files (2026-10-07) — no web counterpart

| mobile/src path | What it is |
|---|---|
| `hooks/useRosterDrivers.ts` | Active drivers in web's roster order (constructor standings, Racing Bulls aliased to Ergast's `rb`, alphabetical-by-team fallback). Used by Home's roster and the Drivers tab. |
| `hooks/useDriverStandings.ts` | The season's drivers' championship (`getSeasonDriverStandings`), cached an hour. |
| `components/dashboard/DriverRosterGrid.tsx` | Home's roster, under the quick-access cards, as on web and desktop. Two-column rows in a plain `View` (Home is a `ScrollView`). |
| `components/driver/DriverStandingsCard.tsx` | Drivers tab: a fixed-height card (about 7 rows) whose rows scroll inside it: position, team logo, name, points. Matched to our roster by driver code; a row opens the driver page. |
| `utils/driverNames.ts` | `displayDriverName`: "Max VERSTAPPEN" (given name, surname in capitals; "Kimi" for ANT, the owner's choice). Live tower and standings card. |
| `utils/rowLogoSizes.ts` | Team logo sizes for list rows (Red Bull, Haas, Alpine 36 px, Ferrari 30, others 24), so rows stay one height. Live tower and standings card. |
| `utils/haptics.ts` | The app's haptics (`expo-haptics`): `selectionTick`, `confirm`, `threshold`, `success`, `warning`, `error`. Screens never import `expo-haptics` directly, so a Settings off switch can be added here. |

**NativeWind gotcha (found 2026-10-07):** a class that no other file uses may
not be applied on the device at all. `ml-4`/`ml-6` between the tower's GAP and
TYRE columns did nothing, and the columns touched, even though `tsc` and the
export were clean. Before using a class in new code, check another file already
uses it (`grep -rlF -- "<class>" app src`); otherwise set the value as a plain
`style`. Reanimated's `Animated.View` takes plain styles only.

**`useCurrentRace`/`useUpcomingRace`/`useCircuitOutline` drop web's `meta: { silentOn404: true }`** — that flag suppresses a global react-query error toast that web's `QueryClientProvider` wires up; mobile's `app/_layout.tsx` uses a bare `new QueryClient()` with no such global handler yet, so the flag would be inert. Restore it on these three if a future checkpoint adds one (e.g. a toast-on-error convention), otherwise 404s could start surfacing as an unwanted global toast.

**`hooks/useWebSocket.ts`** is not a mirror of `web/src/hooks/useWebSocket.ts` — it's a from-scratch RN implementation. Web wraps the `reconnecting-websocket` package (browser-only, explicitly excluded per CLAUDE.md's Day 31 notes); mobile wraps React Native's built-in global `WebSocket` class with a hand-rolled fixed-delay (3s) reconnect instead of that library's backoff/jitter. Same public shape (`readyState`/`send`), same consumer contract, different implementation underneath.

## Not copied — intentionally out of scope for this layer

`hooks/`, `components/`, `pages/` are **not** copied (same convention as
desktop) — hooks are hand-written per-platform re-implementations (started
in Checkpoint 3, continuing in Checkpoint 4), components are hand-built or
ported (Checkpoints 3/4/6), and `pages/` has no mobile equivalent (Expo
Router's file-based `app/` routes replace it entirely).

`components/settings/{ProfileSection,PasswordSection,AlertSubscriptionsSection}.tsx`
mirror web's `components/settings/*.tsx` files but use plain `useState`
forms instead of `react-hook-form` (not installed on mobile — these forms
are small enough a resolver library isn't worth adding). Validation rules
(required fields, email pattern, 8-char minimum, password-match) are
replicated by hand, so re-check them against web's `rules={{...}}` blocks if
those change. `AlertSubscriptionsSection` is also a simplified flat
alphabetical driver list rather than web's team-grouped chips with
per-team select-all — same `GET`/`PUT /alerts/subscriptions` contract,
grouping polish deferred.

## Ported components (Checkpoint 4)

Hand-ported to React Native primitives, same logic/geometry as their web
source. Re-diff, don't blind-overwrite, if the web source changes.

| mobile/src path | web/ source | Notes |
|---|---|---|
| `components/shared/DriverChip.tsx` | `web/src/components/shared/DriverChip.tsx` | Same driverId -> code/team-color resolution. |
| `components/shared/TeamLogo.tsx` | `web/src/components/shared/TeamLogo.tsx` | Ported Day 32 (Checkpoint 2) — was a swatch-only stub through Day 31. Same slug map, same large-logo/Cadillac-backdrop special cases, same swatch fallback for unknown teams. Web's dynamic `/teams/${slug}.png` URL becomes a static `Record<string, ImageSourcePropType>` of `require()` calls (Metro requires static require paths); PNGs copied verbatim from `web/public/teams/*.png` into `mobile/assets/teams/`. |
| `components/circuit/CircuitOutlineSvg.tsx` | `web/src/components/circuit/CircuitOutlineSvg.tsx` | `svg`/`path`/`circle`/`text` -> react-native-svg's `Svg`/`Path`/`Circle`/`Text`. Web's actual markup uses `<path>` (built from `points`), not `<polyline>` — ported as literally written, not per the primitive-mapping note's general guidance. `dominantBaseline="central"` has no react-native-svg equivalent — approximated with a `dy` nudge. |
| `components/telemetry/TyreIcon.tsx` | inline `TyreIcon` function in `web/src/components/telemetry/LiveTimingTower.tsx` | Extracted into its own file since mobile reuses it on both the Live tab and Driver Detail; web only uses it in one place. Same two-arc geometry. |
| `components/strategy/PitWindowCard.tsx` | `web/src/components/strategy/PitWindowCard.tsx` | Same compact/full modes and one render path from `usePitRecommendation`, as web, since Day 6b-mobile CP4 (2026-10-09): the stored prediction for the current lap while a live race or Demo Replay progresses (no `/pit-window` ML call), else the on-demand recompute; web's "~" estimate prefix, "as of lap" caption and fallback-estimate note. Mobile differences: the compact card with no prediction keeps its shape (driver chip, "—", "No pit window predicted"; owner's request 2026-10-07), and the full card's pit-predictor line drops web's "lagging indicator" caption, which is out of date and being reworded in Day 6c. |
| `components/demo/ReplaySelectorPanel.tsx` | `web/src/components/demo/ReplaySelectorPanel.tsx` | Ported Day 6b-mobile CP3 (2026-10-09), above the circuit map on the Live tab as on web's race page; same data, same hiding (a real live race, or the backend reports replays unavailable). Phone differences: collapsed to one "Watch a Replay" row until tapped, so the three race cards don't push the map down; one row with the lap and Stop while a replay runs; web's toasts become a one-line notice plus haptics; no navigation on start, since every race screen follows the running replay (`useRaceSession`). It always renders as a `ScrollView` child, even when hidden, so `live.tsx`'s sticky-switch index counts it. |
| `components/strategy/PositionDistributionChart.tsx` | `web/src/components/strategy/PositionDistributionChart.tsx` | Ported 2026-10-07. Same data (union of positions, percentages to one decimal, gain/hold/lose against `starting_position`, mean position to two decimals) and the same scenario colours. Grouped bars with victory-native's `BarGroup` (as `driver/SectorComparison.tsx`), one fixed y key per strategy (`s0`-`s3`, the backend's 4-scenario cap). No tooltip. The edge padding is sized from the measured width so the first and last bar groups fit (a fixed 16 px cut them off on the iPhone). The table drops web's finish-time range column, which the Simulator's result cards already show. Also exports `strategyLabel`, the Simulator's name for a strategy. |
| `components/strategy/PlanExplanationCard.tsx` | inline `PlanExplanationCard` in `web/src/pages/SimulatorPage.tsx` (desktop's copy-and-adapted version is identical here, minus CSV export) | Ported Day 32 (Checkpoint 4). Same gain/loss heading logic, same pit-cost/recoverable-seconds text. `drivers_overtaken` renders as a `FlatList` (`scrollEnabled={false}`, nested inside the Simulator screen's outer `ScrollView` — lists here are short enough that the nested-list perf warning doesn't matter in practice) using `LiveTimingTower`'s team-color-bar + code row convention (`app/(tabs)/live.tsx`), not `DriverChip`'s pill style — same choice web/desktop made for the same reason. **2026-09-06:** ported web/desktop's wording fix — no longer asserts a "sufficient"/"not enough to recover" verdict from the hardcoded `fresh_tyre_gain_per_lap` constant, which could contradict the real Monte Carlo `position_gain_loss` above it (see `docs/internal/core-feature-rebuild-whatif-simulator.md`'s deferred-item writeup). |
| `components/dashboard/UpcomingRaceCard.tsx` | `web/src/components/dashboard/UpcomingRaceCard.tsx` | Same countdown logic. |
| `components/dashboard/QuickAccessCards.tsx` | `web/src/components/dashboard/QuickAccessCards.tsx` | Two cards, not three — web's third card scroll-anchors to an in-page `#driver-roster` section that doesn't exist on mobile's Home; navigates to the Drivers tab instead. |
| `components/dashboard/RecentAlertsFeed.tsx` | `web/src/components/dashboard/RecentAlertsFeed.tsx` | Shows last 3, not 5 (Day 31 spec explicitly calls for 3 on mobile). |
| `components/driver/StyleRadar.tsx` | `web/src/components/driver/StyleRadar.tsx` | Ported Day 32 (Checkpoint 3). Same 4 axes/metrics/normalization/archetype-description logic, copied verbatim where it's pure data transformation. The chart itself is **not** a victory-native chart — victory-native 41.x (confirmed against its installed source) has no radar/spider chart; its `PolarChart` only supports a `Pie.Chart` child. Hand-rolled instead with `react-native-svg` (`Polygon`/`Line`/`Text`), same manual polar-trig convention as `TelemetryGauge.tsx`/`CircuitOutlineSvg.tsx`. Web's "About this chart" `Dialog` modal becomes a `Pressable`-toggled inline expand section (no modal-in-a-Card pattern established on mobile). |
| `components/driver/SectorComparison.tsx` | `web/src/components/driver/SectorComparison.tsx` | Ported Day 32 (Checkpoint 3). Same per-driver-mean-then-averaged team calculation, copied verbatim. Grouped bars use victory-native's real `CartesianChart` + `BarGroup` API (confirmed against the installed 41.26.0 source) — the classic web `victory` package's `VictoryBar`/`VictoryChart` naming this project's own CLAUDE.md/spec text referenced doesn't apply to this Skia rewrite. Axis tick labels need a real Skia `Font` object (`useFont`) — reuses the same bundled Titillium Web `.ttf` already loaded for RN `Text` via `expo-font`, as a second independent load into Skia's own font subsystem (Skia's Canvas doesn't share React Native's font registration). No `Legend` component exists in victory-native's exports — hand-rolled a small swatch row below the chart instead, same as web's `<Legend/>` visually. |
| `components/circuit/CircuitMapPanel.tsx` | `web/src/components/circuit/CircuitMapPanel.tsx` | Same modes as web (live/historical/non-race/finished/unknown) since Day 6b-mobile CP2 (2026-10-09): the track and race name come from the session's own race (`useRaceBySession`) when dots are on the map or the session is a running replay's (`isExplicitSession`, from `useRaceSession`'s `isReplay`), else the upcoming race. Before, mobile always used the upcoming race, so a replay drew the next race's track. Same same `applyTransform` geometry, same turn markers/countdown/telemetry gauge. Placed at the top of the **Live tab**, not Home — web's Home-equivalent (`DashboardPage`) only ever got the static `UpcomingRaceCard`; the full live panel lives on web's `RacePage` instead, which this mirrors by putting it above `live.tsx`'s driver `FlatList` (as a `ListHeaderComponent`, always rendered regardless of the gaps list's own loading/empty state, same as web mounting both `CircuitMapPanel` and `LiveTimingTower` independently). Live dot movement uses a new `AnimatedDriverDot.tsx` (Reanimated `useAnimatedProps` on an `Animated.createAnimatedComponent(Circle)`) instead of web's CSS `transform` transition — react-native-svg has nothing CSS transitions can hook into. It ports web's `AnimatedDriverDots.tsx` render-behind interpolation buffer: per-instance `useSharedValue<PositionSample[]>` fed one raw `(x, y, Date.now())` sample per poll, a `useFrameCallback` worklet drawing the dot at `Date.now() - renderDelayMs` between the two straddling samples (`renderDelayMs` derives from `useDriverPositions.ts`'s `POSITIONS_POLL_INTERVAL_MS`, 2s on mobile). `applyTransform` moved into `AnimatedDriverDot.tsx` as a `"worklet"` and takes raw `x/y` + `transform` props now, not pre-computed `cx/cy`. |
| `components/circuit/SelectedDriverLabel.tsx` | none (web and desktop draw the label inside the SVG, in `AnimatedDriverDots.tsx`) | The selected driver's code in a dark pill with a team-colour border, above the enlarged dot, following it (2026-10-09, owner's option A, all three clients). Mobile difference: a React Native `Animated.View` over the map, moved by `useAnimatedStyle`, instead of SVG text, because react-native-svg's `<Text>` rebuilds its x/y on render and Reanimated's animated props skip that. The selected `AnimatedDriverDot` copies its animated centre into two shared values (`useAnimatedReaction`, UI thread); the label turns viewBox units into pixels from the map's measured size, as `preserveAspectRatio="xMidYMid meet"` does, and flips below the dot near the top edge. `CircuitMapPanel` draws the selected dot last and shows the label only when that driver is on the map. |
| `components/driver/LapTimesChart.tsx` | `web/src/components/driver/LapTimesChart.tsx` | Ported 2026-10-07 into Driver Detail's Sector Times tab, below the teammate comparison. Lap time by tyre age, one line per compound. victory-native `CartesianChart` takes one data array with fixed y keys, so each compound is a column and each lap fills only its own; `connectMissingData` joins a compound's laps across the others' rows. No tooltip; swatch legend. |
| `components/telemetry/LapTimeChart.tsx` | `web/src/components/telemetry/LapTimeChart.tsx` | Ported 2026-10-07 for the Live tab's Lap Times view. Lap time by lap, the line coloured by compound: the lap before a tyre change is written into both compounds' columns (web's bridge point), and `connectMissingData` is off so two stints on one compound aren't joined. Dashed "Pit" markers are Skia lines drawn with the chart's `xScale` (victory-native has no reference line). Same live/replay lap limit as web. Takes `lapsByDriver` as a prop instead of calling `useLiveTelemetry`, because on mobile every call opens its own WebSocket. Laps with no time are left out (web draws through them). |
| `components/telemetry/SectorHeatmap.tsx` | `web/src/components/telemetry/SectorHeatmap.tsx` | Ported 2026-10-07 for the Live tab's Sectors view. `classifySector`/`minOf`/`formatTimeValue` copied unchanged; times are coloured text in a grey pill (`bg-pill`, web's `bg-pill-surface`), zebra rows as web, and the selected row is outlined (web's ring). Tapping a row selects the driver. `lapsByDriver` is a prop, as above. |
| `components/circuit/TelemetryGauge.tsx` | `web/src/components/circuit/TelemetryGauge.tsx` | Same arc-geometry math (`polarToCartesian`/`describeArc`), same 5 readouts. Two disclosed drops: (1) no arc-sweep animation on data updates — web transitions the `d` attribute via CSS, which browsers can interpolate directly; react-native-svg can't animate `Path`'s `d` as a single tweenable value without a path-morphing library (not installed), so arcs snap to their new value each 8s poll instead of sweeping. (2) accessibility: dropped `role="img"`/`aria-label` (`describeGauge`) and `useId()`-based unique SVG path ids (fixed string ids used instead) — the fixed ids are safe since only one `TelemetryGauge` instance mounts at a time on mobile (unlike web, where nothing prevents two instances existing at once). |

**Simplified vs. web — disclosed, not full parity:**

- `app/(tabs)/drivers.tsx` (since 2026-10-07): the Drivers' Championship card
  (`DriverStandingsCard`), then the roster sorted by constructor standings
  (`useRosterDrivers`), as web.
- `app/(tabs)/live.tsx` (rebuilt 2026-10-07, the owner's phone layout): the
  circuit map, then a `Timing | Lap Times | Sectors` switch that sticks to the
  top of the `ScrollView` once the map scrolls away
  (`stickyHeaderIndices`), then one view at a time. Web shows the tower,
  `LapTimeChart` and `SectorHeatmap` side by side.
  - **Timing** has a header row (POS, DRIVER, GAP, TYRE) and rows showing
    position, team logo, driver name, gap and tyre. There is no last lap time;
    Sectors has it, coloured.
  - **Tapping a row** selects the driver (`sessionStore`) for the map, Lap
    Times and Sectors; its `›` opens Driver Detail.
  - **Spacing:** the gap-to-tyre space is a plain style (see the NativeWind
    gotcha above).
  - **No row animation:** web's FLIP row-reorder animation is
    DOM-measurement-specific, so rows re-render in their new order.
  - **Tyre and gaps as web (Day 6b-mobile CP1, 2026-10-09):** the tyre comes
    from `gap.compound` first, so a new tyre shows at the pit stop, then the
    last lap's. A lapped car shows "+1 LAP" (and every car behind it counts
    laps), as web's `computeGapLabels`; before, those rows showed "—".
- `app/driver/[id].tsx`: a team-colour header and two tabs (since
  2026-10-07, the owner's choice; it was three).
  - **Overview** shows season stats, then the driving-style radar.
  - **Sector Times** shows the teammate comparison, then the ported
    `LapTimesChart`.
  - **Banner:** the historical-data banner doesn't persist its dismissal like
    web's `localStorage` version; it resets each time the screen mounts.
- `app/(tabs)/strategy.tsx` (2026-10-07, mobile only):
  - **Header:** the race and whether it is live.
  - **Cards:** tapping a card selects the driver (`sessionStore`, shared with
    the Live tab; the selected card is outlined) and opens the
    `app/strategy-driver.tsx` route: a native iOS form sheet sized to its
    content (`presentation: "formSheet"`, `sheetAllowedDetents:
    "fitToContents"` in `app/_layout.tsx`; React Native's `Modal` only opens
    full height). It renders `components/strategy/DriverStrategySheet.tsx`,
    a plain `View`: a ScrollView inside a fit-to-contents sheet doesn't size
    it.
  - **The sheet** shows the full `PitWindowCard`, `UndercutThreatPanel` (a
    port of web's, with its replay rows since Day 6b-mobile CP4) and
    "Simulate this driver", which opens `app/simulator.tsx` with `sessionId`
    and `driverId` route params as the starting values.
- `app/(tabs)/alerts.tsx` (rebuilt 2026-10-07, mobile only; web and desktop
  unchanged):
  - **Cards:** each alert has a type badge and title, a relative time and an
    unread edge, and alerts are grouped by race under sticky headers
    (`SectionList`).
  - **Filters and refresh:** filter chips (All, Unread, each type present)
    and pull to refresh.
  - **Tap** marks the alert read and opens the Live tab on its driver.
  - **Swipe left** marks one read (`ReanimatedSwipeable`; the deprecated
    `Swipeable` is gone).
  - **Long press** starts multi-select: "Select all unread" (within the
    filter), and Mark as read sends one `PUT /alerts/{id}/read` per alert
    (there is no bulk endpoint) and counts failures.
  - **Motion:** Reanimated animations and `utils/haptics.ts`.
  - **Empty states:** the no-alerts state links to Settings; the no-match
    state links back to All.
  - **Keep `onLongPress` always set on the row's `Pressable`.** React Native
    only skips the tap on release if it is still set; removing it once
    selection mode started made the release untick the alert and end
    selection mode.
- `app/simulator.tsx` is new Day 32 (Checkpoint 4) — a port of
  `web/src/pages/SimulatorPage.tsx`'s 4-step flow, reached from the Strategy
  tab's "Run Simulator" button and a driver sheet's "Simulate this driver"
  (not a 6th tab). **Brought level with web on 2026-10-07** (owner: the
  results must match web):
  - **Steps:** Setup / Strategy / Simulate / Results, one word each so they
    fit under their circles on a phone.
  - **Session, read-only:** a driver sheet's session first, else the live
    race (a `/races/current` Race session with timing data, as web), else the
    last ingested race (`useLastIngestedSession`). The typed Session ID field
    is gone.
  - **Single Plan / Compare Scenarios,** as web: up to 4 scenarios (pit lap,
    compound, optional label, defaulting to "Pit lap N"), sent as
    `scenarios`. A scenario's lap and label share a line and its compound
    wheel sits below, since web's one-line row is too wide for a phone. The
    quota line says what a comparison costs ("This run needs N").
  - **Results:** the position-change bar chart, named by each strategy's
    label; one card per strategy with the change, `formatRaceTime` finish time
    and its range (web's table, four columns, didn't fit); then
    `PositionDistributionChart` and a labelled `PlanExplanationCard` per
    strategy.
  - **Not on mobile:** CSV export (desktop only) and drag-drop.
  - **NativeWind:** the disabled buttons dim through a plain `opacity` style;
    the `disabled:` variant isn't used elsewhere in mobile, so it may not
    apply on the device.
  Uses `@react-native-picker/picker` (added Day 32 Checkpoint 4) for the
  driver/compound selects, with `itemStyle` so the iOS wheel's labels aren't
  black. The position-change chart is a horizontal bar (victory-native's
  `CartesianChart` + `HorizontalBar`, `orientation="horizontal"`): per-bar
  gain/loss colouring isn't a built-in prop, so the data carries two
  synthetic y-series (`gain`/`loss`, only one nonzero per row) drawn as two
  differently coloured `HorizontalBar` layers. That data is passed to
  `CartesianChart` inline in JSX: routing it through a named interface first
  breaks TypeScript's overload resolution for its generic `RawData`.

## Offline support (Day 32 Checkpoint 5)

`app/_layout.tsx`'s `QueryClientProvider` was replaced with
`PersistQueryClientProvider` (`@tanstack/react-query-persist-client`), backed
by `createAsyncStoragePersister` (`@tanstack/query-async-storage-persister`)
writing to `@react-native-async-storage/async-storage`. No web equivalent —
this is mobile-only, web has no offline story. Persistence is scoped to
exactly 3 query-key-prefix families via a `shouldDehydrateQuery` filter in
`app/_layout.tsx` (`PERSISTED_QUERY_KEY_PREFIXES`): `["race","upcoming"]`
(`useUpcomingRace`), `["drivers"]` (`useDrivers`), and `["strategy"]` (every
`useStrategy.ts` query — `usePitWindow`/`useStrategyOverview`/
`useSimulationResult`). Everything else (live telemetry, alerts, session
gaps, circuit outlines) stays in-memory-only — react-query's own cache still
serves each query's last-successful value while offline, it just isn't
written to disk across app restarts, which is the same "last known" effect
for as long as the app process stays alive.

`mobile/src/components/shared/OfflineBanner.tsx` is new — same informational
blue-tone styling family as `HistoricalDataBanner`'s RN port, different
trigger (`@react-native-community/netinfo`'s `useNetInfo().isConnected`, not
"no live session"). Added to all 5 tab screens plus Driver Detail. Each
screen passes its own most-relevant query's `dataUpdatedAt` for the banner's
stale-timestamp text — including screens whose query isn't one of the 3
persisted families above (e.g. Live's `useSessionGaps`, Alerts' own query) —
the timestamp is about "when did we last successfully fetch this", which
react-query tracks for every query regardless of whether it's written to
AsyncStorage.

`hooks/useWebSocket.ts` now calls `useNetInfo()` internally and folds
`isConnected === false` into its connect-effect's early-return condition
(alongside the existing `!url`/`!enabled` checks), with `isConnected` added
to the effect's dependency array so connectivity returning triggers a normal
reconnect. `hooks/useLiveTelemetry.ts` needed no separate change — it sits on
top of `useWebSocket`, so it inherits the gate automatically; `live.tsx`'s
existing REST-fallback-per-driver logic (`liveLap?.compound ??
latestRestLap?.compound`) already covers "show last cached data" once the WS
stream itself is gated off.

## Testing Options

**Since 2026-10-06 the app runs in Expo Go on the owner's iPhone.** Expo Go
now ships SDK 57, the project's SDK, and has every native module the app uses,
so no development build or Apple Developer account is needed. Setup and steps
are in `mobile/README.md`, section 1. The first device run found a crash that
`tsc` and the export never could: `usePushNotifications` ran above the query
provider (fixed in `app/_layout.tsx`). Every change is still checked with
`npx tsc --noEmit` and `npx expo export --platform ios`, then on the phone.

The options below were written when no device was available (Days 31-32).
They are still the routes for Android and for a development build:

1. **Android Studio emulator (AVD)** — free, no developer account of any
   kind needed. The most complete free option: runs `expo start` +
   Expo Go (or a dev client) exactly like a real Android phone, full access
   to SecureStore/gesture handling/everything built so far **except push
   notifications** (see the Push Notifications note below — that specific
   feature needs a real device regardless of AVD vs physical). Windows-native,
   no Mac needed. Recommended first step once ready to resume device
   testing — full procedure below.
2. **iOS Simulator** — needs a Mac with Xcode installed, but **no paid
   Apple Developer account** — Xcode itself is free, and the Simulator runs
   unsigned builds. Not available on this Windows machine directly, but
   worth knowing the paid account is only a blocker for real iOS *hardware*,
   not the simulator.
3. **EAS Build, `simulator` profile (iOS) / `preview` profile (Android)** —
   `eas.json`'s `development` profile already sets `ios.simulator: false`
   (queued for a future real-device dev client); flipping that to `true` for
   an iOS Simulator build needs a Mac to run the `.app` output but still no
   Apple Developer account, since simulator builds are unsigned. Android's
   `preview`/`development` profiles (`distribution: "internal"`) produce a
   installable `.apk` with **no account needed at all** — can be side-loaded
   onto the Android Studio emulator or any Android device directly.
4. **Cloud device farms** (BrowserStack App Live, Sauce Labs, AWS Device
   Farm, Firebase Test Lab) — real physical/virtual devices accessed through
   a browser, no local hardware required. Straightforward for Android (just
   upload the EAS-built `.apk`, no account needed on Apple's side). iOS
   real-device testing on these services still needs a properly *signed*
   IPA, which circles back to needing an Apple Developer Program membership
   eventually — these services don't remove that requirement, they just
   remove the need to personally own the hardware.
5. **Borrowed device** — the zero-setup option: `expo start` + Expo Go on
   any spare iPhone/Android phone on the same WiFi (a friend's/colleague's),
   no build or account needed at all, same as the original Day 31 plan's
   intended workflow.
6. **Windows Subsystem for Android (WSA)** — a lighter-weight alternative
   to a full Android Studio AVD if disk/resource usage is a concern, though
   less actively maintained than AVD for Expo's use case; AVD remains the
   more standard path.

**Push notifications**: requires physical device with development build.
iOS needs Apple Developer account. Android is free via EAS. Cannot be
tested in Expo Go. All push-notification code (Checkpoint 5 —
`src/notifications/notificationHandler.ts`,
`src/hooks/{usePushNotifications,useNotificationResponseListener}.ts`) was
written and verified via `tsc`/Metro export only, per this constraint —
see the `NOTE:` comment at the top of each of those files. In Expo Go on the
iPhone (2026-10-06) token registration runs at sign-in without breaking
anything (it catches its own failure); whether a push is actually delivered
there has not been tested.

Real Apple Developer Program enrollment ($99/year) only becomes
unavoidable once TestFlight distribution or an App Store submission is the
actual goal — everything above (including iOS Simulator testing) works
without it.

### Android Emulator Testing (procedure)

Verified against Expo's current official docs
(docs.expo.dev/workflow/android-studio-emulator) — Windows-specific, no
Apple/EAS account of any kind needed.

**1. Install prerequisites**

```sh
choco install -y microsoft-openjdk17
```

Download and run the Android Studio installer from
[developer.android.com/studio](https://developer.android.com/studio).
During setup, select the "Android Virtual Device" component and the
"Standard" install type, and accept the license agreements.

**2. Configure the SDK**

In Android Studio: **Settings → Languages & Frameworks → Android SDK**.
- **SDK Platforms** tab: install the current Android Platform + Sources
  (whatever the latest stable API level is — this drifts release to
  release, so use whatever Android Studio's SDK Manager currently lists as
  current rather than pinning a specific number here).
- **SDK Tools** tab: confirm **Android SDK Build-Tools** and **Android
  Emulator** are both installed.

**3. Set environment variables**

Windows Control Panel → User Accounts → User Accounts → **Change my
environment variables**:
- New user variable `ANDROID_HOME` → `%LOCALAPPDATA%\Android\Sdk`
- Append `%LOCALAPPDATA%\Android\Sdk\platform-tools` to `Path`

Verify in PowerShell: `adb --version` should print a version, not
"command not found".

**4. Create a virtual device (AVD)**

Android Studio's main screen → **More Actions → Virtual Device Manager →
Create virtual device**. Pick a Pixel profile, pick a system image (a
**Play Store**-enabled image is worth choosing specifically — it lets Expo
Go install itself onto the AVD automatically in step 6, versus a bare
image where it may need a manual `adb install`), **Finish**. Launch it
once from the Virtual Device Manager (green ▶) and let it fully boot
before the next step — starting Metro against a not-yet-booted emulator
just times out.

**5. Point the app at the backend — `10.0.2.2`, not `localhost`**

**Confirmed via Expo/Android's own documented behavior**: the Android
emulator runs in its own virtual network namespace, and `10.0.2.2` is a
special alias *the emulator itself* maps back to the host machine's
`localhost` — it is not something to configure, it always resolves that
way inside any AVD. This is a different value than the physical-device
setup already documented above `mobile/.env`'s creation note (a real
iPhone on the same WiFi needs the dev machine's actual LAN IP, since it's
a separate physical device on the network, not a VM aliasing the host).

Set (or temporarily swap) `mobile/.env`:
```
EXPO_PUBLIC_API_URL=http://10.0.2.2:8000
```
A LAN-IP value (`http://192.168.x.x:8000`) also typically works from the
AVD, since Android emulators bridge onto the host's network by default —
but `10.0.2.2` is the documented, guaranteed-reliable path and doesn't
depend on WiFi/firewall state, so prefer it specifically for emulator
testing.

**6. Start the app**

With the AVD running:
```sh
cd mobile
npx expo start --android
```
This is the correct command for Expo Go-based testing (what this project
uses today — no native dev client has been built yet). Expo CLI detects
the running emulator and either auto-installs Expo Go onto it (Play
Store-enabled image) or prompts for a manual `adb install` (bare image),
then loads the bundle. `npx expo run:android` is a **different**,
heavier command — it builds a full native dev client via the Android SDK
and is only needed once this project has a native module Expo Go can't
run (not the case yet).
