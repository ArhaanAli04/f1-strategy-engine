import "../global.css"
// NOTE: Push notifications require a development build installed on a
// physical device (iOS: Apple Developer account required, Android: free
// via EAS). Cannot be tested in Expo Go or without a build. Imported for
// its side effect — must run before any screen mounts.
import "@/notifications/notificationHandler"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { TitilliumWeb_600SemiBold } from "@expo-google-fonts/titillium-web/600SemiBold"
import { TitilliumWeb_700Bold } from "@expo-google-fonts/titillium-web/700Bold"
import { useFonts } from "@expo-google-fonts/titillium-web/useFonts"
import AsyncStorage from "@react-native-async-storage/async-storage"
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister"
import { QueryClient, type Query } from "@tanstack/react-query"
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client"
import { Stack } from "expo-router"
import { useState } from "react"
import { View } from "react-native"
import { GestureHandlerRootView } from "react-native-gesture-handler"
import { SafeAreaProvider } from "react-native-safe-area-context"
import { useNotificationResponseListener } from "@/hooks/useNotificationResponseListener"
import { usePushNotifications } from "@/hooks/usePushNotifications"
import { useAuthStore, useIsAuthenticated } from "@/stores/authStore"

// Offline support (Day 32 Checkpoint 5) — scoped to exactly 3 query-key
// families, matching each family's queryKey prefix as this project's own
// hooks build it: useUpcomingRace -> ["race","upcoming"], useDrivers ->
// ["drivers"], usePitWindow/useStrategyOverview/useSimulationResult all
// start with ["strategy", ...]. Everything else (live telemetry, alerts,
// session gaps, circuit outlines) stays in-memory-only — react-query's own
// cache still serves their last-successful value while offline, it just
// isn't written to AsyncStorage across app restarts.
const PERSISTED_QUERY_KEY_PREFIXES: readonly (readonly string[])[] = [
  ["race", "upcoming"],
  ["drivers"],
  ["strategy"],
]

function shouldPersistQuery(query: Query): boolean {
  if (query.state.status !== "success") return false
  return PERSISTED_QUERY_KEY_PREFIXES.some((prefix) =>
    prefix.every((segment, i) => query.queryKey[i] === segment),
  )
}

export default function RootLayout() {
  const [fontsLoaded] = useFonts({
    TitilliumWeb_400Regular,
    TitilliumWeb_600SemiBold,
    TitilliumWeb_700Bold,
  })
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
  const isAuthenticated = useIsAuthenticated()
  // Single QueryClient instance for the app's lifetime — useState (not a
  // module-level const) so Fast Refresh during dev doesn't share a stale
  // client across reloads, same rationale as React Query's own docs.
  const [queryClient] = useState(() => new QueryClient())
  // Same useState-not-module-level rationale as queryClient above.
  const [persister] = useState(() => createAsyncStoragePersister({ storage: AsyncStorage }))

  // NOTE: Push notifications require a development build installed on a
  // physical device (iOS: Apple Developer account required, Android: free
  // via EAS). Cannot be tested in Expo Go or without a build. Called
  // unconditionally (Rules of Hooks) — both hooks internally no-op until
  // there's something to do (unauthenticated / no tap yet).
  usePushNotifications()
  useNotificationResponseListener()

  // Fonts and SecureStore rehydration must both finish before any screen
  // renders — rendering early would either flash unstyled text (no custom
  // font yet) or briefly show the login screen for an already-authenticated
  // user (accessToken not yet read back from SecureStore, see
  // stores/authStore.ts's hasHydrated flag).
  if (!fontsLoaded || !hasHydrated) {
    return <View className="flex-1 bg-background" />
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <PersistQueryClientProvider
          client={queryClient}
          persistOptions={{
            persister,
            dehydrateOptions: { shouldDehydrateQuery: shouldPersistQuery },
          }}
        >
          <View className="flex-1 bg-background">
            <Stack
              screenOptions={{
                headerShown: false,
                contentStyle: { backgroundColor: "#0a0a0a" },
              }}
            >
              <Stack.Protected guard={isAuthenticated}>
                <Stack.Screen name="(tabs)" />
                <Stack.Screen
                  name="settings"
                  options={{
                    headerShown: true,
                    presentation: "modal",
                    title: "Settings",
                    headerStyle: { backgroundColor: "#0a0a0a" },
                    headerTintColor: "#fafafa",
                  }}
                />
                <Stack.Screen
                  name="driver/[id]"
                  options={{
                    headerShown: true,
                    title: "Driver",
                    headerStyle: { backgroundColor: "#0a0a0a" },
                    headerTintColor: "#fafafa",
                  }}
                />
                <Stack.Screen
                  name="simulator"
                  options={{
                    headerShown: true,
                    title: "Strategy Simulator",
                    headerStyle: { backgroundColor: "#0a0a0a" },
                    headerTintColor: "#fafafa",
                  }}
                />
              </Stack.Protected>
              <Stack.Protected guard={!isAuthenticated}>
                <Stack.Screen name="(auth)" />
              </Stack.Protected>
            </Stack>
          </View>
        </PersistQueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}
