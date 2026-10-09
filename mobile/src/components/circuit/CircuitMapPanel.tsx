import { useMemo, useState } from "react"
import { Text, View } from "react-native"
import { useSharedValue } from "react-native-reanimated"
import Svg from "react-native-svg"
import { AnimatedDriverDot } from "@/components/circuit/AnimatedDriverDot"
import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { SelectedDriverLabel } from "@/components/circuit/SelectedDriverLabel"
import { TelemetryGauge } from "@/components/circuit/TelemetryGauge"
import { padCountdownUnit as pad, useCountdown } from "@/hooks/useCountdown"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
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
  // True when this session was asked for specifically (a running Demo
  // Replay's), false when it is the automatic fallback (the most recent
  // completed race). Web's prop: its race page passes an explicit
  // :sessionId; mobile passes useRaceSession's isReplay.
  isExplicitSession: boolean
}

// RN port of web/src/components/circuit/CircuitMapPanel.tsx onto the top of
// the Live tab (see mobile/src/README.md's Checkpoint 6 placement note —
// web's own DashboardPage only gets the static outline via UpcomingRaceCard,
// the full live panel lives on web's RacePage instead). Same three modes,
// same circuit outline, same turn markers, same countdown, same live driver
// dots and telemetry gauge as web — live dot glide now via
// AnimatedDriverDot (Reanimated) instead of a CSS transform transition,
// since react-native-svg has no CSS transitions to lean on.
// Track and race name follow web's Day 43 fix (re-synced 2026-10-09, Day
// 6b-mobile CP2): with dots on the map ("live") or a session asked for
// specifically ("historical"), they come from the session's own race
// (useRaceBySession). Before, mobile always used the upcoming race, so a
// replay of one race drew the next race's track under its dots. The
// upcoming race is still what the idle states show (countdown, finished).
export function CircuitMapPanel({ sessionId, isExplicitSession }: CircuitMapPanelProps) {
  const { data: raceBySession } = useRaceBySession(sessionId)
  const { data: upcomingRace, isLoading: upcomingLoading, isError: upcomingErrored } = useUpcomingRace()
  const { data: positions } = useDriverPositions(sessionId)
  const { data: carNumbers } = useDriverCarNumbers(sessionId)
  const { data: drivers } = useDrivers()
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const { data: liveTelemetry } = useLiveDriverTelemetry(sessionId, selectedDriverId)
  // The selected driver's code label follows these (viewBox units), which the
  // selected AnimatedDriverDot updates every frame; the map's size turns them
  // into pixels (2026-10-09).
  const labelX = useSharedValue(0)
  const labelY = useSharedValue(0)
  const [mapSize, setMapSize] = useState({ width: 0, height: 0 })

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
  const sessionRaceName = raceBySession?.event_name ?? raceBySession?.circuit?.name ?? "Race"

  const countdown = useCountdown(mode === "non-race" ? scheduledStart : null)

  const driverByCarNumber = useMemo(() => {
    const driverById = new Map((drivers ?? []).map((driver) => [driver.id, driver]))
    const map = new Map<string, { color: string; driverId: string; code: string }>()
    for (const entry of carNumbers ?? []) {
      const driver = driverById.get(entry.driver_id)
      if (!driver) continue
      map.set(entry.car_number, {
        color: driver.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
        driverId: driver.id,
        code: driver.code,
      })
    }
    return map
  }, [carNumbers, drivers])

  const viewBox = outline?.viewbox ?? FALLBACK_VIEWBOX
  const transform = outline?.transform ?? null

  // SVG has no z-index: later siblings paint on top, so the selected driver's
  // dot goes last (a stable sort keeps the others in order). The label shows
  // only when that driver is actually on the map.
  const selectedNumber =
    (positions ?? []).find(
      (position) => driverByCarNumber.get(position.driver_number)?.driverId === selectedDriverId,
    )?.driver_number ?? null
  const orderedPositions = [...(positions ?? [])].sort(
    (a, b) => Number(a.driver_number === selectedNumber) - Number(b.driver_number === selectedNumber),
  )
  const selectedMeta = selectedNumber !== null ? driverByCarNumber.get(selectedNumber) : undefined

  return (
    <View className="relative h-[300px] w-full items-center justify-center overflow-hidden border-b border-white/10 bg-surface/30">
      <View className="absolute inset-0">
        <CircuitOutlineSvg outline={outline} />
      </View>

      {/* Absolutely overlaid on the outline above, same viewBox so live dots
          line up pixel-for-pixel with the track line underneath. */}
      <View
        className="absolute inset-0"
        pointerEvents="none"
        onLayout={(event) => {
          const { width, height } = event.nativeEvent.layout
          setMapSize({ width, height })
        }}
      >
        <Svg width="100%" height="100%" viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
          {mode === "live" &&
            transform &&
            orderedPositions.map((position) => {
              const meta = driverByCarNumber.get(position.driver_number)
              return (
                <AnimatedDriverDot
                  key={position.driver_number}
                  x={position.x}
                  y={position.y}
                  transform={transform}
                  color={meta?.color ?? FALLBACK_TEAM_COLOR}
                  isSelected={position.driver_number === selectedNumber}
                  labelX={labelX}
                  labelY={labelY}
                />
              )
            })}
        </Svg>
        {mode === "live" && transform && selectedMeta && mapSize.width > 0 && (
          <SelectedDriverLabel
            code={selectedMeta.code}
            color={selectedMeta.color}
            x={labelX}
            y={labelY}
            mapWidth={mapSize.width}
            mapHeight={mapSize.height}
          />
        )}
      </View>

      <View className="absolute inset-0 justify-between p-4" pointerEvents="none">
        <View>
          {mode === "live" && (
            <View>
              <Text className="text-xs font-semibold uppercase tracking-wide text-muted">Live Now</Text>
              <Text className="text-xl font-bold text-foreground">{sessionRaceName}</Text>
              {!transform && (
                <View className="mt-1 self-start rounded bg-background/80 px-3 py-1.5">
                  <Text className="text-xs text-muted">Track outline unavailable</Text>
                </View>
              )}
            </View>
          )}
          {mode === "historical" && (
            <View>
              <Text className="text-xs font-semibold uppercase tracking-wide text-muted">Circuit</Text>
              <Text className="text-xl font-bold text-foreground">{sessionRaceName}</Text>
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
          <Text className="self-end font-mono text-sm text-foreground">
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
