import { useCallback, useEffect, useRef, useState } from "react"
import ReconnectingWebSocket, { type CloseEvent } from "reconnecting-websocket"

export type WebSocketReadyState = "connecting" | "open" | "closed"

export interface UseWebSocketOptions {
  enabled?: boolean
  // Must be stable across renders (useCallback) — changing identity tears
  // down and reopens the connection, since it's an effect dependency.
  onMessage?: (event: MessageEvent) => void
}

export interface UseWebSocketResult {
  readyState: WebSocketReadyState
  lastCloseEvent: CloseEvent | null
  send: (data: string) => void
}

// Generic reconnecting-websocket wrapper. Domain-specific parsing/typing
// (e.g. TelemetryStreamMessage) belongs in the hook that calls this one.
export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {},
): UseWebSocketResult {
  const { enabled = true, onMessage } = options
  const socketRef = useRef<ReconnectingWebSocket | null>(null)
  const [readyState, setReadyState] = useState<WebSocketReadyState>("connecting")
  const [lastCloseEvent, setLastCloseEvent] = useState<CloseEvent | null>(null)

  useEffect(() => {
    if (!url || !enabled) return

    const socket = new ReconnectingWebSocket(url)
    socketRef.current = socket
    setReadyState("connecting")

    const handleOpen = () => setReadyState("open")
    const handleClose = (event: CloseEvent) => {
      setReadyState("closed")
      setLastCloseEvent(event)
    }

    socket.addEventListener("open", handleOpen)
    socket.addEventListener("close", handleClose)
    if (onMessage) {
      socket.addEventListener("message", onMessage)
    }

    return () => {
      socket.close()
      socketRef.current = null
    }
  }, [url, enabled, onMessage])

  const send = useCallback((data: string) => {
    socketRef.current?.send(data)
  }, [])

  return { readyState, lastCloseEvent, send }
}
