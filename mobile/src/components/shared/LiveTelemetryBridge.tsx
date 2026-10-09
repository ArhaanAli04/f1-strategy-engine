import { useEffect } from "react"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useRaceSession } from "@/hooks/useRaceSession"
import { useIsAuthenticated } from "@/stores/authStore"
import { useLiveTelemetryStore } from "@/stores/liveTelemetryStore"

// Holds the app's one live telemetry WebSocket and publishes its lap events
// to liveTelemetryStore (Day 6b-mobile CP4, 2026-10-09). Mounted once in
// app/_layout.tsx beside the navigator, not around it, so a change of
// session never remounts the screens. Renders nothing.
export function LiveTelemetryBridge(): React.JSX.Element | null {
  const isAuthenticated = useIsAuthenticated()
  if (!isAuthenticated) return null
  return <SessionTelemetry />
}

function SessionTelemetry(): React.JSX.Element {
  const { sessionId, isReplay } = useRaceSession()
  // The key starts a fresh connection, with no lap events, whenever the
  // session or the replay state changes. Without it a replay that stops on
  // the race the app then falls back to (same session id) would leave its
  // last lap events behind, and the strategy panels would keep showing the
  // replay's stored predictions as if it were still running.
  return <SessionSubscriber key={`${sessionId ?? "none"}:${isReplay}`} sessionId={sessionId} />
}

function SessionSubscriber({ sessionId }: { sessionId: string | null }): null {
  const { lapsByDriver, readyState } = useLiveTelemetry(sessionId)
  const publish = useLiveTelemetryStore((state) => state.publish)

  useEffect(() => {
    publish(sessionId, lapsByDriver, readyState)
  }, [publish, sessionId, lapsByDriver, readyState])

  return null
}
