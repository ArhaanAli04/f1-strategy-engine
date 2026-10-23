import * as Haptics from "expo-haptics"
import { usePreferencesStore } from "@/stores/preferencesStore"

// The app's haptic vocabulary (mobile only, 2026-10-07). Screens call these
// named actions instead of expo-haptics directly, so the same moment feels the
// same everywhere, and the Settings "Haptic feedback" switch silences all of
// them here in one place. Used only for meaningful moments (a choice, a state
// change, a result), not every tap; bottom tabs, pull to refresh and Switches
// are left alone (iOS already gives the last two their own, which the setting
// doesn't control). Each call is fire-and-forget: a device without haptics
// simply does nothing.

function hapticsEnabled(): boolean {
  return usePreferencesStore.getState().hapticsEnabled
}

/** A choice among options: a segment, a filter chip, a driver, a checkbox. */
export function selectionTick(): void {
  if (!hapticsEnabled()) return
  void Haptics.selectionAsync()
}

/** Starting something deliberate: entering selection mode, running a simulation. */
export function confirm(): void {
  if (!hapticsEnabled()) return
  void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
}

/** A gesture crossing its action point, such as a swipe that will mark read. */
export function threshold(): void {
  if (!hapticsEnabled()) return
  void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
}

/** A requested action finished as intended. */
export function success(): void {
  if (!hapticsEnabled()) return
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
}

/** Finished, but not entirely: some items failed, a limit was reached. */
export function warning(): void {
  if (!hapticsEnabled()) return
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
}

/** The action failed. */
export function error(): void {
  if (!hapticsEnabled()) return
  void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)
}
