import { create } from "zustand"

// Page-local UI selection for LiveRacePage — which driver's row is
// highlighted, syncing the timing tower / sector heatmap / lap chart /
// undercut panel to the same driver while browsing. Deliberately separate
// from raceContextStore: that store means "my own driver" for tray/
// notification/overlay purposes and is shared cross-window — conflating the
// two would mean clicking a row here while browsing silently changes which
// driver the undercut-notification system watches for threats.
interface LiveRaceSelectionState {
  selectedDriverId: string | null
  setSelectedDriver: (driverId: string | null) => void
}

export const useLiveRaceSelectionStore = create<LiveRaceSelectionState>((set) => ({
  selectedDriverId: null,
  setSelectedDriver: (driverId) => set({ selectedDriverId: driverId }),
}))
