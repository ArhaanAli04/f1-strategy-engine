import { useMemo } from "react"
import { AnimatedDriverDots } from "./AnimatedDriverDots"
import { CircuitOutlineSvg } from "./CircuitOutlineSvg"
import { TelemetryGauge } from "./TelemetryGauge"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { padCountdownValue, useCountdown } from "@/hooks/useCountdown"
import { useDriverCarNumbers, useDriverPositions } from "@/hooks/useDriverPositions"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveDriverTelemetry } from "@/hooks/useLiveDriverTelemetry"
import { useRaceBySession } from "@/hooks/useRaceBySession"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"
import { useSessionStore } from "@/stores/sessionStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

const FALLBACK_VIEWBOX = "0 0 1000 1000"

type Mode = "live" | "historical" | "non-race" | "finished" | "unknown"

interface CircuitMapPanelProps {
  sessionId: string
  // True when this session came from an explicit :sessionId URL param
  // (a deliberate deep link — e.g. a Demo Replay's own session, or any
  // other specific historical session), false when RacePage fell back to
  // resolving one itself (useResolvedSession's "no live race → most recent
  // completed race" fallback). Distinguishes "the user asked to see THIS
  // race" (its own circuit is always correct, live or not) from "nothing
  // else to show, defaulting to something" (where the generic upcoming-race
  // countdown is the more useful thing to display) — see the mode/outline
  // logic below for why these need different circuit sources.
  isExplicitSession: boolean
}

