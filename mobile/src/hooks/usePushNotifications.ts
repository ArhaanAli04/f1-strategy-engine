// NOTE: Push notifications require a development build installed on a
// physical device (iOS: Apple Developer account required, Android: free
// via EAS). Cannot be tested in Expo Go or without a build.
import { useMutation } from "@tanstack/react-query"
import * as Notifications from "expo-notifications"
import { useEffect, useRef } from "react"
import { Platform } from "react-native"
import * as authApi from "@/api/auth"
import { useIsAuthenticated } from "@/stores/authStore"

async function registerForPushNotificationsAsync(): Promise<string | null> {
  const { status: existingStatus } = await Notifications.getPermissionsAsync()
  let finalStatus = existingStatus
  if (existingStatus !== "granted") {
    const { status } = await Notifications.requestPermissionsAsync()
    finalStatus = status
  }
  if (finalStatus !== "granted") return null

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "default",
      importance: Notifications.AndroidImportance.DEFAULT,
    })
  }

  try {
    // projectId defaults to Constants.expoConfig.extra.eas.projectId, set
    // automatically once `eas init`/`eas build:configure` has run — not yet
    // done on this project (see CLAUDE.md's External Services checklist,
    // Firebase/FCM row). Without it this call rejects; caught here since
    // there's no physical device build to actually exercise this path
    // against right now regardless.
    const { data: token } = await Notifications.getExpoPushTokenAsync()
    return token
  } catch {
    return null
  }
}

// Registers the device's Expo push token with the backend
// (PUT /auth/fcm-token, which already accepts Expo tokens — see CLAUDE.md's
// Day 31 spec note) once per authenticated session. Fires the moment
// isAuthenticated flips true, which covers "on first login" naturally
// (that's exactly when this transitions false -> true), and is a no-op
// thereafter until the user logs out and back in.
export function usePushNotifications(): void {
  const isAuthenticated = useIsAuthenticated()
  const registeredRef = useRef(false)

  const registerTokenMutation = useMutation({
    mutationFn: (fcmToken: string) => authApi.updateFcmToken({ fcm_token: fcmToken }),
  })

  useEffect(() => {
    if (!isAuthenticated) {
      registeredRef.current = false
      return
    }
    if (registeredRef.current) return
    registeredRef.current = true

    registerForPushNotificationsAsync().then((token) => {
      if (token) registerTokenMutation.mutate(token)
    })
    // registerTokenMutation is a fresh object every render (useMutation) —
    // isAuthenticated is the only real change signal this effect should
    // react to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated])
}
