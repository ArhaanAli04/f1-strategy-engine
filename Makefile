.PHONY: install dev dev-down test test-unit test-int test-integration test-e2e lint type-check migrate new-migration train seed seed-circuits seed-teams ingest ingest-season backfill-tire-data ingest-live warm-cache fly-race-up fly-race-down

# Installs the backend (editable, with dev extras — there is no
# backend/requirements.txt, dependencies are declared in pyproject.toml)
# plus all three JS clients. Matches what ci.yml's jobs install per-target,
# just gathered into one entrypoint for a fresh clone.
install:
	pip install -e ".[dev]"
	pre-commit install
	cd web && npm ci
	cd desktop && npm ci
	cd mobile && npm ci

dev:
	# --env-file .env (only if present): Compose otherwise looks for .env
	# next to the compose file (infra/docker/), not repo root where it
	# actually lives — silently breaking alertmanager's
	# ${SLACK_WEBHOOK_CRITICAL}-style interpolation (Day 12). Conditional on
	# $(wildcard ...) because --env-file errors hard if the path doesn't
	# exist, and every var it feeds already has a ${VAR:-default} fallback
	# in docker-compose.yml — a fresh clone with no .env must still boot.
	docker compose -f infra/docker/docker-compose.yml $(if $(wildcard .env),--env-file .env,) up --build

dev-down:
	docker compose -f infra/docker/docker-compose.yml down

test:
	pytest backend/tests/ -v

test-unit:
	pytest backend/tests/unit/ -m unit -v

test-int:
	pytest backend/tests/integration/ -m integration -v

# Alias for test-int — kept alongside it since some docs/scripts refer to
# this target by the longer name. Both run the same suite.
test-integration: test-int

test-e2e:
	pytest backend/tests/e2e/ -m e2e -v

lint:
	ruff check backend/ --fix
	ruff format --check .
	cd web && npm run lint

type-check:
	mypy backend/ --strict
	cd web && npx tsc -b

migrate:
	alembic upgrade head

new-migration:
	alembic revision --autogenerate -m "$(MSG)"

train:
	python backend/scripts/train_models.py

seed:
	python backend/scripts/seed_circuits.py

seed-circuits:
	python backend/scripts/seed_circuits.py

seed-teams:
	python backend/scripts/seed_teams.py

ingest:
	python backend/scripts/ingest_historical.py --season $(SEASON) --round $(ROUND) --session-type $(SESSION_TYPE)

ingest-season:
	python backend/scripts/ingest_historical.py --season $(SEASON) --all-rounds --session-type R

backfill-tire-data:
	python backend/scripts/backfill_tire_data.py $(if $(SEASON),--season $(SEASON),)

ingest-live:
	python backend/scripts/ingest_live_session.py --season $(SEASON) $(if $(ROUND),--round $(ROUND) --session-type $(SESSION_TYPE),--poll)

warm-cache:
	python backend/scripts/warm_strategy_cache.py --session-id $(SESSION_ID)

fly-race-up:
	./infra/fly/scale-race-weekend.sh up

fly-race-down:
	./infra/fly/scale-race-weekend.sh down
