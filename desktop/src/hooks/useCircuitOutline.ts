import { useQuery } from "@tanstack/react-query"
import * as circuitApi from "@/api/circuit"

// Circuit outlines never change once extracted (scripts/extract_circuit_outlines.py
// runs once per circuit) — a long staleTime avoids refetching on every
// CircuitMapPanel mount.
const CIRCUIT_OUTLINE_STALE_TIME_MS = 24 * 60 * 60 * 1000

export function useCircuitOutline(circuitId: string | null) {
  return useQuery({
    queryKey: ["circuit", "outline", circuitId],
    queryFn: () => circuitApi.getCircuitOutline(circuitId as string),
    enabled: Boolean(circuitId),
    staleTime: CIRCUIT_OUTLINE_STALE_TIME_MS,
    retry: false,
    // 404 (not yet extracted for this circuit — see CLAUDE.md's
    // extract_circuit_outlines.py deferred-wiring note) is a normal state
    // CircuitMapPanel renders directly, not a global error toast.
    meta: { silentOn404: true },
  })
}
