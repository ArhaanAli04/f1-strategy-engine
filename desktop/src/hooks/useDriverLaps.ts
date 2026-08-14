import { useQuery } from "@tanstack/react-query"
import * as driverApi from "@/api/driver"

// Large enough to cover a full race distance in one page — SimulatorPage's
// auto-fill needs the whole session's laps, not a UI-paged list. Backend
// caps page_size at 100 (backend/apis/v1/drivers.py).
const SESSION_LAPS_PAGE_SIZE = 100

export function driverLapsQueryOptions(sessionId: string | null, driverId: string | null) {
  return {
    queryKey: ["driver", "laps", sessionId, driverId] as const,
    queryFn: () =>
      driverApi.getDriverLaps(driverId as string, sessionId as string, {
        page_size: SESSION_LAPS_PAGE_SIZE,
      }),
    enabled: Boolean(sessionId && driverId),
    refetchInterval: 10_000,
  }
}

export function useDriverLaps(sessionId: string | null, driverId: string | null) {
  return useQuery(driverLapsQueryOptions(sessionId, driverId))
}
