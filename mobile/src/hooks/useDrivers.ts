import { useQuery } from "@tanstack/react-query"
import * as driverApi from "@/api/driver"

// Hand-written — mirrors web/src/hooks/useDrivers.ts. Introduced early
// (Checkpoint 3) because Settings' alert-subscriptions section needs the
// roster; Checkpoint 4's Drivers tab reuses this same hook.
export function useDrivers() {
  return useQuery({
    queryKey: ["drivers"],
    queryFn: driverApi.listDrivers,
  })
}
