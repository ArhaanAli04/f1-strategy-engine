import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { invoke } from "@tauri-apps/api/core"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { broadcastRaceContext } from "@/hooks/useRaceContextBridge"
import { useRaceContextStore } from "@/stores/raceContextStore"

// Moved here from the global header (Checkpoint 6 feedback) — this is
// dashboard-level session setup, not something every page's chrome needs.
export function RaceContextPanel() {
  const [isOpen, setIsOpen] = useState(true)
  const sessionId = useRaceContextStore((state) => state.sessionId)
  const driverId = useRaceContextStore((state) => state.driverId)

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
          <p className="text-xs text-muted-foreground">
            During a live race, enter the active session ID here. Outside race weekends, enter
            any historical session ID to explore past race data.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="session-id">Session ID</Label>
              <Input
                id="session-id"
                placeholder="Session UUID"
                value={sessionId ?? ""}
                onChange={(event) => broadcastRaceContext(event.target.value || null, driverId)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="driver-id">Your driver ID</Label>
              <Input
                id="driver-id"
                placeholder="Driver UUID"
                value={driverId ?? ""}
                onChange={(event) => broadcastRaceContext(sessionId, event.target.value || null)}
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
