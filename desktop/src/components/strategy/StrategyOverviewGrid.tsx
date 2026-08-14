import { useStrategyOverview } from "@/hooks/useStrategy"
import { PitWindowCard } from "@/components/strategy/PitWindowCard"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"

interface StrategyOverviewGridProps {
  sessionId: string | null
}

const GRID_SKELETON_COUNT = 22

// The "strategy wall" — one compact PitWindowCard per driver currently in
// the session. drivers comes from StrategyOverviewResponse, which is
// already session-scoped (not the full historical roster), so this
// naturally caps at however many of the 2026 grid's 22 drivers are active
// in this session.
export function StrategyOverviewGrid({ sessionId }: StrategyOverviewGridProps) {
  const { data: overview, isLoading } = useStrategyOverview(sessionId)

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-2">
        {Array.from({ length: GRID_SKELETON_COUNT }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-24 w-full" />
        ))}
      </div>
    )
  }

  const drivers = overview?.drivers ?? []

  if (drivers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-1 p-6 text-center">
        <p className="text-sm font-medium text-foreground">No live race session active</p>
        <p className="text-xs text-muted-foreground">
          Strategy predictions will appear here during a live race
        </p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2">
      {drivers.map((entry) => (
        <PitWindowCard
          key={entry.driver_id}
          sessionId={sessionId}
          driverId={entry.driver_id}
          compact
        />
      ))}
    </div>
  )
}
