import { Link } from "react-router-dom"
import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { Button } from "@/components/ui/button"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { padCountdownValue, useCountdown } from "@/hooks/useCountdown"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"
import { useIsAuthenticated } from "@/stores/authStore"
import { ROUTES } from "@/utils/constants"

// The landing page's one genuinely live element — GET /races/upcoming and
// GET /circuits/{id}/outline are both public (no get_current_user
// dependency, confirmed against backend/apis/v1/{races,circuits}.py), so
// this renders real data for a signed-out visitor. Deliberately not
// CircuitMapPanel: that component requires a sessionId and drives live
// driver dots + the telemetry gauge from session-scoped, auth-gated
// endpoints — neither is available here.
//
// Real 50/50 flex split, not an overlay: z-index over a full-bleed SVG
// could not stop the circuit's own line art from visually crossing the text
// (stacking order isn't a collision fix). The circuit is confined to its
// own right-hand box (overflow-hidden, fixed width/height) and can never
// extend into the text column. Stacks to text-above/circuit-below on
// narrow screens.
export function LandingCircuitHero() {
  const isAuthenticated = useIsAuthenticated()
  const { data: upcomingRace, isLoading, isError } = useUpcomingRace()
  const { data: outline } = useCircuitOutline(upcomingRace?.circuit_id ?? null)
  const countdown = useCountdown(upcomingRace?.scheduled_start ?? null)

  const hasRace = !isLoading && !isError && upcomingRace

  return (
    <div className="flex w-full flex-col border-b bg-muted/30 md:min-h-[480px] md:flex-row">
      <div className="relative z-10 flex w-full flex-col justify-center gap-6 p-6 sm:p-10 md:w-1/2">
        <div>
          {/* Product identity is the headline — which race is next is
              context, shown in the right column instead (a recruiter/
              visitor should understand what the product does first). */}
          <h1 className="text-3xl font-bold text-foreground sm:text-5xl">
            Real-time F1 race strategy, powered by ML
          </h1>
          <p className="mt-3 max-w-xl text-sm text-muted-foreground sm:text-base">
            Live telemetry, ML-predicted pit windows, undercut/overcut probabilities, and
            Monte Carlo race simulation — the same signal a pit wall engineer reads mid-session.
          </p>
        </div>

        <div className="flex gap-2">
          {isAuthenticated ? (
            <Button asChild size="lg">
              <Link to={ROUTES.DASHBOARD}>Go to Dashboard</Link>
            </Button>
          ) : (
            <>
              <Button asChild size="lg">
                <Link to={ROUTES.REGISTER}>Create Account</Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link to={ROUTES.LOGIN}>Sign In</Link>
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex w-full flex-col gap-2 p-6 sm:p-10 md:w-1/2">
        {hasRace && (
          <div className="inline-flex w-fit items-center gap-1.5 rounded-full border bg-background/60 px-3 py-1 text-xs text-muted-foreground">
            <span className="font-semibold uppercase tracking-wide">Next Race</span>
            <span>·</span>
            <span>{upcomingRace.race_name}</span>
            <span>·</span>
            <span>Round {upcomingRace.round_number}</span>
          </div>
        )}

        <div className="relative h-64 w-full flex-1 overflow-hidden md:h-auto">
          {isLoading ? (
            <LoadingSkeleton className="h-full w-full" />
          ) : (
            <CircuitOutlineSvg outline={outline} className="h-full w-full" />
          )}
        </div>

        {hasRace && countdown && (
          <div className="self-end rounded-md bg-background/80 px-3 py-1.5 font-mono text-sm tabular-nums text-muted-foreground">
            Starts in: {countdown.days}d {padCountdownValue(countdown.hours)}h{" "}
            {padCountdownValue(countdown.minutes)}m {padCountdownValue(countdown.seconds)}s
          </div>
        )}
      </div>
    </div>
  )
}
