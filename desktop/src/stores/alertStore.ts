import { create } from "zustand"
import type { AlertResponse } from "@/types"

function countUnread(alerts: AlertResponse[]): number {
  return alerts.filter((a) => a.read_at === null).length
}

interface AlertState {
  alerts: AlertResponse[]
  unreadCount: number
  setAlerts: (alerts: AlertResponse[]) => void
  addAlert: (alert: AlertResponse) => void
  markRead: (alertId: string) => void
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts: [],
  unreadCount: 0,
  setAlerts: (alerts) => set({ alerts, unreadCount: countUnread(alerts) }),
  addAlert: (alert) =>
    set((state) => {
      const alerts = [alert, ...state.alerts]
      return { alerts, unreadCount: countUnread(alerts) }
    }),
  markRead: (alertId) =>
    set((state) => {
      const alerts = state.alerts.map((a) =>
        a.id === alertId ? { ...a, read_at: new Date().toISOString() } : a,
      )
      return { alerts, unreadCount: countUnread(alerts) }
    }),
}))
