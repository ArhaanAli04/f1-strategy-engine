import { useLiveTelemetryStore } from "@/stores/liveTelemetryStore"
import type { LapCompletedEvent } from "@/types"

const NO_LAPS: Record<string, LapCompletedEvent> = {}

// Lap events from the app's one shared live connection (LiveTelemetryBridge),
// for sessionId only: empty when the shared connection is on another session.
// Use this, not useLiveTelemetry, in screens and components: each
// useLiveTelemetry call opens its own WebSocket. Day 6b-mobile CP4,
// 2026-10-09.
export function useSharedLiveTelemetry(sessionId: string | null): {
  lapsByDriver: Record<string, LapCompletedEvent>
} {
  const storeSessionId = useLiveTelemetryStore((state) => state.sessionId)
  const lapsByDriver = useLiveTelemetryStore((state) => state.lapsByDriver)
  const matches = sessionId !== null && storeSessionId === sessionId
  return { lapsByDriver: matches ? lapsByDriver : NO_LAPS }
}
