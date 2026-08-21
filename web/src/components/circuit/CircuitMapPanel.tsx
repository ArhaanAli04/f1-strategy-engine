import { useMemo } from "react"
import { CircuitOutlineSvg } from "./CircuitOutlineSvg"
import { TelemetryGauge } from "./TelemetryGauge"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { padCountdownValue, useCountdown } from "@/hooks/useCountdown"
import { useDriverCarNumbers, useDriverPositions } from "@/hooks/useDriverPositions"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveDriverTelemetry } from "@/hooks/useLiveDriverTelemetry"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"
import { useSessionStore } from "@/stores/sessionStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { CircuitOutlineTransform } from "@/types"

const FALLBACK_VIEWBOX = "0 0 1000 1000"
const DOT_RADIUS = 12
const SELECTED_DOT_RADIUS = 18
const DOT_STROKE_WIDTH = 1.5
const SELECTED_DOT_STROKE_WIDTH = 3
// transform (not cx/cy) so the browser can composite this on the GPU
// instead of recalculating layout on every one of up to 22 simultaneously
// moving dots. --duration-dot-glide is slightly under useDriverPositions's
// 2s poll interval so a dot finishes easing into place before the next
// update arrives; --ease-in-out-strong reads as a moving object rather
// than the constant-velocity feel of linear.
const DOT_TRANSITION = "transform var(--duration-dot-glide) var(--ease-in-out-strong)"

type Mode = "live" | "non-race" | "finished" | "unknown"

// Mirrors extract_circuit_outlines.py's _build_geometry — applies the same
// X-mirror-correction/rotation/center/scale to a raw live Position.z X/Y
// sample that was applied to the outline's own points, so both land in the
// same viewBox frame. See backend/schemas/circuit_schema.py's
// CircuitOutlineTransform docstring for why the X negation happens first.
function applyTransform(x: number, y: number, transform: CircuitOutlineTransform) {
  const correctedX = -x
  const angle = (transform.rotation_degrees * Math.PI) / 180
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const rotatedX = correctedX * cos - y * sin
  const rotatedY = correctedX * sin + y * cos
  return {
    cx: (rotatedX - transform.center_x) * transform.scale + transform.viewbox_center,
    cy: (rotatedY - transform.center_y) * transform.scale + transform.viewbox_center,
  }
}

interface CircuitMapPanelProps {
  sessionId: string
}

// Circuit_id/race_name/scheduled_start all come from useUpcomingRace rather
// than a per-session lookup: there's no session_id -> circuit_id endpoint,
// and GET /races/upcoming's race_date >= today query keeps it pinned to the
// same race all day (before, during, and immediately after it runs), so it
// doubles correctly as "this session's race" for a currently-relevant
// session. Historical browsing of an old/unrelated session is not yet a
// real navigation path in this app (DashboardPage is still a stub) — revisit
// if that changes.
export function CircuitMapPanel({ sessionId }: CircuitMapPanelProps) {
  const {
    data: upcomingRace,
    isLoading: upcomingLoading,
    isError: upcomingErrored,
  } = useUpcomingRace()
  const { data: outline } = useCircuitOutline(upcomingRace?.circuit_id ?? null)
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
    : upcomingLoading || upcomingErrored || !scheduledStart
      ? "unknown"
      : new Date(scheduledStart).getTime() > Date.now()
        ? "non-race"
        : "finished"

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
        {mode === "live" &&
          transform &&
          (positions ?? []).map((position) => {
            const meta = driverByCarNumber.get(position.driver_number)
            const isSelected = meta !== undefined && meta.driverId === selectedDriverId
            const { cx, cy } = applyTransform(position.x, position.y, transform)
            return (
              <circle
                key={position.driver_number}
                r={isSelected ? SELECTED_DOT_RADIUS : DOT_RADIUS}
                fill={meta?.color ?? FALLBACK_TEAM_COLOR}
                stroke="#fff"
                strokeWidth={isSelected ? SELECTED_DOT_STROKE_WIDTH : DOT_STROKE_WIDTH}
                style={{
                  transform: `translate(${cx}px, ${cy}px)`,
                  transition: prefersReducedMotion ? "none" : DOT_TRANSITION,
                }}
              />
            )
          })}
      </svg>

      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4">
        <div>
          {mode === "live" && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Live Now
              </div>
              <div className="text-2xl font-bold text-foreground">
                {upcomingRace?.race_name ?? "Race"}
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
