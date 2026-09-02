import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query"
import * as demoApi from "@/api/demo"
import type { ReplayStatusResponse } from "@/types"

const CURATED_SESSIONS_QUERY_KEY = ["demo", "sessions"] as const
const REPLAY_AVAILABLE_QUERY_KEY = ["demo", "replay", "available"] as const
const REPLAY_STATUS_QUERY_KEY = ["demo", "replay", "status"] as const

// While a replay is running the status endpoint is the source of truth for
// "is it still going / has it finished" — poll it briskly. When nothing is
// running a slower poll still picks up a replay started or stopped elsewhere.
const STATUS_POLL_RUNNING_MS = 5_000
const STATUS_POLL_IDLE_MS = 20_000

export function useCuratedSessions() {
  return useQuery({
    queryKey: CURATED_SESSIONS_QUERY_KEY,
    queryFn: demoApi.getCuratedSessions,
    // Hardcoded on the backend — never changes within a session.
    staleTime: Infinity,
  })
}

// Re-poll so a transient "unavailable" reading (e.g. a stale gaps key) can
// self-correct instead of hiding the whole panel for the rest of the session.
const AVAILABLE_POLL_MS = 30_000

export function useReplayAvailable() {
  return useQuery({
    queryKey: REPLAY_AVAILABLE_QUERY_KEY,
    queryFn: demoApi.getReplayAvailable,
    staleTime: 30_000,
    refetchInterval: AVAILABLE_POLL_MS,
  })
}

export function useReplayStatus() {
  return useQuery({
    queryKey: REPLAY_STATUS_QUERY_KEY,
    queryFn: demoApi.getReplayStatus,
    refetchInterval: (query: Query<ReplayStatusResponse>) =>
      query.state.data?.running ? STATUS_POLL_RUNNING_MS : STATUS_POLL_IDLE_MS,
  })
}

export function useStartReplay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sessionId: string) => demoApi.startReplay(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: REPLAY_STATUS_QUERY_KEY })
      void queryClient.invalidateQueries({ queryKey: REPLAY_AVAILABLE_QUERY_KEY })
    },
  })
}

export function useStopReplay() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => demoApi.stopReplay(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: REPLAY_STATUS_QUERY_KEY })
      void queryClient.invalidateQueries({ queryKey: REPLAY_AVAILABLE_QUERY_KEY })
    },
  })
}
