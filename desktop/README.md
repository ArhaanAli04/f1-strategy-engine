# F1 Strategy Engine — Desktop

A native Windows app built with Tauri v2 and React. It uses the operating
system's own WebView instead of bundling Chromium, so the installer is about
5 MB. On top of the web app's features it adds an always-on-top race overlay,
a system tray icon, native notifications and CSV export of simulator results
(see [docs/features.md](../docs/features.md#10-desktop-app)).

## Installing

Download the installer from the repo's GitHub Releases. It isn't code-signed
yet, so Windows SmartScreen will warn on first run; see the
[v1.0.0 release notes](../docs/release-notes-v1.0.0.md) for how to proceed.
The app needs a backend running at `http://localhost:8000` (`make dev` from
the repo root).

## Building from source

Needs Node 20+, Rust (via [rustup](https://rustup.rs/)) and Visual Studio
Build Tools with the "Desktop development with C++" workload.

```bash
npm ci
npm run tauri dev      # run the app with hot reload
npm run tauri build    # build the installer
```

The backend address comes from `VITE_API_URL` (and `VITE_WS_URL` for the live
feed), set in `.env.local` for development and `.env.production` for
release builds. See `.env.example`.

## Relationship to `web/`

Much of `src/` is copied by hand from `web/src/`, because symlinks are
unreliable on Windows. [`src/README.md`](src/README.md) lists which files are
exact copies, which are adapted, and which are desktop-only. Check it
whenever a shared web file changes.
