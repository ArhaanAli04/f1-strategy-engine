// NOTE: Push notifications require a development build installed on a
// physical device (iOS: Apple Developer account required, Android: free
// via EAS). Cannot be tested in Expo Go or without a build.
//
// Runs once at module load, before any screen mounts — imported for its
// side effect at the top of app/_layout.tsx. Governs foreground behavior
// only (background/killed-state presentation is OS-controlled).
import * as Notifications from "expo-notifications"

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    // shouldShowAlert is deprecated in this SDK — shouldShowBanner/
    // shouldShowList replaced it (split iOS's old single "alert" concept
    // into where the notification is allowed to appear).
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
})
