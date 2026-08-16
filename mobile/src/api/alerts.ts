import { apiClient } from "./client"
import type { AlertResponse, SubscriptionCreate, SubscriptionResponse } from "@/types"

export async function getAlerts(unread = false): Promise<AlertResponse[]> {
  const { data } = await apiClient.get<AlertResponse[]>("/alerts", { params: { unread } })
  return data
}

export async function markAlertRead(alertId: string): Promise<AlertResponse> {
  const { data } = await apiClient.put<AlertResponse>(`/alerts/${alertId}/read`)
  return data
}

export async function getSubscriptions(): Promise<SubscriptionResponse> {
  const { data } = await apiClient.get<SubscriptionResponse>("/alerts/subscriptions")
  return data
}

export async function updateSubscriptions(
  payload: SubscriptionCreate,
): Promise<SubscriptionResponse> {
  const { data } = await apiClient.put<SubscriptionResponse>("/alerts/subscriptions", payload)
  return data
}
