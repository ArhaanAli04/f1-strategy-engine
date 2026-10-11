import { apiClient } from "./client"
import type {
  PaginatedResponse,
  RaceListResponse,
  RaceResponse,
  SessionResponse,
  UpcomingRaceResponse,
} from "@/types"

export interface ListRacesParams {
  season?: number
  round?: number
  page?: number
  page_size?: number
}

// GET /races/current — registered ahead of /races/{race_id} on the backend
// so a literal "current" segment never gets swallowed by the UUID path param.
export async function getCurrentRace(): Promise<RaceResponse> {
  const { data } = await apiClient.get<RaceResponse>("/races/current")
  return data
}

export async function listRaces(
  params: ListRacesParams = {},
): Promise<PaginatedResponse<RaceListResponse>> {
  const { data } = await apiClient.get<PaginatedResponse<RaceListResponse>>("/races", { params })
  return data
}

export async function getRace(raceId: string): Promise<RaceResponse> {
  const { data } = await apiClient.get<RaceResponse>(`/races/${raceId}`)
  return data
}

export async function getSession(raceId: string, sessionId: string): Promise<SessionResponse> {
  const { data } = await apiClient.get<SessionResponse>(
    `/races/${raceId}/sessions/${sessionId}`,
  )
  return data
}

// GET /races/upcoming — registered ahead of /races/{race_id} on the backend,
// same reason as getCurrentRace above.
export async function getUpcomingRace(): Promise<UpcomingRaceResponse> {
  const { data } = await apiClient.get<UpcomingRaceResponse>("/races/upcoming")
  return data
}

// GET /races/session/{session_id} — registered ahead of /races/{race_id} on
// the backend, same reason as getCurrentRace above.
export async function getRaceBySession(sessionId: string): Promise<RaceResponse> {
  const { data } = await apiClient.get<RaceResponse>(`/races/session/${sessionId}`)
  return data
}
