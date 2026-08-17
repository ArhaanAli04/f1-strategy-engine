# mobile/src — Web Sync Notes

No monorepo/symlink sharing between `web/` and `mobile/` — same reasoning as
`desktop/src/README.md` (unreliable on Windows, see CLAUDE.md's Day 30 setup
notes). The files below are manual copies (or, where noted, hand-written
re-implementations of the same logic) and must be checked by hand whenever
their `web/` source changes.

## Verbatim copies — re-copy on change

| mobile/src path | web/ source |
|---|---|
| `types/*.ts` (10 files) | `web/src/types/*.ts` |
| `api/alerts.ts`, `auth.ts`, `circuit.ts`, `driver.ts`, `ergast.ts`, `race.ts`, `strategy.ts`, `telemetry.ts` (8 files) | `web/src/api/*.ts` (same filenames) |
| `utils/errors.ts` | `web/src/utils/errors.ts` |
| `utils/formatters.ts` | `web/src/utils/formatters.ts` |
| `utils/drivers.ts` | `web/src/utils/drivers.ts` |
| `utils/ergastDriverIds.ts` | `web/src/utils/ergastDriverIds.ts` (copied Day 32 Checkpoint 3 — was undeployed until `useDriverSeasonStats` was ported) |
| `stores/sessionStore.ts` | `web/src/stores/sessionStore.ts` |
| `stores/alertStore.ts` | `web/src/stores/alertStore.ts` |

## Copied and adapted — re-diff on change, don't blind-overwrite

