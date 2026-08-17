# F1 Strategy Engine — Mobile

Expo SDK 57 + Expo Router + NativeWind v4 mobile client for the F1 Strategy
Engine. This is the **setup guide** — how to get the app running, on a
device or emulator, at every stage from local development through App
Store/Play Store release. For the developer-facing web/mobile sync protocol
(which files are ported from `web/`, what's simplified vs. web, etc.), see
[`src/README.md`](./src/README.md) instead — this file is about running the
app, that one is about how it's built.

---

## 1. Development setup (laptop required)

**Prerequisites:**
- Node.js 18+
- An [Expo account](https://expo.dev/signup) (free)
- EAS CLI: `npm install -g eas-cli`, then `eas login`
- **iOS physical device:** requires an active
  [Apple Developer Program](https://developer.apple.com/programs/) membership
  ($99/year) — Apple requires this to install a development build on real
  hardware, even for your own device. There is no free path to a physical
  iOS device.
- **Android physical device:** completely free, no account of any kind
  needed beyond the Expo account above.

**One-time: build a development client.**

A development client is a custom build of the app (this project's native
modules — Skia, Reanimated, react-native-svg, etc. — aren't in the stock
Expo Go app) that you install once and then reload instantly during
day-to-day work, the same way Expo Go normally works.

```sh
cd mobile
eas build --profile development --platform ios      # requires Apple Developer account
eas build --profile development --platform android   # free, no account needed
```

Each command queues a cloud build; EAS emails/prints a link when it's done.
Install the resulting `.ipa`/`.apk` on your device (iOS needs the device
registered in your Apple Developer account first — `eas device:create` walks
through that).

**Daily workflow, once the dev client is installed:**

```sh
# from the repo root — starts Postgres/Redis/backend/worker
make dev

# from mobile/ — starts the Metro bundler
cd mobile
npx expo start
```

Open the installed dev client on your device and scan the QR code (or enter
the URL manually) — the same WiFi network as your dev machine is required
(see `mobile/.env`'s `EXPO_PUBLIC_API_URL`, set to your machine's LAN IP,
e.g. `http://192.168.1.20:8000`; `localhost` would resolve to the device
itself, not your dev machine).

**Limitation: this requires your laptop running nearby, on the same WiFi,
for the entire session.** There is no "detached" mode for local development —
if the dev machine sleeps, closes, or leaves the network, the app loses its
connection to both Metro and the backend. See Section 3 for a setup that
doesn't have this limitation.

---

## 2. Android Emulator (free, no device needed) — recommended path

The most complete free way to actually run and interact with the app
without owning a physical device or any paid account. Full step-by-step
procedure (SDK setup, AVD creation, troubleshooting) lives in
[`src/README.md`'s "Android Emulator Testing" section](./src/README.md#android-emulator-testing-procedure) —
this is the short version:

1. Install [Android Studio](https://developer.android.com/studio) and, from
   its SDK Manager, the Android Emulator + a current Android platform image
   (a **Play Store**-enabled image specifically — it lets Expo Go
   self-install onto the emulator).
2. Create and launch a Virtual Device (**More Actions → Virtual Device
   Manager → Create virtual device**), and let it fully boot.
3. Point the app at your backend via the emulator's special host alias —
   set (or create) `mobile/.env`:
   ```
   EXPO_PUBLIC_API_URL=http://10.0.2.2:8000
   ```
   `10.0.2.2` is Android's documented, always-reliable alias back to the
   host machine's `localhost` from inside any AVD — not something to
   configure per-network, unlike the physical-device LAN-IP setup above.
4. With the emulator running:
   ```sh
   cd mobile
   npx expo start --android
   ```
   Expo CLI detects the running emulator and installs Expo Go onto it
   automatically, then loads the app.

This gives full access to everything built so far — auth, navigation,
charts, offline persistence — **except push notifications**, which need a
physical device with a development build regardless of emulator vs. real
hardware (see Section 1). No Apple hardware, no paid account, no physical
Android device required.

---

## 3. Standalone setup (after Fly.io deployment, Day 40+)

Once the backend is deployed to Fly.io (planned Day 40+), the app can run
without any laptop nearby — point it at the deployed backend instead of a
local Docker Compose stack, and build a standalone binary that doesn't need
Metro running at all.

1. Update `mobile/.env`'s API URL to the deployed backend:
   ```
   EXPO_PUBLIC_API_URL=https://<your-app>.fly.dev
   ```
2. Build a **preview** binary (a real installable app, not a dev client —
   no Metro/laptop dependency once installed):
   ```sh
   eas build --profile preview --platform android   # free, no account needed
   eas build --profile preview --platform ios        # requires Apple Developer account
   ```
3. Install the Android `.apk` by downloading it directly from the EAS build
   link — no account needed on Apple's side, and Android has no equivalent
   registered-device requirement. The iOS build still needs the target
   device registered in your Apple Developer account first, same as
   Section 1's development build.

This is the first setup stage where **Android is fully unblocked** (build,
install, and run standalone, zero cost, zero account) while **iOS still
requires the $99/year Apple Developer Program membership** to install on
real hardware at all — that requirement doesn't go away until the app is
tested exclusively via Simulator (see `src/README.md`'s Testing Options for
the Mac-only, account-free iOS Simulator path).

---

## 4. Production (App Store / Play Store)

Required accounts:

| Platform | Account | Cost |
|---|---|---|
| iOS | [Apple Developer Program](https://developer.apple.com/programs/) | $99/year |
| Android | [Google Play Console](https://play.google.com/console/) | $25 one-time |

```sh
cd mobile
eas build --profile production --platform ios
eas build --profile production --platform android

eas submit --platform ios
eas submit --platform android
```

`eas submit` uploads the built binary to App Store Connect / the Play
Console directly from the command line — App Store review and Play Console's
own release rollout process still apply after that and aren't something EAS
controls.

---

## Honest limitations

- **Development (Section 1) needs your laptop running nearby, on the same
  WiFi, for the whole session.** No detached/standalone mode until Section 3
  (Fly.io deployment, Day 40+).
- **iOS on a physical device needs a paid Apple Developer account
  ($99/year)** at every stage — development, standalone preview, and
  production. There's no free path to real iOS hardware; the iOS Simulator
  (Mac + Xcode only, no paid account) is the only account-free way to see
  the app running on "iOS" before that membership makes sense.
- **Android is free at every stage except the final Play Store listing**
  ($25 one-time, paid once, covers unlimited future app submissions) —
  development builds, the emulator, and standalone preview builds all need
  no account beyond the free Expo account.
- **Push notifications need a physical device with a development build** —
  untestable in Expo Go, untestable in any emulator/simulator, iOS or
  Android. This project's push-notification code
  (`src/notifications/notificationHandler.ts`,
  `src/hooks/{usePushNotifications,useNotificationResponseListener}.ts`) has
  only been verified via `tsc`/Metro export, never run on a device — see
  `src/README.md`'s Testing Options for the full explanation.
- **No physical device or emulator has been used to verify this project
  yet** (as of Day 32) — every mobile checkpoint through Day 32 was verified
  via `npx tsc --noEmit` + `npx expo export --platform ios` (full Metro
  module-graph resolution) + code review only. This setup guide is
  necessarily unverified against a real running app; if a step here doesn't
  match reality, `src/README.md`'s per-file notes on what's genuinely been
  tested are the more trustworthy source for what's actually confirmed
  working.
