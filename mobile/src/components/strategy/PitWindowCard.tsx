import { Text, View } from "react-native"
import { DriverChip } from "@/components/shared/DriverChip"
import { usePitWindow } from "@/hooks/useStrategy"

interface PitWindowCardProps {
  sessionId: string | null
  driverId: string | null
  compact?: boolean
}

// RN port of web/src/components/strategy/PitWindowCard.tsx — Checkpoint 5
// (core-feature-rebuild) renders the SAME recommended_compound/
// confidence_score/explanation.narrative fields web's unified card does.
// Mobile has no WebSocket lap-completion stream wired to the Strategy tab
// (useLiveTelemetry.ts exists for the Live tab's CircuitMapPanel but isn't
// consumed here) — this always sources from usePitWindow's on-demand REST
// recompute, same as before this change. A deliberate, documented
// simplification (see mobile/src/README.md), not an oversight: there's no
// isReplayActive branch to remove here because one never existed on mobile.
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

  if (compact) {
    const caption =
      window.confidence_score !== null
        ? `${Math.round(window.confidence_score * 100)}% confidence`
        : (window.explanation?.narrative ?? null)

    return (
      <View className="gap-1 rounded-md border border-white/10 bg-surface p-2">
        <View className="flex-row items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          <Text className="font-mono text-xs font-semibold text-foreground">
            L{window.window_start}–{window.window_end}
          </Text>
        </View>
        {caption && (
          <Text numberOfLines={1} className="text-[10px] text-muted">
            {caption}
          </Text>
        )}
      </View>
    )
  }

  return (
    <View className="gap-2 rounded-md border border-white/10 bg-surface p-4">
      <View className="flex-row items-center justify-between gap-2">
        <Text className="text-base font-semibold text-foreground">Pit Window</Text>
        {window.confidence_score !== null && (
          <View className="rounded-full bg-primary/10 px-2 py-0.5">
            <Text className="text-xs font-semibold text-primary">
              {Math.round(window.confidence_score * 100)}% confidence
            </Text>
          </View>
        )}
      </View>
      <Text className="text-2xl font-bold text-foreground">
        Lap {window.window_start}–{window.window_end}
      </Text>
      <Text className="text-sm text-muted">
        Recommended: Lap {window.pit_lap} — {window.recommended_compound}
      </Text>
      {window.explanation && <Text className="text-sm text-muted">{window.explanation.narrative}</Text>}
    </View>
  )
}
