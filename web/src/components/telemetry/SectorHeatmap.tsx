import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { cn } from "@/lib/utils"
import { useSessionStore } from "@/stores/sessionStore"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { isActiveDriver } from "@/utils/drivers"
import { formatLapTime } from "@/utils/formatters"
import type { DriverResponse, LapDataResponse } from "@/types"

interface SectorHeatmapProps {
  sessionId: string
}

// lap_time_seconds is included alongside the 3 sectors so the Lap Time
// column gets the same purple/green/yellow session-best/personal-best
// classification as S1/S2/S3, not just a plain readout.
type TimeKey = "lap_time_seconds" | "sector1_seconds" | "sector2_seconds" | "sector3_seconds"

const TIME_COLUMNS: { key: TimeKey; label: string }[] = [
  { key: "lap_time_seconds", label: "LAP TIME" },
  { key: "sector1_seconds", label: "S1" },
  { key: "sector2_seconds", label: "S2" },
  { key: "sector3_seconds", label: "S3" },
]

type SectorClass = "purple" | "green" | "yellow" | "none"

// Real F1 timing-screen convention: purple = absolute session-best, green =
// this driver's own best (but not session-best), yellow = slower than their
// own best, grey/white = no time set. Text color inside a fixed grey pill
// (bg-pill-surface below) rather than a colored cell background.
const TIME_TEXT_STYLES: Record<SectorClass, string> = {
  purple: "text-purple-400",
  green: "text-emerald-400",
  yellow: "text-yellow-300",
  none: "text-gray-400",
}

const EQUALITY_EPSILON = 1e-6

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

export function SectorHeatmap({ sessionId }: SectorHeatmapProps) {
  const { data: drivers } = useDrivers()
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)

  // Position order when gaps are available (matches the timing tower);
  // falls back to alphabetical-by-code before the first gaps response lands.
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

  // Same query key as useDriverLaps/LapTimeChart — react-query dedupes and
  // shares this cache entry rather than double-fetching.
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
      map.set(driverId, lapsQueries[index]?.data?.items ?? [])
    })
    return map
    // orderedDriverIds is the real change signal; lapsQueries is read for
    // its current .data on every render regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderedDriverIds, lapsQueries])

  const sessionBests = useMemo(() => {
    const bests: Record<TimeKey, number | null> = {
      lap_time_seconds: null,
      sector1_seconds: null,
      sector2_seconds: null,
      sector3_seconds: null,
    }
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
      const perDriver: Record<TimeKey, number | null> = {
        lap_time_seconds: null,
        sector1_seconds: null,
        sector2_seconds: null,
        sector3_seconds: null,
      }
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
    return (
      <div className="flex flex-col gap-1">
        {Array.from({ length: 22 }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  const gridColumns = "grid-cols-[7rem_1fr_1fr_1fr_1fr]"

  return (
    <div className="flex flex-col gap-0.5">
      <div className={cn("grid gap-1 px-2 py-1 text-xs font-medium text-muted-foreground", gridColumns)}>
        <span>POSITION</span>
        {TIME_COLUMNS.map(({ key, label }) => (
          <span key={key} className="text-center">
            {label}
          </span>
        ))}
      </div>
      {orderedDriverIds.map((driverId, index) => {
        const driver = driversById.get(driverId)
        const laps = lapsByDriver.get(driverId) ?? []
        const latestLap = laps.reduce<LapDataResponse | null>(
          (latest, lap) => (latest === null || lap.lap_number > latest.lap_number ? lap : latest),
          null,
        )
        const personalBest = personalBests.get(driverId)
        const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR
        const position = positionsByDriver.get(driverId)
        const isSelected = driverId === selectedDriverId
        // Zebra striping: 1st/3rd/5th... rows (even index) are deep black,
        // 2nd/4th/6th... (odd index) are slightly lighter dark grey.
        const rowBg = index % 2 === 0 ? "bg-row-void" : "bg-row-recede"

        return (
          <button
            key={driverId}
            type="button"
            onClick={() => setSelectedDriver(driverId)}
            className={cn(
              "grid items-center gap-1 rounded px-2 py-1.5 text-left",
              gridColumns,
              rowBg,
              isSelected && "ring-2 ring-ring",
            )}
          >
            <span className="flex items-center gap-1.5">
              <span className="w-5 text-right font-mono text-xs text-muted-foreground">
                {position ?? "—"}
              </span>
              <span
                className="h-6 w-1 flex-shrink-0 rounded-full"
                style={{ backgroundColor: teamColor }}
              />
              <span className="text-sm font-semibold text-white">{driver?.code ?? "???"}</span>
            </span>
            {TIME_COLUMNS.map(({ key }) => {
              const value = latestLap ? latestLap[key] : null
              const timeClass = classifySector(value, sessionBests[key], personalBest?.[key] ?? null)
              return (
                <span key={key} className="flex justify-center">
                  <span
                    className={cn(
                      "rounded-md bg-pill-surface px-2 py-1 text-center font-mono text-xs tabular-nums",
                      TIME_TEXT_STYLES[timeClass],
                    )}
                  >
                    {formatTimeValue(key, value)}
                  </span>
                </span>
              )
            })}
          </button>
        )
      })}
    </div>
  )
}
