import { apiClient } from "./client"
import type { DriverAnalysisResponse, DriverResponse, LapDataResponse, PaginatedResponse } from "@/types"

// Returns the full DriverResponse (with nested contracts), not the lighter
// DriverListResponse — matches backend/apis/v1/drivers.py's list_drivers.
export async function listDrivers(): Promise<DriverResponse[]> {
  const { data } = await apiClient.get<DriverResponse[]>("/drivers")
  return data
}

export async function getDriverAnalysis(
  driverId: string,
  sessionId: string,
): Promise<DriverAnalysisResponse> {
  const { data } = await apiClient.get<DriverAnalysisResponse>(`/drivers/${driverId}/analysis`, {
    params: { session_id: sessionId },
  })
  return data
}

export interface GetDriverLapsParams {
  page?: number
  page_size?: number
}

export async function getDriverLaps(
  driverId: string,
  sessionId: string,
  params: GetDriverLapsParams = {},
): Promise<PaginatedResponse<LapDataResponse>> {
  const { data } = await apiClient.get<PaginatedResponse<LapDataResponse>>(
    `/drivers/${driverId}/laps`,
    { params: { session_id: sessionId, ...params } },
  )
  return data
}
