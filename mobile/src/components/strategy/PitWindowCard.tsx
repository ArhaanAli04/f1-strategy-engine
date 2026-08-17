import { Text, View } from "react-native"
import { DriverChip } from "@/components/shared/DriverChip"
import { usePitWindow } from "@/hooks/useStrategy"
import type { FeatureContributionResponse } from "@/types"

interface PitWindowCardProps {
  sessionId: string | null
  driverId: string | null
  compact?: boolean
}

// Mirrors backend/services/ml/explainability.py's FEATURE_LABELS — kept in
// sync manually, same as web's copy (no endpoint exposes these labels).
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

// RN port of web/src/components/strategy/PitWindowCard.tsx — same
// compact/full modes and SHAP-explanation formatting.
export function PitWindowCard({ sessionId, driverId, compact }: PitWindowCardProps) {
  const { data: windows, isLoading } = usePitWindow(sessionId, driverId)

  if (isLoading) {
    return <View className={`${compact ? "h-24" : "h-40"} w-full rounded-md bg-surface`} />
  }

  // Soonest predicted window — the endpoint can return more than one
  // candidate window across the remaining stint.
  const window = windows?.[0] ?? null

  if (!window) {
    return (
      <View className="rounded-md border border-white/10 bg-surface p-3">
        <Text className="text-xs text-muted">No pit window predicted.</Text>
      </View>
    )
  }

  const shapText = formatShapExplanation(window.shap_explanation)

  if (compact) {
    return (
      <View className="gap-1 rounded-md border border-white/10 bg-surface p-2">
        <View className="flex-row items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          <Text className="font-mono text-xs font-semibold text-foreground">
            L{window.window_start}–{window.window_end}
          </Text>
        </View>
        {shapText && (
          <Text numberOfLines={1} className="text-[10px] text-muted">
            {shapText}
          </Text>
        )}
      </View>
    )
  }

  return (
    <View className="gap-2 rounded-md border border-white/10 bg-surface p-4">
      <Text className="text-base font-semibold text-foreground">Pit Window</Text>
      <Text className="text-2xl font-bold text-foreground">
        Lap {window.window_start}–{window.window_end}
      </Text>
      <Text className="text-sm text-muted">Recommended: Lap {window.pit_lap}</Text>
      {shapText && <Text className="text-sm text-muted">{shapText}</Text>}
    </View>
  )
}
