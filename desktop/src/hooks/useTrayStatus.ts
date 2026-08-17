import { useEffect, useRef } from "react"
import { invoke } from "@tauri-apps/api/core"
import * as telemetryApi from "@/api/telemetry"
import { useRaceContextStore } from "@/stores/raceContextStore"

const TRAY_POLL_INTERVAL_MS = 30_000
// A session counts as "active" if at least one car has reported a position
// sample within this window. Uses GET /telemetry/{session_id}/positions
// (unauthenticated) rather than the gaps Redis key directly, since the tray
// poll runs continuously regardless of login state.
const FRESHNESS_WINDOW_MS = 60_000

// Polls session freshness and drives the tray icon (green = active session,
// grey = idle) via the set_tray_status Tauri command. Call once, in the
// main window only — the tray is process-global, not per-window.
export function useTrayStatus(): void {
  const sessionId = useRaceContextStore((state) => state.sessionId)
  const lastStatusRef = useRef<boolean | null>(null)

  useEffect(() => {
    let cancelled = false

    async function poll() {
      let active = false
      if (sessionId) {
        try {
          const positions = await telemetryApi.getDriverPositions(sessionId)
          active = positions.some((position) => {
            if (!position.timestamp) return false
            return Date.now() - new Date(position.timestamp).getTime() < FRESHNESS_WINDOW_MS
          })
        } catch {
          active = false
        }
      }
      if (cancelled || lastStatusRef.current === active) return
      lastStatusRef.current = active
      void invoke("set_tray_status", { status: active })
    }

    void poll()
    const interval = setInterval(() => void poll(), TRAY_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [sessionId])
}
