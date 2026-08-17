import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// Gaps aren't pushed over a WebSocket in desktop (no live telemetry stream
// wired here) — poll GET /telemetry/{sessionId}/gaps instead. Matches the
// backend cache key's own 8s TTL (f1:{season}:{round}:gaps) closely enough
// without polling faster than the underlying value can change.
const GAPS_POLL_INTERVAL_MS = 8_000

export function useSessionGaps(sessionId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "gaps", sessionId],
    queryFn: () => telemetryApi.getSessionGaps(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: GAPS_POLL_INTERVAL_MS,
  })
}
