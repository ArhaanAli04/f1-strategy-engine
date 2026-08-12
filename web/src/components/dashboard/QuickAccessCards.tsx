import { FlaskConical, Radio, Users } from "lucide-react"
import { Link } from "react-router-dom"
import { Card } from "@/components/ui/card"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { cn } from "@/lib/utils"
import { ROUTES } from "@/utils/constants"

interface QuickAccessCardProps {
  icon: React.ReactNode
  label: string
  description: string
  to?: string
  disabledReason?: string
}

function QuickAccessCard({ icon, label, description, to, disabledReason }: QuickAccessCardProps) {
  const content = (
    <Card
      className={cn(
        "flex h-full flex-col gap-2 p-4 transition-colors",
        to ? "hover:bg-accent" : "cursor-not-allowed",
      )}
    >
      <span className={cn(!to && "opacity-50")}>{icon}</span>
      <div className="text-sm font-semibold">{label}</div>
      <div className="text-xs text-muted-foreground">{to ? description : (disabledReason ?? description)}</div>
    </Card>
  )

  if (!to) return content
  return (
    <Link to={to} className="block h-full">
      {content}
    </Link>
  )
}

// The current race's Race ("R") session, when live viewing is possible —
// falls back to the latest session on record for that race so the card can
// still deep-link somewhere reasonable outside of a live weekend.
function useLiveRaceSessionId(): string | null {
  const { data: race } = useCurrentRace()
  if (!race || race.sessions.length === 0) return null
  const raceSession = race.sessions.find((s) => s.session_type === "R")
  const latest = [...race.sessions].sort((a, b) => b.session_date.localeCompare(a.session_date))[0]
  return (raceSession ?? latest).id
}

export function QuickAccessCards() {
  const liveSessionId = useLiveRaceSessionId()

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <QuickAccessCard
        icon={<Radio className="h-5 w-5 text-primary" />}
        label="Live Race"
        description="Timing tower, circuit map & strategy wall"
        disabledReason="No race currently scheduled"
        to={liveSessionId ? ROUTES.race(liveSessionId) : undefined}
      />
      <QuickAccessCard
        icon={<FlaskConical className="h-5 w-5 text-primary" />}
        label="Strategy Simulator"
        description="Run Monte Carlo pit strategy scenarios"
        to={ROUTES.SIMULATE}
      />
      <QuickAccessCard
        icon={<Users className="h-5 w-5 text-primary" />}
        label="Driver Analytics"
        description="Jump to the driver roster below"
        to="#driver-roster"
      />
    </div>
  )
}
