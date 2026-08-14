import { useQuery } from "@tanstack/react-query"
import * as driverApi from "@/api/driver"

// Roster (code, name, team/contract) barely changes within a session —
// long staleTime avoids refetching this on every component that needs it.
const DRIVERS_STALE_TIME_MS = 300_000

export function useDrivers() {
  return useQuery({
    queryKey: ["drivers", "list"],
    queryFn: () => driverApi.listDrivers(),
    staleTime: DRIVERS_STALE_TIME_MS,
  })
}
