import { apiClient } from "./client"
import type { CircuitOutlineResponse } from "@/types"

export async function getCircuitOutline(circuitId: string): Promise<CircuitOutlineResponse> {
  const { data } = await apiClient.get<CircuitOutlineResponse>(`/circuits/${circuitId}/outline`)
  return data
}
