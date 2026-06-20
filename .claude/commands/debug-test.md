# /debug-test — Investigate and fix a failing test

Use this when `make test` is failing and the cause is not immediately obvious.

## What to do

1. Run the failing test with maximum verbosity and show full traceback:
   ```
   cd backend && pytest {failing_test_path} -v --tb=long -s
   ```

2. Read the full error output carefully. Identify whether this is:
   - An import error → missing dependency or circular import
   - An assertion error → logic bug in the service or wrong test expectation
   - A fixture error → missing or misconfigured conftest.py fixture
   - A DB/Redis error → testcontainers not started or wrong connection string
   - A type error → Pydantic validation failure or wrong argument type

3. Read the actual source file being tested (not the test file) to understand
   what the function is supposed to do.

4. Read the test file to understand what the test is asserting.

5. Identify root cause. State it clearly before making any change:
   "The failure is because X, which happens because Y."

6. Make the minimal fix required. Do not refactor unrelated code while fixing.

7. Re-run the specific test to confirm it passes:
   ```
   pytest {failing_test_path}::{test_function_name} -v
   ```

8. Run the full test suite to confirm no regressions:
   ```
   make test-unit
   ```

## Rules
- Never delete a test to make it pass. Fix the underlying code or fix the test assertion.
- If the test was correct and the implementation was wrong: fix the implementation.
- If the implementation was correct and the test expectation was wrong: fix the test.
- Document which one was wrong in the commit message.