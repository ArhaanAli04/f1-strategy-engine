import { apiClient } from "./client"
import type {
  CuratedSessionsResponse,
  ReplayAvailableResponse,
  ReplayStartResponse,
  ReplayStatusResponse,
  ReplayStopResponse,
} from "@/types"

export async function getCuratedSessions(): Promise<CuratedSessionsResponse> {
  const { data } = await apiClient.get<CuratedSessionsResponse>("/demo/sessions")
  return data
}

export async function getReplayAvailable(): Promise<ReplayAvailableResponse> {
  const { data } = await apiClient.get<ReplayAvailableResponse>("/demo/replay/available")
  return data
}

export async function getReplayStatus(): Promise<ReplayStatusResponse> {
  const { data } = await apiClient.get<ReplayStatusResponse>("/demo/replay/status")
  return data
}

export async function startReplay(sessionId: string): Promise<ReplayStartResponse> {
  const { data } = await apiClient.post<ReplayStartResponse>("/demo/replay/start", {
    session_id: sessionId,
  })
  return data
}

export async function stopReplay(): Promise<ReplayStopResponse> {
  const { data } = await apiClient.post<ReplayStopResponse>("/demo/replay/stop")
  return data
}
