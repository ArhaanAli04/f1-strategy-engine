import { useEffect, useMemo, useRef } from "react"
import { invoke } from "@tauri-apps/api/core"
import { useDrivers } from "@/hooks/useDrivers"
import { useNeighborDrivers } from "@/hooks/useNeighborDrivers"
import { useRaceSession } from "@/hooks/useRaceSession"
import { useCurrentLapHistoryEntry, useUndercut } from "@/hooks/useStrategy"
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
  const { sessionId } = useRaceSession()
  const driverId = useRaceContextStore((state) => state.driverId)

  const { data: drivers } = useDrivers()

  const codeById = useMemo(() => {
    const map = new Map<string, string>()
    for (const driver of drivers ?? []) map.set(driver.id, driver.code)
    return map
  }, [drivers])

  const { aheadId, behindId } = useNeighborDrivers(sessionId, driverId)

  // During a Demo Replay (lap events arriving on the WebSocket) the stored
  // prediction for the current lap is used, as UndercutThreatPanel does:
  // undercut_score is the opportunity against the car ahead, and the car
  // behind's chance of jumping this driver is 1 - overcut_score. The live
  // endpoint is then not called, so a replay runs no ML on the web machine.
  const { entry: historyEntry, isReplayActive } = useCurrentLapHistoryEntry(sessionId, driverId)
  const opportunity = useUndercut(sessionId, driverId, aheadId, !isReplayActive)
  const threat = useUndercut(sessionId, behindId, driverId, !isReplayActive)

  const opportunityProbability = isReplayActive
    ? (historyEntry?.undercut_score ?? null)
    : (opportunity.data?.probability_pit_now_gains_position ?? null)
  const threatProbability = isReplayActive
    ? historyEntry
      ? 1 - historyEntry.overcut_score
      : null
    : (threat.data?.probability_pit_now_gains_position ?? null)

  // Tracks which threat/opportunity pairings have already fired a
  // notification while their probability stays above THREAT_THRESHOLD —
  // fires once per crossing, not once per poll, and can fire again if the
  // probability drops and later re-crosses.
  const firedRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (opportunityProbability === null || !aheadId || !driverId) return
    const key = `opportunity:${sessionId}:${driverId}:${aheadId}`
    const probability = opportunityProbability
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
  }, [opportunityProbability, aheadId, sessionId, driverId, codeById])

  useEffect(() => {
    if (threatProbability === null || !behindId || !driverId) return
    const key = `threat:${sessionId}:${driverId}:${behindId}`
    const probability = threatProbability
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
  }, [threatProbability, behindId, sessionId, driverId, codeById])
}
