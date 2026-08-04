import { apiClient } from "./client"
import type { LapHistoryBucket, LiveTelemetryResponse, SessionGapsResponse } from "@/types"

export async function getLiveLap(
  sessionId: string,
  driverId: string,
): Promise<LiveTelemetryResponse> {
  const { data } = await apiClient.get<LiveTelemetryResponse>(
    `/telemetry/${sessionId}/${driverId}/live`,
  )
  return data
}

export async function getLapHistory(
  sessionId: string,
  driverId: string,
  laps = 5,
): Promise<LapHistoryBucket[]> {
  const { data } = await apiClient.get<LapHistoryBucket[]>(
    `/telemetry/${sessionId}/${driverId}/history`,
    { params: { laps } },
  )
  return data
}

export async function getSessionGaps(sessionId: string): Promise<SessionGapsResponse> {
  const { data } = await apiClient.get<SessionGapsResponse>(`/telemetry/${sessionId}/gaps`)
  return data
}
