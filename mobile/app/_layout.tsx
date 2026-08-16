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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Stack } from "expo-router"
import { useState } from "react"
import { View } from "react-native"
import { GestureHandlerRootView } from "react-native-gesture-handler"
import { SafeAreaProvider } from "react-native-safe-area-context"
import { useNotificationResponseListener } from "@/hooks/useNotificationResponseListener"
import { usePushNotifications } from "@/hooks/usePushNotifications"
import { useAuthStore, useIsAuthenticated } from "@/stores/authStore"

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
        <QueryClientProvider client={queryClient}>
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
              </Stack.Protected>
              <Stack.Protected guard={!isAuthenticated}>
                <Stack.Screen name="(auth)" />
              </Stack.Protected>
            </Stack>
          </View>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}
