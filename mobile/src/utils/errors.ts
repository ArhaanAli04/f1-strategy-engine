import { isAxiosError } from "axios"

interface FastAPIValidationDetail {
  msg: string
}

// Backend error responses come in two distinct shapes (confirmed live against
// the running backend, Day 26):
// - F1StrategyError subclasses (backend/core/exceptions.py's
//   f1_strategy_error_handler, covers AuthenticationError/ConflictError/etc.):
//   { error: string, message: string, detail: unknown }
// - FastAPI/Pydantic request validation (422, framework default, never
//   reaches f1_strategy_error_handler): { detail: [{ msg: string, loc: [...] }, ...] }
// types/common.ts's ErrorResponse ({ detail: string, code }) does not match
// either shape on the wire — this reads the two real shapes directly instead.
export function getApiErrorMessage(error: unknown, fallback = "Something went wrong"): string {
  if (!isAxiosError(error)) {
    return error instanceof Error ? error.message : fallback
  }
  const data = error.response?.data as
    | { message?: string; detail?: string | FastAPIValidationDetail[] }
    | undefined
  if (typeof data?.message === "string") return data.message
  if (typeof data?.detail === "string") return data.detail
  if (Array.isArray(data?.detail) && typeof data.detail[0]?.msg === "string") {
    return data.detail[0].msg
  }
  return fallback
}
