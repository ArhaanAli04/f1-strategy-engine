# /pre-commit — Run all checks before committing

Run this before every `git commit`. It catches everything CI would catch,
so you don't push a broken commit and waste a CI run.

## What to do

Run all of these in sequence. Stop at the first failure and fix it.

```bash
# 1. Lint and format check
cd backend && ruff check . --fix
cd backend && ruff format .
cd backend && mypy . --strict

# 2. Unit tests (fastest — catches logic bugs)
make test-unit

# 3. Import check on all new files
# For each new .py file created this session:
python -c "import backend.{module.path}"

# 4. Schema validation check
# For each new Pydantic schema created this session:
python -c "
from backend.schemas.{schema_file} import {SchemaName}
import json
print(json.dumps({SchemaName}.model_json_schema(), indent=2))
"

# 5. Migration check (only if models changed)
cd backend && alembic check
# This command prints "No new upgrade operations detected" if migrations are up to date.
# If it shows pending changes, run /migrate before committing.

# 6. Docker build check (only if Dockerfile or docker-compose changed)
docker build -f infra/docker/Dockerfile.backend -t f1-backend:test . --no-cache
```

## STOP HERE — hand control back to the user

Once all checks above pass, produce a clean summary table of what passed
and report it to the user. Do NOT run any git commands.

The following steps are reserved for the user every single day without exception:
- `git add .`
- `git commit -m "..."`
- `git push origin feature/day-XX-description`

These are intentionally manual steps. Do not run them even if all checks pass.
Do not suggest running them. Just report that all checks passed and stop.

## If mypy fails

Do not suppress mypy errors with `# type: ignore` unless you have a very specific
reason and document it with a comment. Fix the actual type issue instead.
Common fixes:
- Missing return type annotation → add `-> ReturnType:`
- Optional that could be None → add `if x is None: raise ...` guard
- Unknown dict shape → replace with a typed TypedDict or Pydantic model