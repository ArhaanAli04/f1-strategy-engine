import { useDrivers } from "@/hooks/useDrivers"
import { cn } from "@/lib/utils"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

interface DriverChipProps {
  driverId: string
  className?: string
}

// Small reusable code + team-color chip — shared by UndercutThreatPanel,
// CompetitorStrategyTable, and AlertsPage. Resolves driverId -> code/team
// color via the same cached useDrivers() roster query LiveTimingTower uses,
// so mounting several chips doesn't trigger duplicate fetches.
export function DriverChip({ driverId, className }: DriverChipProps) {
  const { data: drivers } = useDrivers()
  const driver = drivers?.find((d) => d.id === driverId)
  const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold",
        className,
      )}
    >
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: teamColor }} />
      {driver?.code ?? "???"}
    </span>
  )
}
