import { usePitWindow } from "@/hooks/useStrategy"
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

// Checkpoint 5 (core-feature-rebuild): renders the SAME recommended_compound/
// confidence_score/explanation.narrative fields web's unified PitWindowCard
// does. Desktop has no WebSocket lap-completion stream anywhere in its
// codebase (confirmed: no live/replay-progression signal exists to build a
// web-style usePitRecommendation on) — it always sources from usePitWindow's
// on-demand REST recompute, same as before this change. That's a deliberate,
// documented simplification (see desktop/src/README.md), not an oversight:
// unlike web, there's no isReplayActive branch to remove here because one
// never existed.
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

  if (compact) {
    const caption =
      window.confidence_score !== null
        ? `${Math.round(window.confidence_score * 100)}% confidence`
        : (window.explanation?.narrative ?? null)

    return (
      <Card className={cn("p-2", className)}>
        <div className="flex items-center justify-between gap-2">
          {driverId && <DriverChip driverId={driverId} />}
          <span className="font-mono text-xs font-semibold tabular-nums">
            L{window.window_start}–{window.window_end}
          </span>
        </div>
        {caption && (
          <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground" title={caption}>
            {caption}
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
        <div className="flex items-center justify-between gap-2">
          <div className="text-2xl font-bold tabular-nums">
            Lap {window.window_start}–{window.window_end}
          </div>
          {window.confidence_score !== null && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
              {Math.round(window.confidence_score * 100)}% confidence
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Recommended: Lap {window.pit_lap} — {window.recommended_compound}
        </p>
        {window.explanation && (
          <p className="text-sm text-muted-foreground">{window.explanation.narrative}</p>
        )}
      </CardContent>
    </Card>
  )
}
