import { useEffect, useState } from "react"
import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { Card, CardContent } from "@/components/ui/card"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"

interface Countdown {
  days: number
  hours: number
  minutes: number
  seconds: number
}

// Same countdown shape/logic as CircuitMapPanel's local useCountdown — kept
// as a separate small copy rather than a shared export, per the Day 29 scope
// (only the static outline+corners was to be extracted out of
// CircuitMapPanel, not its countdown/mode logic).
function useCountdown(targetIso: string | null): Countdown | null {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!targetIso) return
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [targetIso])

  if (!targetIso) return null
  const diffMs = new Date(targetIso).getTime() - now
  if (diffMs <= 0) return { days: 0, hours: 0, minutes: 0, seconds: 0 }
  const totalSeconds = Math.floor(diffMs / 1000)
  return {
    days: Math.floor(totalSeconds / 86400),
    hours: Math.floor((totalSeconds % 86400) / 3600),
    minutes: Math.floor((totalSeconds % 3600) / 60),
    seconds: totalSeconds % 60,
  }
}

function pad(value: number): string {
  return value.toString().padStart(2, "0")
}

// Owns the ticking countdown state itself so the once-a-second re-render
// this ticking causes stays scoped to this small text node — not the whole
// UpcomingRaceCard (Card, CircuitOutlineSvg, race-name block), which don't
// depend on the current second at all.
function CountdownTimer({ targetIso }: { targetIso: string | null }) {
  const countdown = useCountdown(targetIso)
  if (!countdown) return null
  return (
    <div className="self-end font-mono text-sm text-muted-foreground">
      Starts in: {countdown.days}d {pad(countdown.hours)}h {pad(countdown.minutes)}m{" "}
      {pad(countdown.seconds)}s
    </div>
  )
}

export function UpcomingRaceCard() {
  const { data: upcomingRace, isLoading, isError } = useUpcomingRace()
  const { data: outline } = useCircuitOutline(upcomingRace?.circuit_id ?? null)

  if (isLoading) {
    return <LoadingSkeleton className="h-64 w-full" />
  }

  if (isError || !upcomingRace) {
    return (
      <Card>
        <CardContent className="flex h-64 items-center justify-center text-sm text-muted-foreground">
          No upcoming race scheduled.
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      <div className="relative flex h-64 items-center justify-center bg-muted/30">
        <CircuitOutlineSvg outline={outline} className="h-full w-full" />
        <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Upcoming Race
            </div>
            <div className="text-2xl font-bold text-foreground">
              {upcomingRace.race_name ?? "Next Race"}
            </div>
          </div>
          <CountdownTimer targetIso={upcomingRace.scheduled_start} />
        </div>
      </div>
    </Card>
  )
}
