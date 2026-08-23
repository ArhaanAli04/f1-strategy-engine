import { useCallback, useEffect, useRef, useState } from "react"
import { useWebSocket, type WebSocketReadyState } from "./useWebSocket"
import { useAuthStore } from "@/stores/authStore"
import { WS_URL } from "@/utils/constants"
import type { LapCompletedEvent, TelemetryStreamMessage } from "@/types"

// Connected but silent for this long reads as "worker/beat are scaled to 0"
// (Day 40 hybrid deployment — see fly.toml), not a connection failure,
// which readyState already covers on its own.
const STALE_TIMEOUT_MS = 30_000

export interface UseLiveTelemetryResult {
  lapsByDriver: Record<string, LapCompletedEvent>
  readyState: WebSocketReadyState
  staleConnection: boolean
}

// Subscribes to /ws/telemetry/{sessionId}. Auth is via ?token=... (a JWT
// access token) since the browser WebSocket API can't set an Authorization
// header on the handshake — see backend/apis/v1/telemetry.py's
// websocket_telemetry docstring (accepted limitation, short-lived WS ticket
// deferred, see CLAUDE.md).
export function useLiveTelemetry(sessionId: string | null): UseLiveTelemetryResult {
  const accessToken = useAuthStore((state) => state.accessToken)
  const [lapsByDriver, setLapsByDriver] = useState<Record<string, LapCompletedEvent>>({})
  const [staleConnection, setStaleConnection] = useState(false)
  const hasReceivedDataRef = useRef(false)

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message = JSON.parse(event.data as string) as TelemetryStreamMessage
      hasReceivedDataRef.current = true
      setStaleConnection(false)
      setLapsByDriver((prev) => ({ ...prev, [message.data.driver_id]: message.data }))
    } catch {
      // Malformed frame — drop it rather than crash the stream.
    }
  }, [])

  const url =
    sessionId && accessToken
      ? `${WS_URL}/api/v1/ws/telemetry/${sessionId}?token=${encodeURIComponent(accessToken)}`
      : null

  const { readyState } = useWebSocket(url, { onMessage: handleMessage })

  // Re-arms on every transition to "open" (including a reconnect), not just
  // once — a worker that comes back up mid-session should clear a prior
  // staleConnection, and a later drop-and-reconnect should be able to
  // re-flag it independently.
  useEffect(() => {
    if (readyState !== "open") {
      setStaleConnection(false)
      return
    }
    hasReceivedDataRef.current = false
    const timer = window.setTimeout(() => {
      if (!hasReceivedDataRef.current) setStaleConnection(true)
    }, STALE_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [readyState])

  return { lapsByDriver, readyState, staleConnection }
}
