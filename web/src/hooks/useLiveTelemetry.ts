import { useCallback, useState } from "react"
import { useWebSocket, type WebSocketReadyState } from "./useWebSocket"
import { useAuthStore } from "@/stores/authStore"
import { WS_URL } from "@/utils/constants"
import type { LapCompletedEvent, TelemetryStreamMessage } from "@/types"

export interface UseLiveTelemetryResult {
  lapsByDriver: Record<string, LapCompletedEvent>
  readyState: WebSocketReadyState
}

// Subscribes to /ws/telemetry/{sessionId}. Auth is via ?token=... (a JWT
// access token) since the browser WebSocket API can't set an Authorization
// header on the handshake — see backend/apis/v1/telemetry.py's
// websocket_telemetry docstring (accepted limitation, short-lived WS ticket
// deferred, see CLAUDE.md).
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
