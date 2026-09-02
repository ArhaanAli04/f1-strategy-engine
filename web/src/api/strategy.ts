import { apiClient } from "./client"
import type {
  LastIngestedSessionResponse,
  PitWindowResponse,
  SimulateStrategyRequest,
  SimulateTaskAccepted,
  SimulateTaskStatusResponse,
  StrategyOverviewResponse,
  StrategyPredictionHistoryResponse,
  UndercutThreatResponse,
} from "@/types"

// Returns a list — backend/apis/v1/strategy.py's get_pit_window responds
// with list[PitWindowResponse], not a single window.
export async function getPitWindow(
  sessionId: string,
  driverId: string,
): Promise<PitWindowResponse[]> {
  const { data } = await apiClient.get<PitWindowResponse[]>(
    `/strategy/${sessionId}/${driverId}/pit-window`,
  )
  return data
}

export async function getUndercut(
  sessionId: string,
  driverId: string,
  target: string,
): Promise<UndercutThreatResponse> {
  const { data } = await apiClient.get<UndercutThreatResponse>(
    `/strategy/${sessionId}/${driverId}/undercut`,
    { params: { target } },
  )
  return data
}

export async function getStrategyOverview(sessionId: string): Promise<StrategyOverviewResponse> {
  const { data } = await apiClient.get<StrategyOverviewResponse>(`/strategy/${sessionId}/overview`)
  return data
}

export async function getStrategyHistory(
  sessionId: string,
  driverId: string,
): Promise<StrategyPredictionHistoryResponse> {
  const { data } = await apiClient.get<StrategyPredictionHistoryResponse>(
    `/strategy/${sessionId}/${driverId}/history`,
  )
  return data
}

export async function simulateStrategy(
  sessionId: string,
  payload: SimulateStrategyRequest,
): Promise<SimulateTaskAccepted> {
  const { data } = await apiClient.post<SimulateTaskAccepted>(
    `/strategy/${sessionId}/simulate`,
    payload,
  )
  return data
}

// Unauthenticated on the backend — task_id is an unguessable Celery UUID.
export async function getSimulationResult(taskId: string): Promise<SimulateTaskStatusResponse> {
  const { data } = await apiClient.get<SimulateTaskStatusResponse>(`/strategy/simulate/${taskId}`)
  return data
}

export async function getLastIngestedSession(): Promise<LastIngestedSessionResponse> {
  const { data } = await apiClient.get<LastIngestedSessionResponse>(
    "/strategy/last-ingested-session",
  )
  return data
}
