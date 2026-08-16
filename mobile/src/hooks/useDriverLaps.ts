import { useQuery } from "@tanstack/react-query"
import * as driverApi from "@/api/driver"

// Hand-written — mirrors web/src/hooks/useDriverLaps.ts exactly.
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
