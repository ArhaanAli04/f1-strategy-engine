// NOTE: Push notifications require a development build installed on a
// physical device (iOS: Apple Developer account required, Android: free
// via EAS). Cannot be tested in Expo Go or without a build.
import * as Notifications from "expo-notifications"
import { router, type Href } from "expo-router"
import { useEffect } from "react"

// Reads a `path` field off the tapped notification's data payload and
// deep-links there via Expo Router, whether the app was backgrounded or
// fully killed when the tap happened (addNotificationResponseReceivedListener
// covers both — Expo Router's own docs note it also replays the response
// that launched a killed app on first mount). Backend-side wiring to
// actually include a `path` in a delivered notification's payload is a
// future day's work (alert_service.py's delivery path — see CLAUDE.md's
// alert pipeline notes); this listener is ready for it regardless.
export function useNotificationResponseListener(): void {
  useEffect(() => {
    const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const path = response.notification.request.content.data?.path
      if (typeof path === "string") {
        router.push(path as Href)
      }
    })
    return () => subscription.remove()
  }, [])
}
