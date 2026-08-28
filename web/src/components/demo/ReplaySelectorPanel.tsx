import { useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  useCuratedSessions,
  useReplayAvailable,
  useReplayStatus,
  useStartReplay,
  useStopReplay,
} from "@/hooks/useDemoReplay"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import type { CuratedSession } from "@/types"
import { ROUTES } from "@/utils/constants"
import { getApiErrorMessage } from "@/utils/errors"

interface ReplaySelectorPanelProps {
  // The session currently shown on the Race page — used to read the live lap
  // for the "Currently replaying" indicator while a replay runs.
  sessionId: string | null
  // useResolvedSession().isLive — a genuine live race, not a demo replay.
  isLive: boolean
}

export function ReplaySelectorPanel({ sessionId, isLive }: ReplaySelectorPanelProps) {
  const navigate = useNavigate()
  const { data: available } = useReplayAvailable()
  const { data: status } = useReplayStatus()
  const { data: curated } = useCuratedSessions()
  const startReplay = useStartReplay()
  const stopReplay = useStopReplay()

  const running = status?.running ?? false
  // Current lap is derived from the gaps the replay itself publishes
  // (Checkpoints A–F) — the furthest-along driver's lap_number is the race's
  // current lap. Part 5 is control/UI only; no backend progress field exists.
  const { data: gaps } = useSessionGaps(running ? sessionId : null)
  const currentLap = useMemo(() => {
    if (!gaps || gaps.gaps.length === 0) return null
    return Math.max(...gaps.gaps.map((entry) => entry.lap_number))
  }, [gaps])

  // Hidden during a real live race, or whenever the backend reports the
  // feature unavailable (also a server-detected live race).
  if (isLive || available?.available !== true) return null

  const handleStart = (session: CuratedSession) => {
    startReplay.mutate(session.session_id, {
      onSuccess: (result) => {
        toast.success(`Replaying ${result.race_name}`)
        navigate(ROUTES.race(result.session_id))
      },
      onError: (error) => toast.error(getApiErrorMessage(error, "Failed to start replay")),
    })
  }

  const handleStop = () => {
    stopReplay.mutate(undefined, {
      onSuccess: () => toast.success("Replay stopped"),
      onError: (error) => toast.error(getApiErrorMessage(error, "Failed to stop replay")),
    })
  }

  return (
    <Card className="m-4">
      <CardHeader>
        <CardTitle>Watch a Replay</CardTitle>
      </CardHeader>
      <CardContent>
        {running ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm">
              <span className="text-muted-foreground">Currently replaying: </span>
              <span className="font-medium">{status?.race_name}</span>
              {typeof status?.end_lap === "number" && (
                <span className="text-muted-foreground">
                  {` — Lap ${currentLap ?? "…"}/${status.end_lap}`}
                </span>
              )}
            </p>
            <Button
              variant="destructive"
              size="sm"
              className="shrink-0"
              onClick={handleStop}
              disabled={stopReplay.isPending}
            >
              Stop Replay
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(curated?.sessions ?? []).map((session) => (
              <div
                key={session.session_id}
                className="flex flex-col gap-2 rounded-md border p-3"
              >
                <div className="text-sm font-semibold">{session.race_name}</div>
                <div className="text-xs text-muted-foreground">
                  Laps {session.start_lap}–{session.end_lap} · ~
                  {session.estimated_duration_minutes} min
                </div>
                <p className="flex-1 text-xs text-muted-foreground">{session.description}</p>
                <Button
                  size="sm"
                  onClick={() => handleStart(session)}
                  disabled={startReplay.isPending}
                >
                  Start Replay
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
