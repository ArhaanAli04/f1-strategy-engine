import { create } from "zustand"
import type { WebSocketReadyState } from "@/hooks/useWebSocket"
import type { LapCompletedEvent } from "@/types"

// The app's one live telemetry connection, shared by every screen (Day
// 6b-mobile CP4, 2026-10-09, owner's option A). LiveTelemetryBridge (mounted
// once in app/_layout.tsx) holds the WebSocket for the current race session
// and writes its lap events here; screens read them through
// useSharedLiveTelemetry. Web opens one connection per component instead
// (its strategy wall alone opens about 20); a phone shouldn't.
interface LiveTelemetryState {
  // The session these events belong to, so a screen showing another session
  // reads nothing rather than the wrong race's laps.
  sessionId: string | null
  lapsByDriver: Record<string, LapCompletedEvent>
  readyState: WebSocketReadyState
  publish: (
    sessionId: string | null,
    lapsByDriver: Record<string, LapCompletedEvent>,
    readyState: WebSocketReadyState,
  ) => void
}

export const useLiveTelemetryStore = create<LiveTelemetryState>((set) => ({
  sessionId: null,
  lapsByDriver: {},
  readyState: "closed",
  publish: (sessionId, lapsByDriver, readyState) => set({ sessionId, lapsByDriver, readyState }),
}))
