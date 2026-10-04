import { useCallback, useEffect, useRef, useState } from "react"

export type WebSocketReadyState = "connecting" | "open" | "closed"

export interface UseWebSocketOptions {
  enabled?: boolean
  // Must be stable across renders (useCallback) — changing identity tears
  // down and reopens the connection, since it's an effect dependency.
  onMessage?: (event: MessageEvent) => void
}

export interface UseWebSocketResult {
  readyState: WebSocketReadyState
  send: (data: string) => void
}

const RECONNECT_BASE_DELAY_MS = 1_000
const RECONNECT_MAX_DELAY_MS = 30_000

// Hand-written for desktop (demo deployment Day 6b) — same interface as web's
// useWebSocket, which wraps the reconnecting-websocket package. Desktop uses
// the WebView's built-in WebSocket instead, so it needs no new dependency
// (mobile does the same, see mobile/src/hooks/useWebSocket.ts). Reconnects
// after every close with exponential backoff, 1 s doubling up to 30 s,
// reset once a connection opens — so a backend restart reconnects quickly,
// but a long outage isn't retried every second.
export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {},
): UseWebSocketResult {
  const { enabled = true, onMessage } = options
  const socketRef = useRef<WebSocket | null>(null)
  const [readyState, setReadyState] = useState<WebSocketReadyState>("connecting")

  useEffect(() => {
    if (!url || !enabled) return
    let cancelled = false
    let attempt = 0
    let reconnectTimer: number | undefined

    function connect() {
      if (cancelled) return
      const socket = new WebSocket(url as string)
      socketRef.current = socket
      setReadyState("connecting")

      socket.onopen = () => {
        attempt = 0
        setReadyState("open")
      }
      socket.onmessage = (event) => onMessage?.(event)
      socket.onclose = () => {
        setReadyState("closed")
        if (cancelled) return
        const delay = Math.min(RECONNECT_BASE_DELAY_MS * 2 ** attempt, RECONNECT_MAX_DELAY_MS)
        attempt += 1
        reconnectTimer = window.setTimeout(connect, delay)
      }
      // An error is always followed by close, which schedules the reconnect.
      socket.onerror = () => socket.close()
    }

    connect()

    return () => {
      cancelled = true
      window.clearTimeout(reconnectTimer)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [url, enabled, onMessage])

  const send = useCallback((data: string) => {
    socketRef.current?.send(data)
  }, [])

  return { readyState, send }
}
