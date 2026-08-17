import { useCallback, useState } from "react"
import { useWebSocket, type WebSocketReadyState } from "./useWebSocket"
import { useAuthStore } from "@/stores/authStore"
import { WS_URL } from "@/utils/constants"
import type { LapCompletedEvent, TelemetryStreamMessage } from "@/types"

export interface UseLiveTelemetryResult {
  lapsByDriver: Record<string, LapCompletedEvent>
  readyState: WebSocketReadyState
}

// Hand-written — mirrors web/src/hooks/useLiveTelemetry.ts's logic exactly,
// on top of the RN-native useWebSocket above instead of
// reconnecting-websocket. Same ?token=... auth caveat applies (see
// CLAUDE.md's "WebSocket JWT in query param" deferred-wiring note).
// Offline gating (Day 32 Checkpoint 5) lives inside useWebSocket itself
// (checks NetInfo's isConnected before opening) — this hook inherits it
// automatically rather than duplicating the check. lapsByDriver simply
// stops receiving new entries while offline; consumers (e.g. live.tsx)
// already fall back to their own last-known REST data per driver when no
// live WS event exists for that driver.
export function useLiveTelemetry(sessionId: string | null): UseLiveTelemetryResult {
  const accessToken = useAuthStore((state) => state.accessToken)
  const [lapsByDriver, setLapsByDriver] = useState<Record<string, LapCompletedEvent>>({})

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message = JSON.parse(event.data as string) as TelemetryStreamMessage
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

  return { lapsByDriver, readyState }
}
