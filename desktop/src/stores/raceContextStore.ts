import { create } from "zustand"
import { persist } from "zustand/middleware"

// The user's own race-context choices, saved across restarts (demo
// deployment Day 6b). The session itself is NOT stored here: every page gets
// it from useRaceSession, which picks the live race or the most recent
// completed one automatically, like web. A session id here is only an
// optional override, typed on the Dashboard.
//
// Each Tauri window (main, overlay) has its own JS runtime and its own
// instance of this store. Both rehydrate from the same localStorage on open
// (same app origin); useRaceContextBridge keeps them in sync while open.
interface RaceContextState {
  // A session id typed on the Dashboard, used instead of the automatic one.
  sessionOverride: string | null
  // "Your driver": tray/undercut notifications, the overlay highlight, and
  // the Driver Analytics page's selected driver.
  driverId: string | null
  setContext: (sessionOverride: string | null, driverId: string | null) => void
}

export const useRaceContextStore = create<RaceContextState>()(
  persist(
    (set) => ({
      sessionOverride: null,
      driverId: null,
      setContext: (sessionOverride, driverId) => set({ sessionOverride, driverId }),
    }),
    {
      name: "f1-race-context",
      partialize: (state) => ({ sessionOverride: state.sessionOverride, driverId: state.driverId }),
    },
  ),
)
