import { useLayoutEffect, useMemo, useRef } from "react"
import { useQueries } from "@tanstack/react-query"
import { Info } from "lucide-react"
import { driverLapsQueryOptions } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { cn } from "@/lib/utils"
import { useSessionStore } from "@/stores/sessionStore"
import { COMPOUND_COLORS, FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { formatLapTime, getCompoundColor, getCompoundLabel } from "@/utils/formatters"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import type { DriverGap, DriverResponse, LapDataResponse } from "@/types"

const TYRE_ICON_SIZE = 24
const TYRE_ICON_BG = "#1a1a1a"

interface TyreIconProps {
  compound: string | null
}

// F1-style tyre icon: dark disc, bold compound letter, and a partial
// circular border split into two arcs (left/right) with small gaps at 12
// and 6 o'clock — not a full ring. Letter and arcs share the compound color.
function TyreIcon({ compound }: TyreIconProps) {
  const color = compound ? getCompoundColor(compound) : COMPOUND_COLORS.UNKNOWN
  const label = compound ? getCompoundLabel(compound) : "?"

  return (
    <svg
      width={TYRE_ICON_SIZE}
      height={TYRE_ICON_SIZE}
      viewBox="0 0 24 24"
      className="flex-shrink-0"
      aria-label={compound ?? "Unknown compound"}
    >
      <circle cx="12" cy="12" r="11" fill={TYRE_ICON_BG} />
      {/* Right arc: theta 12°→168° (measured clockwise from 12 o'clock) */}
      <path
        d="M 13.87 3.2 A 9 9 0 0 1 13.87 20.8"
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      {/* Left arc: theta 192°→348°, mirrors the right arc */}
      <path
        d="M 10.13 20.8 A 9 9 0 0 1 10.13 3.2"
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <text
        x="12"
        y="12.5"
        textAnchor="middle"
        dominantBaseline="middle"
        fontSize="10"
        fontWeight="bold"
        fill={color}
      >
        {label}
      </text>
    </svg>
  )
}

interface LiveTimingTowerProps {
  sessionId: string
}

interface TimingRow {
  driverId: string
  position: number
  code: string
  teamColor: string
  lastLapSeconds: number | null
  gapLabel: string
  compound: string | null
}


// formatGap (utils/formatters.ts) is flat-seconds ("+2.345s") — right for
// small sub-lap deltas elsewhere (undercut/overcut), but a cumulative gap to
// the leader can exceed a minute (e.g. a lapped car), so this uses
// formatLapTime's mm:ss.sss rollover instead: "+23.456" / "+1:23.456".
function formatGapToLeader(seconds: number): string {
  return `+${formatLapTime(seconds)}`
}

function formatLapsBehind(laps: number): string {
  return `+${laps} LAP${laps > 1 ? "S" : ""}`
}

// Position 1 shows "Leader" rather than a gap. Once a driver is a lap down
// (gap_to_ahead_seconds is null AND laps_behind > 0 — a real lap-number
// boundary, not just a missing time), the seconds-gap chain no longer means
// anything: a lapped car's cumulative race time isn't comparable to the
// lead lap's, so we switch to counting laps instead and never revert to a
// time gap for anyone further back (lap deficits only grow going down the
// order, never shrink). A driver on the SAME lap as the car ahead of them
// (laps_behind === 0) but already in lapped mode carries the same lap
// count forward rather than resuming the time-based cumulative sum — e.g.
// P4 is "+1 LAP" behind the leader, and P5 (same lap as P4) is also
// "+1 LAP", not "+1 LAP" plus a few extra tenths.
//
// Separately, gap_to_ahead_seconds can still be null with laps_behind === 0
// for a driver who genuinely has no time set yet — that's the original
// "chain broken" case and keeps showing "—" for the rest of the field,
// since every gap behind an unknown gap is itself unknowable.
function computeGapLabels(gaps: DriverGap[]): Record<string, string> {
  const sorted = [...gaps].sort((a, b) => a.position - b.position)
  const labels: Record<string, string> = {}
  let cumulativeSeconds = 0
  let cumulativeLaps = 0
  let lappedMode = false
  let chainBroken = false

  for (const gap of sorted) {
    if (gap.position === 1) {
      labels[gap.driver_id] = "Leader"
      continue
    }

    if (lappedMode || gap.laps_behind > 0) {
      lappedMode = true
      cumulativeLaps += gap.laps_behind
      labels[gap.driver_id] = formatLapsBehind(cumulativeLaps)
      continue
    }

    if (gap.gap_to_ahead_seconds === null || chainBroken) {
      chainBroken = true
      labels[gap.driver_id] = "—"
      continue
    }

    cumulativeSeconds += gap.gap_to_ahead_seconds
    labels[gap.driver_id] = formatGapToLeader(cumulativeSeconds)
  }

  return labels
}

export function LiveTimingTower({ sessionId }: LiveTimingTowerProps) {
  const { data: drivers } = useDrivers()
  const { data: gapsResponse, isLoading: gapsLoading } = useSessionGaps(sessionId)
  const { lapsByDriver, staleConnection } = useLiveTelemetry(sessionId)
  const selectedDriverId = useSessionStore((state) => state.selectedDriverId)
  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)

  const gaps = useMemo(() => gapsResponse?.gaps ?? [], [gapsResponse])
  const driverIds = useMemo(() => gaps.map((gap) => gap.driver_id), [gaps])

  // REST fallback for compound/lap time before the WS has delivered a live
  // event for this driver yet. Shares its react-query cache entry with
  // SectorHeatmap's per-driver queries via the same query key.
  const lapsQueries = useQueries({
    queries: driverIds.map((driverId) => driverLapsQueryOptions(sessionId, driverId)),
  })

  const driversById = useMemo(() => {
    const map = new Map<string, DriverResponse>()
    for (const driver of drivers ?? []) map.set(driver.id, driver)
    return map
  }, [drivers])

  const latestLapByDriver = useMemo(() => {
    const map = new Map<string, LapDataResponse>()
    driverIds.forEach((driverId, index) => {
      const items = lapsQueries[index]?.data?.items ?? []
      if (items.length === 0) return
      const latest = items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b))
      map.set(driverId, latest)
    })
    return map
    // lapsQueries is a fresh array each render (useQueries) — driverIds is
    // the real change signal, lapsQueries is read for its current .data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driverIds, lapsQueries])

  const gapLabels = useMemo(() => computeGapLabels(gaps), [gaps])

  const rows: TimingRow[] = useMemo(() => {
    return [...gaps]
      .sort((a, b) => a.position - b.position)
      .map((gap) => {
        const driver = driversById.get(gap.driver_id)
        const liveLap = lapsByDriver[gap.driver_id]
        const latestRestLap = latestLapByDriver.get(gap.driver_id)
        return {
          driverId: gap.driver_id,
          position: gap.position,
          code: driver?.code ?? "???",
          teamColor: driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR,
          lastLapSeconds: liveLap?.lap_time_seconds ?? latestRestLap?.lap_time_seconds ?? null,
          gapLabel: gapLabels[gap.driver_id] ?? "—",
          compound: liveLap?.compound ?? latestRestLap?.compound ?? null,
        }
      })
  }, [gaps, driversById, lapsByDriver, latestLapByDriver, gapLabels])

  // FLIP-style reorder animation (adapted from sab-f1-ui's timing-tower CSS
  // approach): DOM rows stay keyed by driver_id across re-sorts, so on
  // reorder we measure each row's old/new top, snap it back to the old spot
  // with no transition, then transition it to its real spot on the next
  // frame — cheaper than a JS animation library for a once-in-a-while
  // reorder.
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const prevTops = useRef<Map<string, number>>(new Map())
  const orderKey = rows.map((row) => row.driverId).join(",")

  useLayoutEffect(() => {
    const newTops = new Map<string, number>()
    rowRefs.current.forEach((el, driverId) => {
      newTops.set(driverId, el.getBoundingClientRect().top)
    })

    // Reduced motion: still reorder (rows are already in their new DOM
    // position by this point), just skip the animated glide between old
    // and new spots.
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    if (!prefersReducedMotion) {
      rowRefs.current.forEach((el, driverId) => {
        const prevTop = prevTops.current.get(driverId)
        const newTop = newTops.get(driverId)
        if (prevTop === undefined || newTop === undefined || prevTop === newTop) return
        const delta = prevTop - newTop
        el.style.transition = "none"
        el.style.transform = `translateY(${delta}px)`
        requestAnimationFrame(() => {
          el.style.transition = "transform var(--duration-row-reorder) var(--ease-out-strong)"
          el.style.transform = ""
        })
      })
    }

    prevTops.current = newTops
    // orderKey is the intended re-measurement trigger (reorder only) —
    // rows itself changes on every gap-value update, which would defeat
    // the point of measuring only on reorder.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderKey])

  if (gapsLoading && rows.length === 0) {
    return (
      <div className="flex flex-col gap-1 p-2">
        {Array.from({ length: 22 }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-9 w-full" />
        ))}
      </div>
    )
  }

  if (!gapsLoading && rows.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 p-6 text-center">
        <p className="text-sm font-medium text-foreground">No live race session active</p>
        <p className="text-xs text-muted-foreground">Timing data will appear here during a live race</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Connected but quiet for 30s+ — worker/beat are likely scaled to 0
          between race weekends (Day 40 hybrid deployment, see fly.toml).
          Informational, not an error — same blue-toned treatment as
          HistoricalDataBanner. Note: if `sessionId` resolves to a
          completed/historical session rather than a genuinely live one,
          this can show alongside that page-level banner; left as-is since
          both are accurate for that case, not worth extra plumbing to
          suppress one. */}
      {staleConnection && (
        <div className="flex items-center gap-2 border-b border-blue-900/40 bg-blue-950/40 px-4 py-2 text-sm text-blue-200">
          <Info className="h-4 w-4 flex-shrink-0" />
          <span>No live race data. Showing last completed race. Live timing is active during race weekends.</span>
        </div>
      )}
      {rows.map((row) => (
        <div
          key={row.driverId}
          ref={(el) => {
            if (el) rowRefs.current.set(row.driverId, el)
            else rowRefs.current.delete(row.driverId)
          }}
          role="button"
          tabIndex={0}
          onClick={() => setSelectedDriver(row.driverId)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") setSelectedDriver(row.driverId)
          }}
          className={cn(
            // flex + justify-between with every field fixed-width: each
            // field claims exactly its own space and justify-between
            // distributes the leftover evenly between them, so there's no
            // single large gap anywhere and the tyre icon (last field)
            // lands flush against the row's right edge. Row height
            // (py-1.5) is unchanged. Lap time was dropped — already shown
            // in SectorHeatmap, redundant here.
            "flex cursor-pointer items-center justify-between border-b px-1.5 py-1.5 text-xs",
            row.driverId === selectedDriverId ? "bg-accent" : "hover:bg-muted/50",
          )}
        >
          <span className="w-6 text-center font-mono text-muted-foreground">{row.position}</span>
          <span
            className="h-5 w-1 flex-shrink-0 rounded-full"
            style={{ backgroundColor: row.teamColor }}
          />
          <span className="w-10 font-semibold">{row.code}</span>
          <span className="w-20 text-right font-mono tabular-nums text-muted-foreground">
            {row.gapLabel}
          </span>
          <TyreIcon compound={row.compound} />
        </div>
      ))}
    </div>
  )
}
