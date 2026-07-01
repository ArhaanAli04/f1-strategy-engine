# /migrate — Create and apply a new Alembic migration

Use this whenever a SQLAlchemy model has been changed or a new one added.
NEVER skip this. NEVER manually edit existing migration files.

## What to do

1. Read the current state of models/ to understand what changed.

2. Ask the user: "What changed in the models? Summarise in 3–5 words for the 
   migration message." Wait for their answer.

3. Generate the migration:
   ```
   cd backend && alembic revision --autogenerate -m "{user's message}"
   ```

4. READ the generated migration file in migrations/versions/ before applying it.
   Check for:
   - Any DROP COLUMN or DROP TABLE commands → STOP and show user, require explicit approval
   - Any column type changes → STOP and show user, require explicit approval
   - Missing nullable=True on new columns added to existing tables → fix before applying
   - Correct op.create_index for any new columns that will be queried frequently

5. Show the user the migration diff and ask: "Does this look correct? Applying now."

6. Apply:
   ```
   cd backend && alembic upgrade head
   ```

7. Verify:
   ```
   cd backend && alembic current
   ```
   Confirm it shows the new revision as (head).

8. Run a quick schema check:
   ```
   psql $DATABASE_URL -c "\dt" | grep -E "(table|view)"
   ```
   Verify expected tables exist.

9. Commit the migration file:
   ```
   git add backend/migrations/versions/
   git commit -m "migration: {description}"
   ```

## Warning
If `alembic upgrade head` fails mid-migration, run `alembic downgrade -1`
before attempting to fix and re-run. Never leave the DB in a partial migration state.