// Day 43 fix: circuit_id/outline/transform come from useRaceBySession
// (sessionId's OWN race) whenever sessionId means something specific —
// live/replay dots (mode "live") or an explicit deep link (mode
// "historical") — not useUpcomingRace, which answers a different question
// ("what's next on the calendar") that has nothing to do with sessionId.
// Confirmed live, two distinct regressions during Day 43 verification: (1)
// replaying British GP while the real upcoming race was Monza rendered
// Monza's outline/transform against Silverstone's real coordinates — fixed
// by sourcing "live" mode from raceBySession; (2) that fix then broke the
// OPPOSITE case — visiting /race/{british-gp-session-id} directly (no live
// data, since no replay is currently running) fell through to "non-race"
// and showed Monza's outline again, this time under a countdown to a race
// nobody asked to see. useUpcomingRace is still used below, but only for
// its own genuinely distinct purpose — the idle "nothing else to show"
// dashboard state ("non-race"/"finished"), which only applies when
// sessionId is itself a fallback, not an explicit ask.
export function CircuitMapPanel({ sessionId, isExplicitSession }: CircuitMapPanelProps) {
  const { data: raceBySession } = useRaceBySession(sessionId)
  const {
    data: upcomingRace,
    isLoading: upcomingLoading,
    isError: upcomingErrored,
  } = useUpcomingRace()
  const { data: positions } = useDriverPositions(sessionId)
  const { data: carNumbers } = useDriverCarNumbers(sessionId)
  const { data: drivers } = useDrivers()
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const { data: liveTelemetry } = useLiveDriverTelemetry(sessionId, selectedDriverId)
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches

  const isLive = Boolean(positions && positions.length > 0)
  const scheduledStart = upcomingRace?.scheduled_start ?? null

  const mode: Mode = isLive
    ? "live"
    : isExplicitSession
      ? "historical"
      : upcomingLoading || upcomingErrored || !scheduledStart
        ? "unknown"
        : new Date(scheduledStart).getTime() > Date.now()
          ? "non-race"
          : "finished"

  const outlineCircuitId =
    mode === "live" || mode === "historical"
      ? (raceBySession?.circuit_id ?? null)
      : (upcomingRace?.circuit_id ?? null)
  const { data: outline } = useCircuitOutline(outlineCircuitId)

  const countdown = useCountdown(mode === "non-race" ? scheduledStart : null)

  const driverByCarNumber = useMemo(() => {
    const driverById = new Map((drivers ?? []).map((driver) => [driver.id, driver]))
    const map = new Map<string, { color: string; driverId: string }>()
    for (const entry of carNumbers ?? []) {
      const driver = driverById.get(entry.driver_id)
      if (!driver) continue
      map.set(entry.car_number, {
        color: driver.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
        driverId: driver.id,
      })
    }
    return map
  }, [carNumbers, drivers])

  const viewBox = outline?.viewbox ?? FALLBACK_VIEWBOX
  const transform = outline?.transform ?? null

  return (
    <div className="relative flex h-[500px] flex-shrink-0 items-center justify-center overflow-hidden border-b bg-muted/30">
      <CircuitOutlineSvg outline={outline} className="h-full w-full" />
      {/* Absolutely overlaid on the outline above, same viewBox/preserveAspectRatio
          so live dots line up pixel-for-pixel with the track line underneath —
          kept as a separate SVG rather than folded into CircuitOutlineSvg since
          live positions/selection are CircuitMapPanel-only state. */}
      <svg
        viewBox={viewBox}
        preserveAspectRatio="xMidYMid meet"
        className="absolute inset-0 h-full w-full"
        aria-hidden="true"
      >
        {mode === "live" && transform && (
          <AnimatedDriverDots
            positions={positions ?? []}
            transform={transform}
            driverByCarNumber={driverByCarNumber}
            selectedDriverId={selectedDriverId}
            prefersReducedMotion={prefersReducedMotion}
          />
        )}
      </svg>

      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
        <div>
          {mode === "live" && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Live Now
              </div>
              <div className="text-2xl font-bold text-foreground">
                {raceBySession?.event_name ?? raceBySession?.circuit?.name ?? "Race"}
              </div>
              {!transform && (
                <div className="mt-1 inline-block rounded bg-background/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur-sm">
                  Track outline unavailable
                </div>
              )}
            </div>
          )}
          {mode === "historical" && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Circuit
              </div>
              <div className="text-2xl font-bold text-foreground">
                {raceBySession?.event_name ?? raceBySession?.circuit?.name ?? "Race"}
              </div>
              {!transform && (
                <div className="mt-1 inline-block rounded bg-background/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur-sm">
                  Track outline unavailable
                </div>
              )}
            </div>
          )}
          {mode === "non-race" && upcomingRace && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Upcoming Race
              </div>
              <div className="text-2xl font-bold text-foreground">
                {upcomingRace.race_name ?? "Next Race"}
              </div>
            </div>
          )}
          {mode === "finished" && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Race Finished
              </div>
              {upcomingRace && (
                <div className="text-lg font-semibold text-foreground">
                  Next: {upcomingRace.race_name ?? "TBD"}
                </div>
              )}
            </div>
          )}
          {mode === "unknown" && (
            <div className="text-sm text-muted-foreground">
              {upcomingLoading ? "Circuit Map — loading…" : "No upcoming race scheduled"}
            </div>
          )}
        </div>

        {mode === "non-race" && countdown && (
          <div className="self-end font-mono text-sm text-muted-foreground">
            Starts in: {countdown.days}d {padCountdownValue(countdown.hours)}h{" "}
            {padCountdownValue(countdown.minutes)}m {padCountdownValue(countdown.seconds)}s
          </div>
        )}
      </div>

      {mode === "live" && (
        <div className="pointer-events-none absolute bottom-4 left-4 rounded bg-black/60 shadow-sm backdrop-blur-sm">
          {!selectedDriverId ? (
            <div className="px-4 py-3 text-xs text-muted-foreground">Select a driver</div>
          ) : liveTelemetry === undefined ? (
            <div className="px-4 py-3 text-xs text-muted-foreground">No live data</div>
          ) : (
            <div className="[&>svg]:h-[170px] [&>svg]:w-[170px]">
              <TelemetryGauge
                speedKmh={liveTelemetry.speed_kmh}
                gear={liveTelemetry.gear}
                throttlePct={liveTelemetry.throttle_pct}
                brake={liveTelemetry.brake}
                drsOpen={liveTelemetry.drs === "open"}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
