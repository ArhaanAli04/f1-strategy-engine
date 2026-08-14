import { useMemo } from "react"
import { useSessionGaps } from "@/hooks/useSessionGaps"

// Shared by useUndercutNotifications (fires OS notifications) and
// RaceOverlay (displays the same assessment) so both agree on who "the car
// ahead" / "the car behind" actually is.
export function useNeighborDrivers(sessionId: string | null, driverId: string | null) {
  const { data: gapsResponse } = useSessionGaps(sessionId)

  return useMemo(() => {
    const gaps = gapsResponse?.gaps ?? []
    const sorted = [...gaps].sort((a, b) => a.position - b.position)
    const index = sorted.findIndex((gap) => gap.driver_id === driverId)
    if (index === -1) return { aheadId: null as string | null, behindId: null as string | null }
    return {
      aheadId: sorted[index - 1]?.driver_id ?? null,
      behindId: sorted[index + 1]?.driver_id ?? null,
    }
  }, [gapsResponse, driverId])
}
