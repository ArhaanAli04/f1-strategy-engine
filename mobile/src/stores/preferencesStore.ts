import AsyncStorage from "@react-native-async-storage/async-storage"
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

interface PreferencesState {
  hapticsEnabled: boolean
  setHapticsEnabled: (enabled: boolean) => void
}

// Device-only preferences, kept on the phone rather than on the account: a
// vibration setting belongs to the device in hand, and the backend has no
// reason to know it. AsyncStorage, not SecureStore — nothing here is secret.
// Until the stored value loads (a few ms after launch) the default applies.
export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      hapticsEnabled: true,
      setHapticsEnabled: (enabled) => set({ hapticsEnabled: enabled }),
    }),
    {
      name: "f1-preferences",
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({ hapticsEnabled: state.hapticsEnabled }),
    },
  ),
)
