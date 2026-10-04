import { useEffect } from "react"
import { emit, listen } from "@tauri-apps/api/event"
import { useRaceContextStore } from "@/stores/raceContextStore"

const RACE_CONTEXT_EVENT = "race-context-changed"

interface RaceContextPayload {
  sessionOverride: string | null
  driverId: string | null
}

// Call once per window (main and overlay both mount this). Applies incoming
// broadcasts to this window's own local store, so a change made in the main
// window reaches an overlay that is already open. (An overlay opened later
// reads the saved values from localStorage instead.)
export function useRaceContextBridge(): void {
  useEffect(() => {
    const unlistenPromise = listen<RaceContextPayload>(RACE_CONTEXT_EVENT, (event) => {
      useRaceContextStore.getState().setContext(event.payload.sessionOverride, event.payload.driverId)
    })
    return () => {
      void unlistenPromise.then((unlisten) => unlisten())
    }
  }, [])
}

// Updates this window's own store immediately, then broadcasts to the rest
// (the overlay, and this window's own listener above — a harmless same-value
// re-application).
function broadcast(sessionOverride: string | null, driverId: string | null): void {
  useRaceContextStore.getState().setContext(sessionOverride, driverId)
  void emit(RACE_CONTEXT_EVENT, { sessionOverride, driverId } satisfies RaceContextPayload)
}

// The Dashboard's session override; null returns to the automatic session.
export function setSessionOverride(sessionOverride: string | null): void {
  broadcast(sessionOverride, useRaceContextStore.getState().driverId)
}

// "Your driver"; null clears it (Driver Analytics' "Back to all drivers").
export function setContextDriver(driverId: string | null): void {
  broadcast(useRaceContextStore.getState().sessionOverride, driverId)
}