| mobile/src path | web/ source | What's different |
|---|---|---|
| `api/client.ts` | `web/src/api/client.ts` | Same axios instance, same request/response interceptor shape (attach bearer token, single-flight 401 refresh). Reads `accessToken` synchronously from `useAuthStore.getState()`, same as web — the store itself is now SecureStore-backed (see below), not the interceptor. |
| `stores/authStore.ts` | `web/src/stores/authStore.ts` | `persist`'s storage swapped from the default (localStorage) to a `StateStorage` adapter wrapping `expo-secure-store`'s async `getItemAsync`/`setItemAsync`/`deleteItemAsync`. `partialize` persists only `accessToken`/`refreshToken`/`expiresAt` — `user` is intentionally never persisted (SecureStore caps each item at ~2048 bytes on iOS; `user` has no fixed size bound and is cheap to refetch via `GET /auth/me` on launch instead). Adds a `hasHydrated` flag (`onRehydrateStorage`) — the root layout (`app/_layout.tsx`, Checkpoint 3) must gate rendering on this before reading `accessToken`, since SecureStore's read is async unlike localStorage's synchronous one. |
| `utils/constants.ts` | `web/src/utils/constants.ts` | Drops `CHART_TOOLTIP_STYLE` (styled Recharts' web-only `<Tooltip>` — no chart library wired up yet, victory-native is deferred to Day 32). `API_URL`/`WS_URL` read from `process.env.EXPO_PUBLIC_*` (Expo's built-in env inlining) instead of Vite's `import.meta.env`. `ROUTES` redefined entirely as Expo Router file-based paths (`/(tabs)/live`, `/(auth)/login`, etc.) instead of web's react-router path strings — same key names, different values/shape. `FALLBACK_TEAM_COLOR` and `COMPOUND_COLORS` are unchanged. |

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
| `hooks/useSessionGaps.ts` | `web/src/hooks/useSessionGaps.ts` |
| `hooks/useStrategy.ts` | `web/src/hooks/useStrategy.ts` (`usePitWindow`/`useStrategyOverview` ported first for the Strategy tab; `useSimulateStrategy`/`useSimulationResult` added Day 32 Checkpoint 4 for the Simulator screen. `useUndercut` still isn't ported — no mobile consumer yet) |
| `hooks/useDriverSeasonStats.ts` | `web/src/hooks/useDriverSeasonStats.ts` (ported Day 32 Checkpoint 3, verbatim logic — pure react-query + fetch, no browser API to adapt) |
| `hooks/useDriverAnalysis.ts` | inline `useQuery` in `web/src/components/driver/StyleRadar.tsx` | web defines this query inline since `StyleRadar` is its only consumer; mobile's Driver Detail header also needs `archetype`, so it's a shared hook here — same queryKey, so react-query dedupes the request between the header and `StyleRadar` instead of firing it twice. |
| `hooks/useUpcomingRace.ts` | `web/src/hooks/useUpcomingRace.ts` |
| `hooks/useCircuitOutline.ts` | `web/src/hooks/useCircuitOutline.ts` |
| `hooks/useDriverLaps.ts` | `web/src/hooks/useDriverLaps.ts` |
| `hooks/useLiveTelemetry.ts` | `web/src/hooks/useLiveTelemetry.ts` — sits on top of the RN-native `useWebSocket.ts` below instead of `reconnecting-websocket` |
| `hooks/useDriverPositions.ts` | `web/src/hooks/useDriverPositions.ts` |
| `hooks/useLiveDriverTelemetry.ts` | `web/src/hooks/useLiveDriverTelemetry.ts` (drops `meta: { silentOn503: true }` for the same reason noted below) |
| `hooks/useCountdown.ts` | inline `useCountdown` in `web/src/components/dashboard/UpcomingRaceCard.tsx` | web deliberately keeps two separate copies (see its own comment on why); mobile has a second consumer too (`CircuitMapPanel`, Checkpoint 6) so this one was extracted into a shared file instead of copied a third time — `UpcomingRaceCard.tsx` was refactored to import it. |

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
| `components/strategy/PitWindowCard.tsx` | `web/src/components/strategy/PitWindowCard.tsx` | Same compact/full modes, same SHAP top-contribution formatting. |
| `components/strategy/PlanExplanationCard.tsx` | inline `PlanExplanationCard` in `web/src/pages/SimulatorPage.tsx` (desktop's copy-and-adapted version is identical here, minus CSV export) | Ported Day 32 (Checkpoint 4). Same gain/loss heading logic, same pit-cost/recoverable-seconds text. `drivers_overtaken` renders as a `FlatList` (`scrollEnabled={false}`, nested inside the Simulator screen's outer `ScrollView` — lists here are short enough that the nested-list perf warning doesn't matter in practice) using `LiveTimingTower`'s team-color-bar + code row convention (`app/(tabs)/live.tsx`), not `DriverChip`'s pill style — same choice web/desktop made for the same reason. |
| `components/dashboard/UpcomingRaceCard.tsx` | `web/src/components/dashboard/UpcomingRaceCard.tsx` | Same countdown logic. |
| `components/dashboard/QuickAccessCards.tsx` | `web/src/components/dashboard/QuickAccessCards.tsx` | Two cards, not three — web's third card scroll-anchors to an in-page `#driver-roster` section that doesn't exist on mobile's Home; navigates to the Drivers tab instead. |
| `components/dashboard/RecentAlertsFeed.tsx` | `web/src/components/dashboard/RecentAlertsFeed.tsx` | Shows last 3, not 5 (Day 31 spec explicitly calls for 3 on mobile). |
| `components/driver/StyleRadar.tsx` | `web/src/components/driver/StyleRadar.tsx` | Ported Day 32 (Checkpoint 3). Same 4 axes/metrics/normalization/archetype-description logic, copied verbatim where it's pure data transformation. The chart itself is **not** a victory-native chart — victory-native 41.x (confirmed against its installed source) has no radar/spider chart; its `PolarChart` only supports a `Pie.Chart` child. Hand-rolled instead with `react-native-svg` (`Polygon`/`Line`/`Text`), same manual polar-trig convention as `TelemetryGauge.tsx`/`CircuitOutlineSvg.tsx`. Web's "About this chart" `Dialog` modal becomes a `Pressable`-toggled inline expand section (no modal-in-a-Card pattern established on mobile). |
| `components/driver/SectorComparison.tsx` | `web/src/components/driver/SectorComparison.tsx` | Ported Day 32 (Checkpoint 3). Same per-driver-mean-then-averaged team calculation, copied verbatim. Grouped bars use victory-native's real `CartesianChart` + `BarGroup` API (confirmed against the installed 41.26.0 source) — the classic web `victory` package's `VictoryBar`/`VictoryChart` naming this project's own CLAUDE.md/spec text referenced doesn't apply to this Skia rewrite. Axis tick labels need a real Skia `Font` object (`useFont`) — reuses the same bundled Titillium Web `.ttf` already loaded for RN `Text` via `expo-font`, as a second independent load into Skia's own font subsystem (Skia's Canvas doesn't share React Native's font registration). No `Legend` component exists in victory-native's exports — hand-rolled a small swatch row below the chart instead, same as web's `<Legend/>` visually. |
| `components/circuit/CircuitMapPanel.tsx` | `web/src/components/circuit/CircuitMapPanel.tsx` | Same 3 modes (live/non-race/finished/unknown), same `applyTransform` geometry, same turn markers/countdown/telemetry gauge. Placed at the top of the **Live tab**, not Home — web's Home-equivalent (`DashboardPage`) only ever got the static `UpcomingRaceCard`; the full live panel lives on web's `RacePage` instead, which this mirrors by putting it above `live.tsx`'s driver `FlatList` (as a `ListHeaderComponent`, always rendered regardless of the gaps list's own loading/empty state, same as web mounting both `CircuitMapPanel` and `LiveTimingTower` independently). Live dot movement uses a new `AnimatedDriverDot.tsx` (Reanimated `useAnimatedProps` on an `Animated.createAnimatedComponent(Circle)`) instead of web's CSS `transform` transition — react-native-svg has nothing CSS transitions can hook into. |
| `components/circuit/TelemetryGauge.tsx` | `web/src/components/circuit/TelemetryGauge.tsx` | Same arc-geometry math (`polarToCartesian`/`describeArc`), same 5 readouts. Two disclosed drops: (1) no arc-sweep animation on data updates — web transitions the `d` attribute via CSS, which browsers can interpolate directly; react-native-svg can't animate `Path`'s `d` as a single tweenable value without a path-morphing library (not installed), so arcs snap to their new value each 8s poll instead of sweeping. (2) accessibility: dropped `role="img"`/`aria-label` (`describeGauge`) and `useId()`-based unique SVG path ids (fixed string ids used instead) — the fixed ids are safe since only one `TelemetryGauge` instance mounts at a time on mobile (unlike web, where nothing prevents two instances existing at once). |

