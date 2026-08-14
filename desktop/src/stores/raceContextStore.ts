import { create } from "zustand"

// Not persisted — this is live session state, not auth. Each Tauri window
// (main, overlay) has its own separate JS runtime and its own instance of
// this store; useRaceContextBridge (hooks/useRaceContextBridge.ts) keeps
// them in sync via Tauri's event bus, since there is no shared memory or
// localStorage bridge between windows to rely on instead.
interface RaceContextState {
  sessionId: string | null
  driverId: string | null
  setContext: (sessionId: string | null, driverId: string | null) => void
}

export const useRaceContextStore = create<RaceContextState>((set) => ({
  sessionId: null,
  driverId: null,
  setContext: (sessionId, driverId) => set({ sessionId, driverId }),
}))
