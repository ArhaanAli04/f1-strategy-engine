import { useQuery } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"

// The backend caches this for 24h (strategy_service.LAST_INGESTED_SESSION_TTL_SECONDS)
// — it only changes when new race data is ingested — so there's no value in
// refetching it on the client faster than that.
const STALE_TIME_MS = 60 * 60 * 1000

// enabled: only the Strategy Simulator's non-live mode needs this. 404 (a
// fresh DB with no ingested races) is a normal state, surfaced by the page
// as "No ingested race available", not a global error toast.
export function useLastIngestedSession(enabled: boolean) {
  return useQuery({
    queryKey: ["strategy", "last-ingested-session"],
    queryFn: strategyApi.getLastIngestedSession,
    enabled,
    staleTime: STALE_TIME_MS,
    retry: false,
    meta: { silentOn404: true },
  })
}
