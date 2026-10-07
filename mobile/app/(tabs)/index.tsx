import { ScrollView, Text, View } from "react-native"
import { DriverRosterGrid } from "@/components/dashboard/DriverRosterGrid"
import { QuickAccessCards } from "@/components/dashboard/QuickAccessCards"
import { RecentAlertsFeed } from "@/components/dashboard/RecentAlertsFeed"
import { UpcomingRaceCard } from "@/components/dashboard/UpcomingRaceCard"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"

// RN port of web/src/pages/DashboardPage.tsx, roster included below the quick
// access cards as on web and desktop (since 2026-10-06; it was left to the
// Drivers tab before).
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
        <View className="gap-3">
          <Text className="text-lg font-semibold text-foreground">Driver Roster</Text>
          <DriverRosterGrid />
        </View>
      </ScrollView>
    </View>
  )
}