**Simplified vs. web — disclosed, not full parity:**

- `app/(tabs)/drivers.tsx` sorts alphabetically by team name rather than by
  real Ergast constructor-standings position
  (`web/src/hooks/useConstructorStandings.ts` was not ported — it's an
  external-API integration not needed anywhere else on mobile yet). This is
  the exact same fallback web itself uses when the standings fetch is
  empty/unavailable, just used unconditionally here instead of only as a
  fallback.
- `app/(tabs)/live.tsx` drops web's FLIP-style row-reorder animation
  (`LiveTimingTower.tsx`'s `useLayoutEffect` + `getBoundingClientRect`) — that
  technique is DOM-measurement-specific with no direct React Native
  equivalent. `FlatList` re-renders rows in their new sorted order on every
  gaps poll with no animated glide between old/new positions.
- `app/driver/[id].tsx` was a minimal stub (identity + current-session
  snapshot only) through Day 31 — full-ported Day 32 (Checkpoint 3) into a
  team-color header + segmented Overview/Driving Style/Sector Times control,
  mirroring `web/src/pages/DriverPage.tsx`. One chart is intentionally not
  ported: web's `LapTimesChart.tsx` (lap time by compound, over a session)
  isn't in the Day 32 spec's 3 sub-views — Overview/Driving Style/Sector
  Times only. Add it as a 4th sub-view if a future day wants full parity.
  The historical-data banner (shown when `!isLive`) doesn't persist
  dismissal to `AsyncStorage` like web's `localStorage`-backed version does
  — plain component state, resets each time the screen mounts.
- `app/simulator.tsx` is new Day 32 (Checkpoint 4) — a port of
  `web/src/pages/SimulatorPage.tsx`/`desktop/src/pages/SimulatorPage.tsx`'s
  4-step flow, reached via a "Run Simulator" button at the top of the
  Strategy tab (`app/(tabs)/strategy.tsx`) rather than a 6th tab bar entry.
  Session ID is always a plain manual text input, matching desktop's
  version — no web-only live-mode auto-detect (`useCurrentRace`/
  `useSessionGaps`-driven read-only field). No CSV export (desktop-only) and
  no drag-drop (add/remove buttons only, same as web). Uses
  `@react-native-picker/picker` (added Day 32 Checkpoint 4) for the
  driver/compound selects — confirmed via Expo's docs as still current and
  Expo-SDK-57-compatible; `@expo/ui/community/picker` (a newer Jetpack-
  Compose/SwiftUI-backed drop-in Expo also documents) was not used, to avoid
  pulling in the larger `@expo/ui` package for a single form control. Step
  4's chart is a horizontal bar (victory-native's `CartesianChart` +
  `HorizontalBar`, `orientation="horizontal"`) — per-bar gain/loss coloring
  isn't a built-in prop (no Recharts-style per-`Cell` coloring), so the
  chart data carries two synthetic y-series (`gain`/`loss`, only one nonzero
  per row) rendered as two independently-colored `HorizontalBar` layers.
  That chart data is passed to `CartesianChart` inline in JSX, not through a
  separately-typed component prop — routing it through a named interface
  first breaks TypeScript's overload resolution for `CartesianChart`'s
  generic `RawData` (confirmed while building this screen; see the inline
  comment at the call site).

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

No physical iOS/Android device or Apple Developer account is available for
the remainder of Days 31-32 — every checkpoint through the build sprint is
verified by `tsc --noEmit` + `npx expo export --platform ios` (full Metro
module-graph resolution) + code review only, not a running app. Real-device
testing was possible on Day 30 for the desktop app but isn't for mobile
right now. Options to actually run and interact with the app, in rough
order of setup cost, once the build sprint itself is done:

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
see the `NOTE:` comment at the top of each of those files.

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
