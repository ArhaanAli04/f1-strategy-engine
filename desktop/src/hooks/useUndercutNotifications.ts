import { useEffect, useMemo, useRef } from "react"
import { invoke } from "@tauri-apps/api/core"
import { useDrivers } from "@/hooks/useDrivers"
import { useNeighborDrivers } from "@/hooks/useNeighborDrivers"
import { useUndercut } from "@/hooks/useStrategy"
import { useRaceContextStore } from "@/stores/raceContextStore"

const THREAT_THRESHOLD = 0.7

// get_undercut_score's probability_pit_now_gains_position is always "the
// probability the *first* driver argument gains position over the *second*
// (target) by pitting now" (backend/services/strategy_service.py). So:
// - Opportunity (this driver could undercut the car ahead): call with
//   (sessionId, thisDriver, carAhead).
// - Threat (the car behind could undercut this driver): call with
//   (sessionId, carBehind, thisDriver) — driver/target swapped, since the
//   probability has to be computed from the behind car's perspective.
export function useUndercutNotifications(): void {
  const sessionId = useRaceContextStore((state) => state.sessionId)
  const driverId = useRaceContextStore((state) => state.driverId)

  const { data: drivers } = useDrivers()

  const codeById = useMemo(() => {
    const map = new Map<string, string>()
    for (const driver of drivers ?? []) map.set(driver.id, driver.code)
    return map
  }, [drivers])

  const { aheadId, behindId } = useNeighborDrivers(sessionId, driverId)

  const opportunity = useUndercut(sessionId, driverId, aheadId)
  const threat = useUndercut(sessionId, behindId, driverId)

  // Tracks which threat/opportunity pairings have already fired a
  // notification while their probability stays above THREAT_THRESHOLD —
  // fires once per crossing, not once per poll, and can fire again if the
  // probability drops and later re-crosses.
  const firedRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!opportunity.data || !aheadId || !driverId) return
    const key = `opportunity:${sessionId}:${driverId}:${aheadId}`
    const probability = opportunity.data.probability_pit_now_gains_position
    if (probability > THREAT_THRESHOLD) {
      if (firedRef.current.has(key)) return
      firedRef.current.add(key)
      const aheadCode = codeById.get(aheadId) ?? aheadId
      const selfCode = codeById.get(driverId) ?? driverId
      void invoke("send_threat_notification", {
        driver: selfCode,
        message: `Undercut opportunity! Pit now to jump ${aheadCode} (${Math.round(probability * 100)}% chance)`,
      })
    } else {
      firedRef.current.delete(key)
    }
  }, [opportunity.data, aheadId, sessionId, driverId, codeById])

  useEffect(() => {
    if (!threat.data || !behindId || !driverId) return
    const key = `threat:${sessionId}:${driverId}:${behindId}`
    const probability = threat.data.probability_pit_now_gains_position
    if (probability > THREAT_THRESHOLD) {
      if (firedRef.current.has(key)) return
      firedRef.current.add(key)
      const behindCode = codeById.get(behindId) ?? behindId
      const selfCode = codeById.get(driverId) ?? driverId
      void invoke("send_threat_notification", {
        driver: selfCode,
        message: `Undercut threat! ${behindCode} behind you may pit to jump you (${Math.round(probability * 100)}% chance)`,
      })
    } else {
      firedRef.current.delete(key)
    }
  }, [threat.data, behindId, sessionId, driverId, codeById])
}
