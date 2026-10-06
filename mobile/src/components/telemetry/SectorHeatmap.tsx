import { useQueries } from "@tanstack/react-query"
import { useMemo } from "react"
import { Pressable, Text, View } from "react-native"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSessionStore } from "@/stores/sessionStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { isActiveDriver } from "@/utils/drivers"
import { formatLapTime } from "@/utils/formatters"
import * as haptics from "@/utils/haptics"
import type { DriverResponse, LapCompletedEvent, LapDataResponse } from "@/types"

interface SectorHeatmapProps {
  sessionId: string
  // The live tab's lap-completion events, passed in rather than read here:
  // on mobile every useLiveTelemetry call opens its own WebSocket.
  lapsByDriver: Record<string, LapCompletedEvent>
}

type TimeKey = "lap_time_seconds" | "sector1_seconds" | "sector2_seconds" | "sector3_seconds"

const TIME_COLUMNS: { key: TimeKey; label: string }[] = [
  { key: "lap_time_seconds", label: "LAP" },
  { key: "sector1_seconds", label: "S1" },
  { key: "sector2_seconds", label: "S2" },
  { key: "sector3_seconds", label: "S3" },
]

type SectorClass = "purple" | "green" | "yellow" | "none"

// F1 timing-screen colours, as on web: purple = session best, green = the
// driver's own best, yellow = slower than their best, grey = no time.
const TIME_TEXT_STYLES: Record<SectorClass, string> = {
  purple: "text-purple-400",
  green: "text-emerald-400",
  yellow: "text-yellow-300",
  none: "text-gray-400",
}

const EQUALITY_EPSILON = 1e-6

// classifySector, minOf and formatTimeValue are copied from web's
// SectorHeatmap: pure functions.
function classifySector(
  value: number | null,
  sessionBest: number | null,
  personalBest: number | null,
): SectorClass {
  if (value === null) return "none"
  if (sessionBest !== null && Math.abs(value - sessionBest) < EQUALITY_EPSILON) return "purple"
  if (personalBest !== null && Math.abs(value - personalBest) < EQUALITY_EPSILON) return "green"
  return "yellow"
}

function minOf(values: (number | null)[]): number | null {
  let best: number | null = null
  for (const value of values) {
    if (value === null) continue
    if (best === null || value < best) best = value
  }
  return best
}

function formatTimeValue(key: TimeKey, value: number | null): string {
  if (value === null) return "—"
  return key === "lap_time_seconds" ? formatLapTime(value) : value.toFixed(3)
}

function emptyTimes(): Record<TimeKey, number | null> {
  return { lap_time_seconds: null, sector1_seconds: null, sector2_seconds: null, sector3_seconds: null }
}

