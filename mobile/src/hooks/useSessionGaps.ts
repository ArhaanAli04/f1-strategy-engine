import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// Hand-written — mirrors web/src/hooks/useSessionGaps.ts exactly.
const GAPS_POLL_INTERVAL_MS = 8_000

export function useSessionGaps(sessionId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "gaps", sessionId],
    queryFn: () => telemetryApi.getSessionGaps(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: GAPS_POLL_INTERVAL_MS,
  })
}
