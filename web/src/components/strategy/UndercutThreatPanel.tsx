import { useMemo } from "react"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useCurrentLapHistoryEntry, useUndercut } from "@/hooks/useStrategy"
import { DriverChip } from "@/components/shared/DriverChip"
import { ProgressBar } from "@/components/shared/ProgressBar"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { DriverGap, UndercutThreatResponse } from "@/types"

// projected_gap_seconds is NOT a gap-to-car distance — it's the predicted
// net time gain/loss from executing the undercut now vs. staying out
// (backend/services/strategy_service.py). Sign communicates direction, so
// the label spells out "gain"/"loss" in words and color rather than relying
// on a bare +/- sign, which read as ambiguous against a literal gap value.
function formatNetTimeDelta(value: number): { label: string; colorClass: string } {
  if (value >= 0) {
    return {
      label: `+${value.toFixed(3)}s net gain`,
      colorClass: "text-[#10B981]",
    }
  }
  return {
    label: `${Math.abs(value).toFixed(3)}s net loss`,
    colorClass: "text-[#EF4444]",
  }
}

interface UndercutThreatPanelProps {
  sessionId: string | null
  driverId: string | null
}

interface Neighbors {
  aheadDriverId: string | null
  behindDriverId: string | null
}

// GET .../undercut always answers "does driver_id pitting now gain a
// position over target" (see backend/apis/v1/strategy.py) — the car
// immediately ahead/behind in track position, matching
// alert_service.evaluate_threats' own assumption about what undercut_score
// means (see CLAUDE.md).
function resolveNeighbors(gaps: DriverGap[], driverId: string | null): Neighbors {
  if (!driverId || gaps.length === 0) return { aheadDriverId: null, behindDriverId: null }
  const sorted = [...gaps].sort((a, b) => a.position - b.position)
  const index = sorted.findIndex((gap) => gap.driver_id === driverId)
  if (index === -1) return { aheadDriverId: null, behindDriverId: null }
  return {
    aheadDriverId: index > 0 ? sorted[index - 1].driver_id : null,
    behindDriverId: index < sorted.length - 1 ? sorted[index + 1].driver_id : null,
  }
}

interface ThreatRowProps {
  label: string
  otherDriverId: string | null
  data: UndercutThreatResponse | undefined
  isLoading: boolean
}

function ThreatRow({ label, otherDriverId, data, isLoading }: ThreatRowProps) {
  if (!otherDriverId) {
    return (
      <div className="rounded-md border p-2 text-xs text-muted-foreground">
        {label}: no car in range.
      </div>
    )
  }

  return (
    <div className="space-y-1.5 rounded-md border p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <DriverChip driverId={otherDriverId} />
      </div>
      {isLoading || !data ? (
        <LoadingSkeleton className="h-4 w-full" />
      ) : (
        <>
          <ProgressBar value={data.probability_pit_now_gains_position} />
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{Math.round(data.probability_pit_now_gains_position * 100)}% gain probability</span>
            <span className={cn("font-semibold", formatNetTimeDelta(data.projected_gap_seconds).colorClass)}>
              {formatNetTimeDelta(data.projected_gap_seconds).label}
            </span>
          </div>
          <p className="text-[10px] text-muted-foreground">
            Predicted net time after undercut vs staying out
          </p>
          <p className="text-xs">{data.recommended_action}</p>
        </>
      )}
    </div>
  )
}

interface ReplayThreatRowProps {
  label: string
  otherDriverId: string | null
  probability: number | null
  asOfLap: number | null
}

// Replay/live-progression counterpart to ThreatRow above — StrategyPrediction
// history carries no projected_gap_seconds/recommended_action (see
// CLAUDE.md's Day 43 notes), so this renders a probability bar only, one
// lap-context caption instead of the exact-seconds/action lines live mode
// has room for.
function ReplayThreatRow({ label, otherDriverId, probability, asOfLap }: ReplayThreatRowProps) {
  if (!otherDriverId) {
    return (
      <div className="rounded-md border p-2 text-xs text-muted-foreground">
        {label}: no car in range.
      </div>
    )
  }

  return (
    <div className="space-y-1.5 rounded-md border p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <DriverChip driverId={otherDriverId} />
      </div>
      {probability === null ? (
        <p className="text-xs text-muted-foreground">No prediction yet</p>
      ) : (
        <>
          <ProgressBar value={probability} />
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{Math.round(probability * 100)}% gain probability</span>
            <span>as of lap {asOfLap}</span>
          </div>
        </>
      )}
    </div>
  )
}

export function UndercutThreatPanel({ sessionId, driverId }: UndercutThreatPanelProps) {
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const gaps = useMemo(() => gapsResponse?.gaps ?? [], [gapsResponse])
  const { aheadDriverId, behindDriverId } = useMemo(
    () => resolveNeighbors(gaps, driverId),
    [gaps, driverId],
  )

  // Replay/live progression: undercut_score on the SELECTED driver's own
  // history row is already "opportunity vs. car ahead" (see
  // prediction_worker._resolve_undercut_overcut) — no need to fetch the
  // neighbor's own history. overcut_score is "probability of RETAINING
  // position while the car behind pits now", so the behind car's own
  // pit-now-gains-position probability (the "Threat" row) is its complement.
  const { entry: historyEntry, isReplayActive } = useCurrentLapHistoryEntry(sessionId, driverId)

  // Skip the live ML-inference recompute while replay/live is progressing —
  // historyEntry above is what actually renders in that case.
  const opportunity = useUndercut(sessionId, driverId, aheadDriverId, !isReplayActive)
  const threat = useUndercut(sessionId, behindDriverId, driverId, !isReplayActive)

  if (!driverId) {
    return (
      <p className="text-sm text-muted-foreground">Select a driver to see undercut threats.</p>
    )
  }

  if (!sessionId) {
    return <p className="text-sm text-muted-foreground">No live race session active</p>
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Undercut Threats</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {isReplayActive ? (
          <>
            <ReplayThreatRow
              label="Opportunity — car ahead"
              otherDriverId={aheadDriverId}
              probability={historyEntry?.undercut_score ?? null}
              asOfLap={historyEntry?.lap_number ?? null}
            />
            <ReplayThreatRow
              label="Threat — car behind"
              otherDriverId={behindDriverId}
              probability={historyEntry ? 1 - historyEntry.overcut_score : null}
              asOfLap={historyEntry?.lap_number ?? null}
            />
          </>
        ) : (
          <>
            <ThreatRow
              label="Opportunity — car ahead"
              otherDriverId={aheadDriverId}
              data={opportunity.data}
              isLoading={opportunity.isLoading}
            />
            <ThreatRow
              label="Threat — car behind"
              otherDriverId={behindDriverId}
              data={threat.data}
              isLoading={threat.isLoading}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}
