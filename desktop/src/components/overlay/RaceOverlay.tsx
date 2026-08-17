import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"
import { invoke } from "@tauri-apps/api/core"
import { X } from "lucide-react"
import * as driverApi from "@/api/driver"
import { useDrivers } from "@/hooks/useDrivers"
import { useNeighborDrivers } from "@/hooks/useNeighborDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useUndercut } from "@/hooks/useStrategy"
import { useRaceContextBridge } from "@/hooks/useRaceContextBridge"
import { useRaceContextStore } from "@/stores/raceContextStore"
import { cn } from "@/lib/utils"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { formatGap, getCompoundColor, getCompoundLabel } from "@/utils/formatters"
import type { DriverGap, DriverResponse } from "@/types"

const TOP_N = 5

interface OverlayRow {
  driverId: string
  position: number
  code: string
  teamColor: string
  gapLabel: string
  compound: string | null
}

function computeGapLabels(gaps: DriverGap[]): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const gap of gaps) {
    labels[gap.driver_id] = gap.position === 1 ? "Leader" : formatGap(gap.gap_to_ahead_seconds)
  }
  return labels
}

export function RaceOverlay() {
  useRaceContextBridge()
  const sessionId = useRaceContextStore((state) => state.sessionId)
  const driverId = useRaceContextStore((state) => state.driverId)

  const { data: gapsResponse, isLoading } = useSessionGaps(sessionId)
  const { data: drivers } = useDrivers()

  const gaps = useMemo(() => gapsResponse?.gaps ?? [], [gapsResponse])
  const topGaps = useMemo(
    () => [...gaps].sort((a, b) => a.position - b.position).slice(0, TOP_N),
    [gaps],
  )
  const gapLabels = useMemo(() => computeGapLabels(gaps), [gaps])

  const driversById = useMemo(() => {
    const map = new Map<string, DriverResponse>()
    for (const driver of drivers ?? []) map.set(driver.id, driver)
    return map
  }, [drivers])

  // REST-only, top-5 latest lap (for compound) — a compact overlay window
  // has no room for a live telemetry feed, one poll every few seconds is
  // enough to keep the tyre icons current.
  const lapsQueries = useQueries({
    queries: topGaps.map((gap) => ({
      queryKey: ["driver", "laps", "latest", sessionId, gap.driver_id],
      queryFn: () => driverApi.getDriverLaps(gap.driver_id, sessionId as string, { page_size: 100 }),
      enabled: Boolean(sessionId),
      refetchInterval: 10_000,
    })),
  })

  const rows: OverlayRow[] = useMemo(() => {
    return topGaps.map((gap, index) => {
      const driver = driversById.get(gap.driver_id)
      const items = lapsQueries[index]?.data?.items ?? []
      const latest = items.length > 0 ? items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b)) : null
      return {
        driverId: gap.driver_id,
        position: gap.position,
        code: driver?.code ?? "???",
        teamColor: driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
        gapLabel: gapLabels[gap.driver_id] ?? "—",
        compound: latest?.compound ?? null,
      }
    })
    // lapsQueries is a fresh array each render (useQueries) — topGaps is the
    // real change signal, lapsQueries is read for its current .data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topGaps, driversById, gapLabels, lapsQueries])

  const { aheadId } = useNeighborDrivers(sessionId, driverId)
  const opportunity = useUndercut(sessionId, driverId, aheadId)
  const selfDriver = driverId ? driversById.get(driverId) : undefined
  const aheadDriver = aheadId ? driversById.get(aheadId) : undefined

  return (
    <div
      data-tauri-drag-region
      className="flex h-screen w-screen flex-col overflow-hidden rounded-lg border border-border bg-background/85 text-foreground backdrop-blur-sm"
    >
      <div data-tauri-drag-region className="flex items-center justify-between border-b border-border px-2 py-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          F1 Strategy Engine
        </span>
        <button
          type="button"
          aria-label="Close overlay"
          onClick={() => void invoke("hide_overlay")}
          className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-1.5 py-1">
        {isLoading && rows.length === 0 && (
          <p className="px-1 py-2 text-[11px] text-muted-foreground">Waiting for session data…</p>
        )}
        {rows.map((row) => (
          <div
            key={row.driverId}
            className={cn(
              "flex items-center justify-between gap-1.5 py-0.5 text-[11px]",
              row.driverId === driverId && "rounded bg-accent px-1",
            )}
          >
            <span className="w-4 text-center font-mono text-muted-foreground">{row.position}</span>
            <span className="h-3.5 w-1 flex-shrink-0 rounded-full" style={{ backgroundColor: row.teamColor }} />
            <span className="w-9 font-semibold">{row.code}</span>
            <span className="flex-1 text-right font-mono tabular-nums text-muted-foreground">{row.gapLabel}</span>
            <span
              className="w-4 flex-shrink-0 text-center text-[9px] font-bold"
              style={{ color: row.compound ? getCompoundColor(row.compound) : undefined }}
            >
              {row.compound ? getCompoundLabel(row.compound) : "?"}
            </span>
          </div>
        ))}
      </div>

      {selfDriver && aheadDriver && opportunity.data && (
        <div className="border-t border-border px-2 py-1 text-[10px]">
          <span className="font-semibold">{selfDriver.code}</span>{" "}
          <span className="text-muted-foreground">
            vs {aheadDriver.code}: {Math.round(opportunity.data.probability_pit_now_gains_position * 100)}%
            undercut
          </span>
        </div>
      )}
    </div>
  )
}
