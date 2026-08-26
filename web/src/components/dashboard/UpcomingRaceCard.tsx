import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { Card, CardContent } from "@/components/ui/card"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { padCountdownValue, useCountdown } from "@/hooks/useCountdown"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"

// Owns the ticking countdown state itself so the once-a-second re-render
// this ticking causes stays scoped to this small text node — not the whole
// UpcomingRaceCard (Card, CircuitOutlineSvg, race-name block), which don't
// depend on the current second at all.
function CountdownTimer({ targetIso }: { targetIso: string | null }) {
  const countdown = useCountdown(targetIso)
  if (!countdown) return null
  return (
    <div className="self-end font-mono text-sm text-muted-foreground">
      Starts in: {countdown.days}d {padCountdownValue(countdown.hours)}h{" "}
      {padCountdownValue(countdown.minutes)}m {padCountdownValue(countdown.seconds)}s
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
      <Card className="h-64">
        <CardContent className="flex h-full items-center justify-center text-sm text-muted-foreground">
          No upcoming race scheduled.
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="h-64 overflow-hidden">
      <div className="relative flex h-full items-center justify-center bg-muted/30">
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
