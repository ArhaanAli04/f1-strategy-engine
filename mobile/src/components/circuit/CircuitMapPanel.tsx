import { useMemo } from "react"
import { Text, View } from "react-native"
import Svg from "react-native-svg"
import { AnimatedDriverDot } from "@/components/circuit/AnimatedDriverDot"
import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { TelemetryGauge } from "@/components/circuit/TelemetryGauge"
import { padCountdownUnit as pad, useCountdown } from "@/hooks/useCountdown"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { useDriverCarNumbers, useDriverPositions } from "@/hooks/useDriverPositions"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveDriverTelemetry } from "@/hooks/useLiveDriverTelemetry"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"
import { useSessionStore } from "@/stores/sessionStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { CircuitOutlineTransform } from "@/types"

const FALLBACK_VIEWBOX = "0 0 1000 1000"

type Mode = "live" | "non-race" | "finished" | "unknown"

// Mirrors extract_circuit_outlines.py's _build_geometry — applies the same
// X-mirror-correction/rotation/center/scale to a raw live Position.z X/Y
// sample that was applied to the outline's own points, so both land in the
// same viewBox frame. Identical to web's CircuitMapPanel.tsx.
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

// RN port of web/src/components/circuit/CircuitMapPanel.tsx onto the top of
// the Live tab (see mobile/src/README.md's Checkpoint 6 placement note —
// web's own DashboardPage only gets the static outline via UpcomingRaceCard,
// the full live panel lives on web's RacePage instead). Same three modes,
// same circuit outline, same turn markers, same countdown, same live driver
// dots and telemetry gauge as web — live dot glide now via
// AnimatedDriverDot (Reanimated) instead of a CSS transform transition,
// since react-native-svg has no CSS transitions to lean on.
export function CircuitMapPanel({ sessionId }: CircuitMapPanelProps) {
  const { data: upcomingRace, isLoading: upcomingLoading, isError: upcomingErrored } = useUpcomingRace()
  const { data: outline } = useCircuitOutline(upcomingRace?.circuit_id ?? null)
  const { data: positions } = useDriverPositions(sessionId)
  const { data: carNumbers } = useDriverCarNumbers(sessionId)
  const { data: drivers } = useDrivers()
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const { data: liveTelemetry } = useLiveDriverTelemetry(sessionId, selectedDriverId)

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
    <View className="relative h-[300px] w-full items-center justify-center overflow-hidden border-b border-white/10 bg-surface/30">
      <View className="absolute inset-0">
        <CircuitOutlineSvg outline={outline} />
      </View>

      {/* Absolutely overlaid on the outline above, same viewBox so live dots
          line up pixel-for-pixel with the track line underneath. */}
      <View className="absolute inset-0" pointerEvents="none">
        <Svg width="100%" height="100%" viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
          {mode === "live" &&
            transform &&
            (positions ?? []).map((position) => {
              const meta = driverByCarNumber.get(position.driver_number)
              const isSelected = meta !== undefined && meta.driverId === selectedDriverId
              const { cx, cy } = applyTransform(position.x, position.y, transform)
              return (
                <AnimatedDriverDot
                  key={position.driver_number}
                  cx={cx}
                  cy={cy}
                  color={meta?.color ?? FALLBACK_TEAM_COLOR}
                  isSelected={isSelected}
                />
              )
            })}
        </Svg>
      </View>

      <View className="absolute inset-0 justify-between p-4" pointerEvents="none">
        <View>
          {mode === "live" && (
            <View>
              <Text className="text-xs font-semibold uppercase tracking-wide text-muted">Live Now</Text>
              <Text className="text-xl font-bold text-foreground">{upcomingRace?.race_name ?? "Race"}</Text>
              {!transform && (
                <View className="mt-1 self-start rounded bg-background/80 px-3 py-1.5">
                  <Text className="text-xs text-muted">Track outline unavailable</Text>
                </View>
              )}
            </View>
          )}
          {mode === "non-race" && upcomingRace && (
            <View>
              <Text className="text-xs font-semibold uppercase tracking-wide text-muted">Upcoming Race</Text>
              <Text className="text-xl font-bold text-foreground">{upcomingRace.race_name ?? "Next Race"}</Text>
            </View>
          )}
          {mode === "finished" && (
            <View>
              <Text className="text-xs font-semibold uppercase tracking-wide text-muted">Race Finished</Text>
              {upcomingRace && (
                <Text className="text-base font-semibold text-foreground">
                  Next: {upcomingRace.race_name ?? "TBD"}
                </Text>
              )}
            </View>
          )}
          {mode === "unknown" && (
            <Text className="text-sm text-muted">
              {upcomingLoading ? "Circuit Map — loading…" : "No upcoming race scheduled"}
            </Text>
          )}
        </View>

        {mode === "non-race" && countdown && (
          <Text className="self-end font-mono text-sm text-muted">
            Starts in: {countdown.days}d {pad(countdown.hours)}h {pad(countdown.minutes)}m{" "}
            {pad(countdown.seconds)}s
          </Text>
        )}
      </View>

      {mode === "live" && (
        <View className="absolute bottom-4 left-4 rounded bg-black/60" pointerEvents="none">
          {!selectedDriverId ? (
            <Text className="px-4 py-3 text-xs text-muted">Select a driver</Text>
          ) : liveTelemetry === undefined ? (
            <Text className="px-4 py-3 text-xs text-muted">No live data</Text>
          ) : (
            <View className="h-[170px] w-[170px]">
              <TelemetryGauge
                speedKmh={liveTelemetry.speed_kmh}
                gear={liveTelemetry.gear}
                throttlePct={liveTelemetry.throttle_pct}
                brake={liveTelemetry.brake}
                drsOpen={liveTelemetry.drs === "open"}
              />
            </View>
          )}
        </View>
      )}
    </View>
  )
}
