import { DriverRosterGrid } from "@/components/dashboard/DriverRosterGrid"
import { QuickAccessCards } from "@/components/dashboard/QuickAccessCards"
import { RecentAlertsFeed } from "@/components/dashboard/RecentAlertsFeed"
import { UpcomingRaceCard } from "@/components/dashboard/UpcomingRaceCard"

export function DashboardPage() {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-6xl space-y-6">
        <h1 className="text-xl font-semibold">Dashboard</h1>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <UpcomingRaceCard />
          </div>
          <RecentAlertsFeed />
        </div>

        <QuickAccessCards />

        <div id="driver-roster">
          <h2 className="mb-3 text-lg font-semibold">Driver Roster</h2>
          <DriverRosterGrid />
        </div>
      </div>
    </div>
  )
}
