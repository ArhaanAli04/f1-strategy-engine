import { Text, View } from "react-native"
import { DriverChip } from "@/components/shared/DriverChip"
import { usePitRecommendation, type PitRecommendationView } from "@/hooks/useStrategy"

interface PitWindowCardProps {
  sessionId: string | null
  driverId: string | null
  compact?: boolean
}

// As web's formatHeadline: the window band when there is one, else the
// single lap, with "~" when that lap is only pit_predictor's estimate.
function formatHeadline(view: PitRecommendationView, windowLabel: string, lapLabel: string): string {
  if (view.windowStart !== null && view.windowEnd !== null) {
    return `${windowLabel}${view.windowStart}–${view.windowEnd}`
  }
  const prefix = view.isFallbackEstimate ? "~" : ""
  return `${prefix}${lapLabel}${view.pitLap}`
}

// As web's formatCompactCaption, for a card that has a prediction.
function formatCompactCaption(view: PitRecommendationView): string {
  const parts = [
    view.confidenceScore !== null ? `${Math.round(view.confidenceScore * 100)}% confidence` : null,
    view.asOfLapNumber !== null ? `as of lap ${view.asOfLapNumber}` : null,
  ].filter((part): part is string => part !== null)
  if (parts.length > 0) return parts.join(" — ")
  return view.explanation?.narrative ?? "Pit prediction available"
}

// RN port of web/src/components/strategy/PitWindowCard.tsx: one render path
// for both modes, from usePitRecommendation (since Day 6b-mobile CP4,
// 2026-10-09). That hook picks the source: the stored prediction for the
// current lap while a live race or Demo Replay progresses, else the
// on-demand /pit-window recompute. Mobile differences: the compact card with
// no prediction keeps its shape (driver chip, "—", "No pit window
// predicted"; owner's request 2026-10-07), and the full card's pit-predictor
// line drops web's "lagging indicator" caption, which is out of date (the
// model has given advance warning since 2026-09-04) and is being reworded in
// Day 6c.
export function PitWindowCard({ sessionId, driverId, compact }: PitWindowCardProps) {
  const { view, isLoading } = usePitRecommendation(sessionId, driverId)

  if (isLoading) {
    return <View className={`${compact ? "h-24" : "h-40"} w-full rounded-md bg-surface`} />
  }

  if (compact) {
    return (
      <View className="gap-1 rounded-md border border-white/10 bg-surface p-2">
        <View className="flex-row items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          <Text className={`font-mono text-xs font-semibold ${view ? "text-foreground" : "text-muted"}`}>
            {view ? formatHeadline(view, "L", "Lap ") : "—"}
          </Text>
        </View>
        <Text numberOfLines={1} className="text-[10px] text-muted">
          {view ? formatCompactCaption(view) : "No pit window predicted"}
        </Text>
      </View>
    )
  }

  if (!view) {
    return (
      <View className="rounded-md border border-white/10 bg-surface p-3">
        <Text className="text-xs text-muted">No pit window predicted.</Text>
      </View>
    )
  }

  return (
    <View className="gap-2 rounded-md border border-white/10 bg-surface p-4">
      <View className="flex-row items-center justify-between gap-2">
        <Text className="text-base font-semibold text-foreground">Pit Window</Text>
        {view.confidenceScore !== null && (
          <View className="rounded-full bg-primary/10 px-2 py-0.5">
            <Text className="text-xs font-semibold text-primary">
              {Math.round(view.confidenceScore * 100)}% confidence
            </Text>
          </View>
        )}
      </View>
      <Text className="text-2xl font-bold text-foreground">{formatHeadline(view, "Lap ", "Lap ")}</Text>
      <Text className="text-sm text-muted">
        {view.isFallbackEstimate ? "Estimated" : "Recommended"}: Lap {view.pitLap}
        {view.recommendedCompound ? ` — ${view.recommendedCompound}` : ""}
      </Text>
      {view.isFallbackEstimate && (
        <Text className="text-[10px] text-muted">
          Unconfirmed estimate — not yet bounded by a real pit-window search.
        </Text>
      )}
      {view.explanation && <Text className="text-sm text-muted">{view.explanation.narrative}</Text>}
      {view.asOfLapNumber !== null && <Text className="text-[10px] text-muted">As of lap {view.asOfLapNumber}</Text>}
      {view.pitProbability !== null && (
        <Text className="text-[10px] text-muted">Pit predictor: {Math.round(view.pitProbability * 100)}%</Text>
      )}
    </View>
  )
}
