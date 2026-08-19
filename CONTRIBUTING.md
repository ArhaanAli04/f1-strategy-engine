# Contributing

A practical guide to working on this repo — branching, commits, PRs, and the
checks that actually gate a merge. Every command below is copied from what
this repo's own `Makefile`/`package.json`/`.pre-commit-config.yaml`/CI
workflows actually run, not an idealized version of it.

## Getting Started

**Prerequisites** (same as [README.md](README.md)):
- Python 3.12+
- Node 20+
- Docker Desktop
- Rust + Visual Studio Build Tools — only if you're building the desktop app

**Local development setup:**
```bash
git clone https://github.com/ArhaanAli04/f1-strategy-engine
cd f1-strategy-engine
cp .env.example .env          # edit with your own credentials
make install                  # backend (editable+dev extras) + web/desktop/mobile npm deps + pre-commit hooks
make dev                      # starts Postgres, Redis, backend, worker, monitoring stack
cd web && npm run dev         # starts the web app — http://localhost:5173
```

**Verify everything works:**
```bash
make test-unit      # backend unit tests — no DB/Redis/network
make type-check      # mypy --strict + web's tsc -b
make lint            # ruff check/format + web's oxlint
```

## Branching Strategy

- `feature/day-XX-description` — a numbered day's build session (this
  project's primary unit of work; matches `/run-day`'s convention).
- `feature/description` — a feature branch not tied to a specific numbered
  day (e.g. a cross-cutting fix pass).
- `bugfix/description` — a non-urgent bug fix.
- `hotfix/description` — a critical fix, branched from and merged directly
  back into `develop` rather than waiting for the next feature branch.
- **Never push directly to `main`.** `main` only moves via a `develop` →
  `main` PR at the end of a phase — every day-to-day feature/bugfix/hotfix
  branch targets `develop`, not `main`.

*Note on actual practice so far:* this repo's git history to date used a
shorter `fix/description` prefix (e.g. `fix/retrain-incremental-session-load`)
rather than `bugfix/description` for non-urgent fixes, and no `hotfix/`
branch has been used yet. The convention above is what new contributions
should follow going forward — worth knowing if you're looking at branch
history and the prefixes don't line up with what's described here.

## Development Workflow

1. `git checkout develop && git pull origin develop`
2. `git checkout -b feature/your-feature`
3. Make your changes.
4. Run the pre-commit checks (see [Pre-commit Checklist](#pre-commit-checklist) below).
5. `git add . && git commit -m "type: description"`
6. `git push origin feature/your-feature`
7. Open a PR: base `develop` ← compare `feature/your-branch`.
8. CI must pass before merge.
9. Merge → delete the branch.

## Commit Message Format

```
type: short description
```

Types used in this repo: `feat`, `fix`, `update`, `refactor`, `docs`,
`test`, `chore` — plus this project's own day-session convention, `Day XX:
what was built`, for the commit that closes out a numbered day.

Real examples from this repo's history:
- `Day 33: Vitest tests, Vercel CD, desktop CD pipeline`
- `fix: update CI integration test SECRET_KEY to meet 32-char JWT validator requirement`
- `update: phase tracker after Day 35`

## Pre-commit Checklist

**Automated on every `git commit`** via `.pre-commit-config.yaml`
(installed by `make install`) — Python files only:
- `ruff check --fix`
- `ruff format`
- `mypy backend/ --strict`

These hooks don't run the test suite or touch the JS clients — run the
rest of this checklist yourself before pushing, since it's exactly what CI
(`ci.yml`) re-checks on your PR:

**Backend:**
```bash
ruff check backend/
ruff format --check .
mypy backend/ --strict
make test-unit
```

**Web** (`cd web`):
```bash
npm run lint      # oxlint
npm run test       # vitest run
npm run build      # tsc -b && vite build
```

**Desktop** (`cd desktop`) — no dedicated lint/test scripts exist yet,
matching what `ci.yml`'s `desktop-check` job actually runs:
```bash
npx tsc --noEmit
npm run build      # tsc && vite build
```

**Mobile** (`cd mobile`) — same story, no `package.json` scripts for these;
run via `npx` directly, matching `ci.yml`'s `mobile-check` job:
```bash
npx tsc --noEmit
npx expo export --platform ios
```

## Pull Request Process

- **Title format:** matches commit message style (`type: short description`).
- **Description:** what changed and why — link the day/issue it relates to
  if applicable.
- **CI must be green before merging** — `ci.yml` runs frontend/desktop/
  mobile checks, backend lint, unit tests, integration tests, e2e tests,
  and a Docker build check (path-filtered — see `ci.yml`'s `changes` job,
  so a backend-only PR doesn't wait on an unrelated mobile check, etc.).
- **No self-merging without CI passing.** A red CI check is a blocker, not
  a suggestion.

## Code Style

- **Python:** `ruff` for both linting and formatting, `mypy --strict` for
  types — zero untyped function signatures (see `CLAUDE.md`'s Code Quality
  Standards).
- **TypeScript:** `oxlint` (web), `tsc` in strict mode across all three
  clients.
- **No hardcoded secrets** — `.gitignore` already excludes `.env` and
  credential files; every secret must come from an environment variable
  via `core/config.py` (see `CLAUDE.md`'s Secrets rule). **Note:** this
  repo does not currently run an automated secret-scanning tool (e.g.
  GitGuardian) in CI or as a pre-commit hook — there's nothing in
  `.github/workflows/` or `.pre-commit-config.yaml` that does this today,
  so treat this as a manual review responsibility until one is wired up,
  not an automated safety net.
- **No new dependencies without asking first** — this is a hard rule (see
  the user's global preferences in `CLAUDE.md`'s companion instructions);
  any new package needs sign-off before it lands in `pyproject.toml` or a
  client's `package.json`.

## Running Tests

- **Backend unit:** `make test-unit` (no DB/Redis/network — see
  `tests/unit/`).
- **Backend integration:** `make test-int` / `make test-integration`
  (requires a real Postgres + Redis — either the local Docker stack via
  `make dev`, or testcontainers spins them up automatically depending on
  the test).
- **Backend e2e:** `make test-e2e` (Playwright, full stack — see `ci.yml`'s
  `e2e-tests` job for how CI stands up backend+worker for this).
- **Frontend:** `cd web && npm run test` (Vitest).
- **Load test:** see [docs/runbook.md](docs/runbook.md) and
  `tests/load/locustfile.py` — not part of the normal PR checklist, run
  ahead of a known high-traffic event (see the Race day checklist).
