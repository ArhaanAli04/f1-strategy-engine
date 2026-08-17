import { useNetInfo } from "@react-native-community/netinfo"
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

const RECONNECT_DELAY_MS = 3000

// React Native ships a built-in global WebSocket — no reconnecting-websocket
// (browser-only, see CLAUDE.md's Day 31 notes). Reconnect-on-close is
// hand-rolled here with a fixed delay instead of that library's
// backoff/jitter — good enough for a mobile client on a LAN dev network.
export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {},
): UseWebSocketResult {
  const { enabled = true, onMessage } = options
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [readyState, setReadyState] = useState<WebSocketReadyState>("connecting")
  // isConnected === false is the only state that blocks connecting — null
  // (still determining, most platforms' default on first render) shouldn't
  // stop an otherwise-valid connection attempt. Offline -> back online
  // transitions re-run this effect (isConnected is a dependency below),
  // reopening the socket automatically.
  const { isConnected } = useNetInfo()

  useEffect(() => {
    if (!url || !enabled || isConnected === false) return
    let cancelled = false

    function connect() {
      if (cancelled) return
      const socket = new WebSocket(url as string)
      socketRef.current = socket
      setReadyState("connecting")

      socket.onopen = () => setReadyState("open")
      socket.onmessage = (event) => onMessage?.(event as unknown as MessageEvent)
      socket.onclose = () => {
        setReadyState("closed")
        if (!cancelled) {
          reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS)
        }
      }
      socket.onerror = () => {
        socket.close()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [url, enabled, isConnected, onMessage])

  const send = useCallback((data: string) => {
    socketRef.current?.send(data)
  }, [])

  return { readyState, send }
}
