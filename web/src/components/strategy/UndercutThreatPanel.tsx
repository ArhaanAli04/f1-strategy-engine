import { useMemo } from "react"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useUndercut } from "@/hooks/useStrategy"
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
      colorClass: "text-emerald-600 dark:text-emerald-400",
    }
  }
  return {
    label: `${Math.abs(value).toFixed(3)}s net loss`,
    colorClass: "text-red-600 dark:text-red-400",
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
          <p
            className="text-[10px] text-muted-foreground"
            title="Predicted net time after undercut vs staying out"
          >
            Predicted net time after undercut vs staying out
          </p>
          <p className="text-xs">{data.recommended_action}</p>
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

  // Opportunity: can the selected driver undercut the car ahead by pitting now?
  const opportunity = useUndercut(sessionId, driverId, aheadDriverId)
  // Threat: can the car behind undercut the selected driver by pitting now?
  // Reversed driver_id/target vs. the opportunity call — see resolveNeighbors.
  const threat = useUndercut(sessionId, behindDriverId, driverId)

  if (!driverId) {
    return (
      <p className="text-sm text-muted-foreground">Select a driver to see undercut threats.</p>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Undercut Threats</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
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
      </CardContent>
    </Card>
  )
}
