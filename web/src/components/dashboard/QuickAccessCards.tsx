import { FlaskConical, Radio, Users } from "lucide-react"
import { Link } from "react-router-dom"
import { Card } from "@/components/ui/card"
import { ROUTES } from "@/utils/constants"

interface QuickAccessCardProps {
  icon: React.ReactNode
  label: string
  description: string
  to: string
}

// All three cards are always enabled now (Live Race no longer depends on a
// live session existing — see QuickAccessCards below), so there's no
// disabled state left to render.
function QuickAccessCard({ icon, label, description, to }: QuickAccessCardProps) {
  return (
    <Link to={to} className="block h-full">
      <Card className="flex h-full flex-col gap-2 p-4 transition-colors hover:bg-accent">
        {icon}
        <div className="text-sm font-semibold">{label}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </Card>
    </Link>
  )
}

export function QuickAccessCards() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {/* Always enabled — RacePage resolves a session itself via
          useResolvedSession (live race, or the most recent completed one)
          when reached through this bare /race route. */}
      <QuickAccessCard
        icon={<Radio className="h-5 w-5 text-primary" />}
        label="Live Race"
        description="Timing tower, circuit map & strategy wall"
        to={ROUTES.LIVE_RACE}
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
