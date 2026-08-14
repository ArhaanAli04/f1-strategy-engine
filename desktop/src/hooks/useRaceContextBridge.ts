import { useEffect } from "react"
import { emit, listen } from "@tauri-apps/api/event"
import { useRaceContextStore } from "@/stores/raceContextStore"

const RACE_CONTEXT_EVENT = "race-context-changed"

interface RaceContextPayload {
  sessionId: string | null
  driverId: string | null
}

// Call once per window (main and overlay both mount this). Applies incoming
// broadcasts to this window's own local store — the only way the overlay's
// separate WebView process learns what the main window's user selected.
export function useRaceContextBridge(): void {
  useEffect(() => {
    const unlistenPromise = listen<RaceContextPayload>(RACE_CONTEXT_EVENT, (event) => {
      useRaceContextStore.getState().setContext(event.payload.sessionId, event.payload.driverId)
    })
    return () => {
      void unlistenPromise.then((unlisten) => unlisten())
    }
  }, [])
}

// Called by the main window whenever the user edits session/driver context.
// Updates this window's own store immediately, then broadcasts to the rest
// (the overlay, and this window's own listener above — a harmless
// same-value re-application).
export function broadcastRaceContext(sessionId: string | null, driverId: string | null): void {
  useRaceContextStore.getState().setContext(sessionId, driverId)
  void emit(RACE_CONTEXT_EVENT, { sessionId, driverId } satisfies RaceContextPayload)
}
