import { ScrollView, Text, View } from "react-native"
import { QuickAccessCards } from "@/components/dashboard/QuickAccessCards"
import { RecentAlertsFeed } from "@/components/dashboard/RecentAlertsFeed"
import { UpcomingRaceCard } from "@/components/dashboard/UpcomingRaceCard"

// RN port of web/src/pages/DashboardPage.tsx. Drops DriverRosterGrid (the
// full roster already has its own Drivers tab on mobile — no need to
// duplicate it on Home the way web's single-page layout does).
export default function HomeScreen() {
  return (
    <ScrollView className="flex-1 bg-background" contentContainerClassName="gap-4 p-4">
      <UpcomingRaceCard />
      <RecentAlertsFeed />
      <QuickAccessCards />
    </ScrollView>
  )
}
