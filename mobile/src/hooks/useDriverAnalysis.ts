import { useQuery } from "@tanstack/react-query"
import * as driverApi from "@/api/driver"

// Web defines this query inline inside StyleRadar.tsx (its only consumer).
// Mobile's Driver Detail screen also needs `archetype` for its header (per
// Day 32 spec), so it's extracted here — same queryKey/queryFn as web's
// inline version, just shared between the screen header and StyleRadar so
// react-query dedupes the request instead of firing it twice.
export function useDriverAnalysis(driverId: string | null, sessionId: string | null) {
  return useQuery({
    queryKey: ["driver", "analysis", driverId, sessionId],
    queryFn: () => driverApi.getDriverAnalysis(driverId as string, sessionId as string),
    enabled: Boolean(driverId && sessionId),
    retry: false,
  })
}
