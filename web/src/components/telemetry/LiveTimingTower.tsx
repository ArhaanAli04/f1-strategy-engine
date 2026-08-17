import { useLayoutEffect, useMemo, useRef } from "react"
import { useQueries } from "@tanstack/react-query"
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

// Position 1 shows "Leader" rather than a gap. A null gap_to_ahead_seconds
// means the driver hasn't set a time yet — once a null link appears in the
// ahead-chain, every cumulative gap behind it is unknowable, so the chain is
// marked broken rather than silently summing past the gap. A lapped driver
// naturally shows a large cumulative gap (e.g. "+1:23.456") — no separate
// "LAP" label needed.
function computeGapLabels(gaps: DriverGap[]): Record<string, string> {
  const sorted = [...gaps].sort((a, b) => a.position - b.position)
  const labels: Record<string, string> = {}
  let cumulative = 0
  let chainBroken = false

  for (const gap of sorted) {
    if (gap.position === 1) {
      labels[gap.driver_id] = "Leader"
      continue
    }
    if (gap.gap_to_ahead_seconds === null || chainBroken) {
      chainBroken = true
      labels[gap.driver_id] = "—"
      continue
    }
    cumulative += gap.gap_to_ahead_seconds
    labels[gap.driver_id] = formatGapToLeader(cumulative)
  }

  return labels
}

export function LiveTimingTower({ sessionId }: LiveTimingTowerProps) {
  const { data: drivers } = useDrivers()
  const { data: gapsResponse, isLoading: gapsLoading } = useSessionGaps(sessionId)
  const { lapsByDriver } = useLiveTelemetry(sessionId)
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
