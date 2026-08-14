import { FlaskConical, Radio, Users } from "lucide-react"
import { Card } from "@/components/ui/card"
import type { DesktopPage } from "@/App"

interface QuickAccessCardProps {
  icon: React.ReactNode
  label: string
  description: string
  onClick: () => void
}

function QuickAccessCard({ icon, label, description, onClick }: QuickAccessCardProps) {
  return (
    <button type="button" onClick={onClick} className="block h-full text-left">
      <Card className="flex h-full flex-col gap-2 p-4 transition-colors hover:bg-accent">
        <span className="text-primary">{icon}</span>
        <div className="text-sm font-semibold">{label}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </Card>
    </button>
  )
}

interface QuickAccessCardsProps {
  onNavigate: (page: DesktopPage) => void
}

// Mirrors web's QuickAccessCards.tsx — Live Race and Driver Analytics are
// only reachable from here, not the sidebar (see Sidebar.tsx's comment).
export function QuickAccessCards({ onNavigate }: QuickAccessCardsProps) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <QuickAccessCard
        icon={<Radio className="h-5 w-5" />}
        label="Live Race"
        description="Timing tower, circuit map & strategy wall"
        onClick={() => onNavigate("liveRace")}
      />
      <QuickAccessCard
        icon={<FlaskConical className="h-5 w-5" />}
        label="Strategy Simulator"
        description="Run Monte Carlo pit strategy scenarios"
        onClick={() => onNavigate("simulator")}
      />
      <QuickAccessCard
        icon={<Users className="h-5 w-5" />}
        label="Driver Analytics"
        description="Season stats, style fingerprint & lap history"
        onClick={() => onNavigate("driverAnalytics")}
      />
    </div>
  )
}
