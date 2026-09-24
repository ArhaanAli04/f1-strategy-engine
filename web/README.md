# F1 Strategy Engine — Web

The main client: React + Vite + TypeScript, with TanStack Query for server
data, Zustand for UI state, Recharts for charts and shadcn/ui components.
Deployed to Vercel from `main` (`.github/workflows/cd-web.yml`).

For what the app does, see [docs/features.md](../docs/features.md).

## Running it

The backend needs to be running first (`make dev` from the repo root, see the
[main README](../README.md#quick-start-local-development)).

```bash
npm ci
npm run dev        # http://localhost:5173
```

The app reads the backend's address from two variables with no built-in
default, so create `web/.env.local` with:

```
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Start the dev server with hot reload |
| `npm run build` | Type-check (`tsc -b`) and build for production |
| `npm run test` | Run the Vitest suite once |
| `npm run lint` | Lint with oxlint |
| `npm run preview` | Serve the production build locally |

## Layout

| Folder | Contents |
|---|---|
| `src/pages/` | One component per route (dashboard, race, driver, simulator, alerts) |
| `src/components/` | Feature components, grouped by area (`telemetry/`, `circuit/`, `strategy/`, `driver/`, …), plus `ui/` for shadcn primitives |
| `src/hooks/` | TanStack Query hooks and the WebSocket hook |
| `src/api/` | Typed API client, one file per backend area |
| `src/types/` | TypeScript types mirroring the backend's response schemas |
| `src/stores/` | Zustand stores (auth, selected session/driver, alerts) |

Several of these folders are copied by hand into `desktop/` and `mobile/`.
If you change a shared file here, check `desktop/src/README.md` and
`mobile/src/README.md` for what needs syncing.
