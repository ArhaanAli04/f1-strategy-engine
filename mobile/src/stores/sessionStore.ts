import { create } from "zustand"

// UI-only selection state for the currently viewed race weekend — e.g.
// syncing the timing tower and circuit map to the same driver (see
// CLAUDE.md's Planned Feature: Live Circuit Map, Days 25-28).
interface SessionState {
  selectedSessionId: string | null
  selectedDriverId: string | null
  setSelectedSession: (sessionId: string | null) => void
  setSelectedDriver: (driverId: string | null) => void
}

export const useSessionStore = create<SessionState>((set) => ({
  selectedSessionId: null,
  selectedDriverId: null,
  setSelectedSession: (sessionId) => set({ selectedSessionId: sessionId }),
  setSelectedDriver: (driverId) => set({ selectedDriverId: driverId }),
}))
