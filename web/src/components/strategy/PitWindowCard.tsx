import { usePitWindow } from "@/hooks/useStrategy"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { DriverChip } from "@/components/shared/DriverChip"
import type { FeatureContributionResponse } from "@/types"

interface PitWindowCardProps {
  sessionId: string | null
  driverId: string | null
  compact?: boolean
  className?: string
}

// Mirrors backend/services/ml/explainability.py's FEATURE_LABELS — kept in
// sync manually since no endpoint exposes these labels; feature_name falls
// back to itself for anything added backend-side and not yet mirrored here.
const FEATURE_LABELS: Record<string, string> = {
  lap_number: "Lap number",
  compound_encoded: "Tyre compound",
  tyre_age_laps: "Tyre age",
  fuel_adjusted_time: "Fuel-adjusted pace",
  circuit_id_encoded: "Circuit",
  driver_id_encoded: "Driver",
  current_tyre_age: "Tyre age",
  predicted_life_remaining: "Predicted tyre life remaining",
  gap_to_car_ahead: "Gap to car ahead",
  gap_to_car_behind: "Gap to car behind",
  safety_car_probability: "Safety car probability",
  laps_to_race_end: "Laps to race end",
  position: "Track position",
  fuel_load_est: "Estimated fuel load",
}

// Top SHAP contribution by |contribution| — shap_explanation is already
// sorted this way by explain_prediction, but sort defensively rather than
// trust ordering across the wire.
function topContribution(
  shap: FeatureContributionResponse[] | null,
): FeatureContributionResponse | null {
  if (!shap || shap.length === 0) return null
  return [...shap].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))[0]
}

function formatShapExplanation(shap: FeatureContributionResponse[] | null): string | null {
  const top = topContribution(shap)
  if (!top) return null
  const label = FEATURE_LABELS[top.feature_name] ?? top.feature_name
  const sign = top.direction === "+" ? "+" : "-"
  return `${label} is the primary factor — ${top.value.toFixed(1)} (${sign}${Math.abs(top.contribution).toFixed(2)} impact)`
}

export function PitWindowCard({ sessionId, driverId, compact, className }: PitWindowCardProps) {
  const { data: windows, isLoading } = usePitWindow(sessionId, driverId)

  if (isLoading) {
    return <LoadingSkeleton className={cn(compact ? "h-24" : "h-40", "w-full", className)} />
  }

  // Soonest predicted window — the endpoint can return more than one
  // candidate window across the remaining stint.
  const window = windows?.[0] ?? null

  if (!window) {
    return (
      <Card className={cn(compact && "p-2", className)}>
        <CardContent className={cn("text-xs text-muted-foreground", compact ? "p-0" : "pt-6")}>
          No pit window predicted.
        </CardContent>
      </Card>
    )
  }

  const shapText = formatShapExplanation(window.shap_explanation)

  if (compact) {
    return (
      <Card className={cn("p-2", className)}>
        <div className="flex items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          <span className="font-mono text-xs font-semibold tabular-nums">
            L{window.window_start}–{window.window_end}
          </span>
        </div>
        {shapText && (
          <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground" title={shapText}>
            {shapText}
          </p>
        )}
      </Card>
    )
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">Pit Window</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="text-2xl font-bold tabular-nums">
          Lap {window.window_start}–{window.window_end}
        </div>
        <p className="text-sm text-muted-foreground">Recommended: Lap {window.pit_lap}</p>
        {shapText && <p className="text-sm text-muted-foreground">{shapText}</p>}
      </CardContent>
    </Card>
  )
}
