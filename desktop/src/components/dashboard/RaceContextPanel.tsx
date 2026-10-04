import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { invoke } from "@tauri-apps/api/core"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { setContextDriver, setSessionOverride } from "@/hooks/useRaceContextBridge"
import { useRaceSession } from "@/hooks/useRaceSession"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { useRaceContextStore } from "@/stores/raceContextStore"

// Moved here from the global header (Checkpoint 6 feedback) — this is
// dashboard-level session setup, not something every page's chrome needs.
// Since demo deployment Day 6b the session is automatic, like web; the field
// here is only an optional override. Both values are saved across restarts.
export function RaceContextPanel() {
  const [isOpen, setIsOpen] = useState(true)
  const sessionOverride = useRaceContextStore((state) => state.sessionOverride)
  const driverId = useRaceContextStore((state) => state.driverId)
  const automatic = useResolvedSession()
  const current = useRaceSession()

  const automaticLabel = automatic.sessionId
    ? `${automatic.raceName ?? "Race"} — ${automatic.isLive ? "live now" : "most recent completed race"}`
    : "No race found yet"
  const sessionLabel = sessionOverride
    ? "Override (below)"
    : current.isReplay
      ? `Demo Replay — ${current.raceName ?? "Race"} (until it stops)`
      : `Automatic — ${automaticLabel}`

  return (
    <Card>
      <CardHeader>
        <button
          type="button"
          onClick={() => setIsOpen((open) => !open)}
          className="flex w-full items-center justify-between text-left"
          aria-expanded={isOpen}
        >
          <div>
            <CardTitle>Race context</CardTitle>
            <CardDescription>
              Drives the tray status, undercut notifications, and the overlay window's timing tower.
            </CardDescription>
          </div>
          {isOpen ? (
            <ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
          )}
        </button>
      </CardHeader>
      {isOpen && (
        <CardContent className="space-y-4">
          <div className="space-y-1 text-sm">
            <div>
              <span className="text-muted-foreground">Session: </span>
              <span className="font-medium">{sessionLabel}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              Every page follows a running Demo Replay, otherwise the live race, or the most
              recent completed race when none is live. Enter a session ID below only to look at a
              different session.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="session-id">Session override (optional)</Label>
              <div className="flex gap-2">
                <Input
                  id="session-id"
                  placeholder="Session UUID"
                  value={sessionOverride ?? ""}
                  onChange={(event) => setSessionOverride(event.target.value.trim() || null)}
                />
                {sessionOverride && (
                  <Button variant="outline" size="sm" onClick={() => setSessionOverride(null)}>
                    Use automatic
                  </Button>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="driver-id">Your driver ID</Label>
              <Input
                id="driver-id"
                placeholder="Driver UUID"
                value={driverId ?? ""}
                onChange={(event) => setContextDriver(event.target.value.trim() || null)}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => void invoke("show_overlay")}>
              Show overlay
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void invoke("hide_overlay")}>
              Hide overlay
            </Button>
            {/* Temporary — Day 30 Checkpoint 6 manual test trigger, remove
                once a real threat/opportunity has been observed firing this
                for real via useUndercutNotifications. */}
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                void invoke("send_threat_notification", {
                  driver: "TEST",
                  message: "This is a manual test notification from Checkpoint 6.",
                })
              }
            >
              Send test notification
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
