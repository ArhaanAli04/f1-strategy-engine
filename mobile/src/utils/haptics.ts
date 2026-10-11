import * as Haptics from "expo-haptics"

// The app's haptic vocabulary (mobile only, 2026-10-07). Screens call these
// named actions instead of expo-haptics directly, so the same moment feels the
// same everywhere and a Settings on/off switch can be added here in one place.
// Used only for meaningful moments (a choice, a state change, a result), not
// every tap; bottom tabs, pull to refresh and Switches are left alone (iOS
// already gives the last two their own). Each call is fire-and-forget: a
// device without haptics simply does nothing.

/** A choice among options: a segment, a filter chip, a driver, a checkbox. */
export function selectionTick(): void {
  void Haptics.selectionAsync()
}

/** Starting something deliberate: entering selection mode, running a simulation. */
export function confirm(): void {
  void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
}

/** A gesture crossing its action point, such as a swipe that will mark read. */
export function threshold(): void {
  void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
}

/** A requested action finished as intended. */
export function success(): void {
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
}

/** Finished, but not entirely: some items failed, a limit was reached. */
export function warning(): void {
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
}

/** The action failed. */
export function error(): void {
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)
}
