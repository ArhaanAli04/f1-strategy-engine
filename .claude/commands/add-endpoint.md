# /add-endpoint — Scaffold a complete new API endpoint

Use this when adding a new FastAPI endpoint. It generates all 4 layers at once
and ensures they are consistent with each other.

## What to do

Ask the user for:
1. Endpoint name and HTTP method (e.g. GET /strategy/{session_id}/overtake-window)
2. What data it returns (describe the response shape)
3. What parameters it accepts (path params, query params, request body)
4. Whether it requires authentication (default: yes)

Then:

### Step 1 — Schema first
Read schemas/ directory to understand existing patterns.
Add request and response Pydantic models to the appropriate schema file.
Follow the naming convention: {Resource}Request, {Resource}Response.
Always include model_config = ConfigDict(from_attributes=True).

### Step 2 — Service method
Read services/ to find which service owns this feature.
Add the service method. It MUST:
- Accept only typed arguments (no raw dicts)
- Check Redis cache first (use cache_service.cache_get)
- On cache miss: compute, then cache_service.cache_set with correct TTL
- Return the Pydantic response model, not a raw dict
- Have a docstring with Args and Returns

### Step 3 — Route handler
Read apis/v1/ to find the correct router file.
Add the route handler. It must:
- Have response_model= pointing to the Pydantic response schema
- Have a summary= and description= for Swagger docs
- Call exactly one service method
- Use Depends(get_current_user) unless endpoint is explicitly public
- Return the service method result directly

### Step 4 — Unit test
Read tests/unit/ to understand existing test patterns.
Add a unit test that:
- Mocks the DB session and Redis client (use existing fixtures)
- Tests the service method directly (not via HTTP)
- Includes at least: happy path, cache hit path, and one error case
- Is tagged @pytest.mark.unit

### Step 5 — Verify
Run: `python -c "from backend.apis.v1.{router} import router"` to check imports.
Run: `make test-unit -k test_{new_test_name}` to verify the new test passes.