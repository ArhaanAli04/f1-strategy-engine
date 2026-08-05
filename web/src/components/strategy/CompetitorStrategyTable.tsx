import { useQuery } from "@tanstack/react-query"
import * as strategyApi from "@/api/strategy"
import { DriverChip } from "@/components/shared/DriverChip"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"

interface CompetitorStrategyTableProps {
  sessionId: string | null
}

const POLL_INTERVAL_MS = 30_000

// Deliberately not useStrategyOverview (staleTime-only, no polling) — this
// table needs live 30s polling per spec. Shares the same query key/cache
// entry as useStrategyOverview though, so mounting both on one page (e.g.
// RacePage's grid + this table) doesn't double-fetch.
export function CompetitorStrategyTable({ sessionId }: CompetitorStrategyTableProps) {
  const { data: overview, isLoading } = useQuery({
    queryKey: ["strategy", "overview", sessionId],
    queryFn: () => strategyApi.getStrategyOverview(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: POLL_INTERVAL_MS,
  })

  if (isLoading) {
    return (
      <div className="flex flex-col gap-1">
        {Array.from({ length: 22 }).map((_, index) => (
          <LoadingSkeleton key={index} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  const drivers = [...(overview?.drivers ?? [])].sort(
    (a, b) => a.predicted_pit_lap - b.predicted_pit_lap,
  )

  if (drivers.length === 0) {
    return <p className="text-sm text-muted-foreground">No competitor predictions available.</p>
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <th className="py-1 font-medium">Driver</th>
          <th className="py-1 font-medium">Predicted Pit Lap</th>
          <th className="py-1 font-medium">Pit Probability</th>
        </tr>
      </thead>
      <tbody>
        {drivers.map((entry) => (
          <tr key={entry.driver_id} className="border-b last:border-0">
            <td className="py-1.5">
              <DriverChip driverId={entry.driver_id} />
            </td>
            <td className="py-1.5 font-mono tabular-nums">Lap {entry.predicted_pit_lap}</td>
            <td className="py-1.5 font-mono tabular-nums">
              {Math.round(entry.pit_probability * 100)}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