// RN port of web/src/components/telemetry/SectorHeatmap.tsx: every driver's
// latest lap time and sectors, in tower order, coloured against the session
// best and their own best. During a live race or replay each driver's laps
// stop at their latest lap event, so the table grows with the race (a driver
// with no event, as in a completed race, shows every lap). Tapping a row
// selects the driver, as on web. Each time is coloured text in a grey pill
// (web's bg-pill-surface, mobile's bg-pill, both #2a2a2a), and the selected
// row is outlined like web's ring.
export function SectorHeatmap({ sessionId, lapsByDriver: liveLapsByDriver }: SectorHeatmapProps) {
  const { data: drivers } = useDrivers()
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)

  // Tower order when gaps are available, else alphabetical by code.
  const orderedDriverIds = useMemo(() => {
    const gaps = gapsResponse?.gaps ?? []
    if (gaps.length > 0) {
      return [...gaps].sort((a, b) => a.position - b.position).map((gap) => gap.driver_id)
    }
    return [...(drivers ?? [])]
      .filter(isActiveDriver)
      .sort((a, b) => a.code.localeCompare(b.code))
      .map((d) => d.id)
  }, [gapsResponse, drivers])

  const positionsByDriver = useMemo(() => {
    const map = new Map<string, number>()
    for (const gap of gapsResponse?.gaps ?? []) map.set(gap.driver_id, gap.position)
    return map
  }, [gapsResponse])

  // Same query key as the tower's and LapTimeChart's, so react-query shares
  // the cache entries instead of fetching again.
  const lapsQueries = useQueries({
    queries: orderedDriverIds.map((driverId) => driverLapsQueryOptions(sessionId, driverId)),
  })

  const driversById = useMemo(() => {
    const map = new Map<string, DriverResponse>()
    for (const driver of drivers ?? []) map.set(driver.id, driver)
    return map
  }, [drivers])

  const lapsByDriver = useMemo(() => {
    const map = new Map<string, LapDataResponse[]>()
    orderedDriverIds.forEach((driverId, index) => {
      const allLaps = lapsQueries[index]?.data?.items ?? []
      const liveEvent = liveLapsByDriver[driverId]
      const filtered =
        liveEvent === undefined
          ? allLaps
          : allLaps.filter((lap) => lap.lap_number <= liveEvent.lap_number)
      map.set(driverId, filtered)
    })
    return map
    // orderedDriverIds is the real change signal; lapsQueries (a new array
    // every render) and liveLapsByDriver are read for their current values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderedDriverIds, lapsQueries, liveLapsByDriver])

  const sessionBests = useMemo(() => {
    const bests = emptyTimes()
    for (const { key } of TIME_COLUMNS) {
      const allValues: (number | null)[] = []
      lapsByDriver.forEach((laps) => laps.forEach((lap) => allValues.push(lap[key])))
      bests[key] = minOf(allValues)
    }
    return bests
  }, [lapsByDriver])

  const personalBests = useMemo(() => {
    const map = new Map<string, Record<TimeKey, number | null>>()
    orderedDriverIds.forEach((driverId) => {
      const laps = lapsByDriver.get(driverId) ?? []
      const perDriver = emptyTimes()
      for (const { key } of TIME_COLUMNS) {
        perDriver[key] = minOf(laps.map((lap) => lap[key]))
      }
      map.set(driverId, perDriver)
    })
    return map
  }, [orderedDriverIds, lapsByDriver])

  const hasAnyData = lapsQueries.some((query) => query.data)
  const isLoading =
    orderedDriverIds.length === 0 || (!hasAnyData && lapsQueries.some((query) => query.isLoading))

  if (isLoading) {
    return <View className="h-64 w-full rounded-md bg-surface" />
  }

  return (
    <View className="gap-0.5">
      <View className="flex-row items-center px-2.5 py-1">
        <Text className="w-20 text-[10px] font-medium text-muted">POS</Text>
        {TIME_COLUMNS.map(({ key, label }) => (
          <Text key={key} className="flex-1 text-center text-[10px] font-medium text-muted">
            {label}
          </Text>
        ))}
      </View>
      {orderedDriverIds.map((driverId, index) => {
        const driver = driversById.get(driverId)
        const laps = lapsByDriver.get(driverId) ?? []
        const latestLap = laps.reduce<LapDataResponse | null>(
          (latest, lap) => (latest === null || lap.lap_number > latest.lap_number ? lap : latest),
          null,
        )
        const personalBest = personalBests.get(driverId)
        const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR
        const isSelected = driverId === selectedDriverId
        // Zebra rows as on web (row-void / row-recede); the selected row gets an
        // outline like web's ring. The border is always there, transparent when
        // not selected, so selecting doesn't shift the row.
        const rowBg = index % 2 === 0 ? "bg-background" : "bg-surface"
        const rowBorder = isSelected ? "border-foreground/70" : "border-transparent"

        return (
          <Pressable
            key={driverId}
            onPress={() => {
              if (!isSelected) haptics.selectionTick()
              setSelectedDriver(driverId)
            }}
            accessibilityRole="button"
            accessibilityState={{ selected: isSelected }}
            className={`flex-row items-center rounded border-2 px-2 py-1.5 ${rowBg} ${rowBorder}`}
          >
            <View className="w-20 flex-row items-center gap-1.5">
              <Text className="w-5 text-right font-mono text-xs text-muted">
                {positionsByDriver.get(driverId) ?? "—"}
              </Text>
              <View className="h-5 w-1 rounded-full" style={{ backgroundColor: teamColor }} />
              <Text className="text-sm font-semibold text-foreground">{driver?.code ?? "???"}</Text>
            </View>
            {TIME_COLUMNS.map(({ key }) => {
              const value = latestLap ? latestLap[key] : null
              const timeClass = classifySector(value, sessionBests[key], personalBest?.[key] ?? null)
              return (
                <View key={key} className="flex-1 items-center px-0.5">
                  {/* The pill is a View: iOS doesn't round a Text's background. */}
                  <View className="rounded-md bg-pill px-1.5 py-1">
                    <Text className={`text-center font-mono text-[11px] ${TIME_TEXT_STYLES[timeClass]}`}>
                      {formatTimeValue(key, value)}
                    </Text>
                  </View>
                </View>
              )
            })}
          </Pressable>
        )
      })}
    </View>
  )
}
