import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { cn } from "@/lib/utils"
import { useSessionStore } from "@/stores/sessionStore"
import type { DriverResponse, LapDataResponse } from "@/types"

interface SectorHeatmapProps {
  sessionId: string
}

type SectorKey = "sector1_seconds" | "sector2_seconds" | "sector3_seconds"

const SECTORS: { key: SectorKey; label: string }[] = [
  { key: "sector1_seconds", label: "S1" },
  { key: "sector2_seconds", label: "S2" },
  { key: "sector3_seconds", label: "S3" },
]

type SectorClass = "purple" | "green" | "yellow" | "none"

// Matches real F1 timing screens: purple = absolute session-best for that
// sector, green = this driver's own best (but not session-best), yellow =
// slower than their own best, grey = no time set.
const SECTOR_CLASS_STYLES: Record<SectorClass, string> = {
  purple: "bg-purple-500 text-white",
  green: "bg-emerald-500 text-white",
  yellow: "bg-yellow-400 text-black",
  none: "bg-muted text-muted-foreground",
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
    return [...(drivers ?? [])].sort((a, b) => a.code.localeCompare(b.code)).map((d) => d.id)
  }, [gapsResponse, drivers])

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
    const bests: Record<SectorKey, number | null> = {
      sector1_seconds: null,
      sector2_seconds: null,
      sector3_seconds: null,
    }
    for (const { key } of SECTORS) {
      const allValues: (number | null)[] = []
      lapsByDriver.forEach((laps) => laps.forEach((lap) => allValues.push(lap[key])))
      bests[key] = minOf(allValues)
    }
    return bests
  }, [lapsByDriver])

  const personalBests = useMemo(() => {
    const map = new Map<string, Record<SectorKey, number | null>>()
    orderedDriverIds.forEach((driverId) => {
      const laps = lapsByDriver.get(driverId) ?? []
      const perDriver: Record<SectorKey, number | null> = {
        sector1_seconds: null,
        sector2_seconds: null,
        sector3_seconds: null,
      }
      for (const { key } of SECTORS) {
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

  return (
    <div className="flex flex-col gap-1">
      <div className="grid grid-cols-[3rem_1fr_1fr_1fr] gap-1 px-1 text-xs font-medium text-muted-foreground">
        <span>Driver</span>
        {SECTORS.map((sector) => (
          <span key={sector.key} className="text-center">
            {sector.label}
          </span>
        ))}
      </div>
      {orderedDriverIds.map((driverId) => {
        const driver = driversById.get(driverId)
        const laps = lapsByDriver.get(driverId) ?? []
        const latestLap = laps.reduce<LapDataResponse | null>(
          (latest, lap) => (latest === null || lap.lap_number > latest.lap_number ? lap : latest),
          null,
        )
        const personalBest = personalBests.get(driverId)

        return (
          <button
            key={driverId}
            type="button"
            onClick={() => setSelectedDriver(driverId)}
            className={cn(
              "grid grid-cols-[3rem_1fr_1fr_1fr] items-center gap-1 rounded px-1 py-1 text-left",
              driverId === selectedDriverId ? "ring-2 ring-ring" : "",
            )}
          >
            <span className="text-sm font-semibold">{driver?.code ?? "???"}</span>
            {SECTORS.map(({ key }) => {
              const value = latestLap ? latestLap[key] : null
              const sectorClass = classifySector(
                value,
                sessionBests[key],
                personalBest?.[key] ?? null,
              )
              return (
                <span
                  key={key}
                  className={cn(
                    "rounded py-1 text-center font-mono text-xs tabular-nums",
                    SECTOR_CLASS_STYLES[sectorClass],
                  )}
                >
                  {value === null ? "—" : value.toFixed(3)}
                </span>
              )
            })}
          </button>
        )
      })}
    </div>
  )
}
