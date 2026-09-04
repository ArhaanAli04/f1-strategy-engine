import { usePitRecommendation, type PitRecommendationView } from "@/hooks/useStrategy"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import { DriverChip } from "@/components/shared/DriverChip"

interface PitWindowCardProps {
  sessionId: string | null
  driverId: string | null
  compact?: boolean
  className?: string
}

// windowLabel/lapLabel let compact (space-constrained, "L22–26") and full
// ("Lap 22–26") modes share one headline rule — a real window band when
// available, falling back to the single recommended lap otherwise (e.g. a
// pre-Checkpoint-4 history row, or a lap where the recommendation
// computation degraded gracefully — see usePitRecommendation's own
// viewFromHistoryEntry).
function formatHeadline(view: PitRecommendationView, windowLabel: string, lapLabel: string): string {
  if (view.windowStart !== null && view.windowEnd !== null) {
    return `${windowLabel}${view.windowStart}–${view.windowEnd}`
  }
  return `${lapLabel}${view.pitLap}`
}

function formatCompactCaption(view: PitRecommendationView | null): string {
  if (!view) return "No prediction yet"
  const parts = [
    view.confidenceScore !== null ? `${Math.round(view.confidenceScore * 100)}% confidence` : null,
    view.asOfLapNumber !== null ? `as of lap ${view.asOfLapNumber}` : null,
  ].filter((part): part is string => part !== null)
  if (parts.length > 0) return parts.join(" — ")
  return view.explanation?.narrative ?? "Pit prediction available"
}

// Checkpoint 5 (core-feature-rebuild): single render path for both modes —
// no isReplayActive branch here at all. usePitRecommendation already picked
// a source (live/replay progression vs. on-demand recompute) and normalized
// it into one PitRecommendationView; this component only ever reads that.
export function PitWindowCard({ sessionId, driverId, compact, className }: PitWindowCardProps) {
  const { view, isLoading } = usePitRecommendation(sessionId, driverId)

  if (isLoading) {
    return <LoadingSkeleton className={cn(compact ? "h-24" : "h-40", "w-full", className)} />
  }

  if (compact) {
    return (
      <Card className={cn("p-2", className)}>
        <div className="flex items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          {view && (
            <span className="font-mono text-xs font-semibold tabular-nums">
              {formatHeadline(view, "L", "Lap ")}
            </span>
          )}
        </div>
        <p
          className="mt-1 line-clamp-1 text-[10px] text-muted-foreground"
          title={formatCompactCaption(view)}
        >
          {formatCompactCaption(view)}
        </p>
      </Card>
    )
  }

  if (!view) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">Pit Window</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          No pit window predicted.
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">Pit Window</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="text-2xl font-bold tabular-nums">{formatHeadline(view, "Lap ", "Lap ")}</div>
          {view.confidenceScore !== null && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
              {Math.round(view.confidenceScore * 100)}% confidence
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Recommended: Lap {view.pitLap}
          {view.recommendedCompound && ` — ${view.recommendedCompound}`}
        </p>
        {view.explanation && (
          <p className="text-sm text-muted-foreground">{view.explanation.narrative}</p>
        )}
        {view.asOfLapNumber !== null && (
          <p className="text-[10px] text-muted-foreground">As of lap {view.asOfLapNumber}</p>
        )}
        {view.pitProbability !== null && (
          <p className="text-[10px] text-muted-foreground">
            Pit predictor: {Math.round(view.pitProbability * 100)}% (lagging indicator — fires on
            the pit lap itself, not before)
          </p>
        )}
      </CardContent>
    </Card>
  )
}
