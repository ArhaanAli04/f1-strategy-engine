import { ScrollView, View } from "react-native"
import { QuickAccessCards } from "@/components/dashboard/QuickAccessCards"
import { RecentAlertsFeed } from "@/components/dashboard/RecentAlertsFeed"
import { UpcomingRaceCard } from "@/components/dashboard/UpcomingRaceCard"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"

// RN port of web/src/pages/DashboardPage.tsx. Drops DriverRosterGrid (the
// full roster already has its own Drivers tab on mobile — no need to
// duplicate it on Home the way web's single-page layout does).
export default function HomeScreen() {
  // Called again here (UpcomingRaceCard already calls it internally) purely
  // for its dataUpdatedAt — react-query dedupes same-key queries onto one
  // shared cache entry/request, not a second network call.
  const { dataUpdatedAt } = useUpcomingRace()

  return (
    <View className="flex-1 bg-background">
      <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      <ScrollView className="flex-1" contentContainerClassName="gap-4 p-4">
        <UpcomingRaceCard />
        <RecentAlertsFeed />
        <QuickAccessCards />
      </ScrollView>
    </View>
  )
}
