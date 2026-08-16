import { useQuery } from "@tanstack/react-query"
import * as circuitApi from "@/api/circuit"

// Hand-written — mirrors web/src/hooks/useCircuitOutline.ts exactly.
const CIRCUIT_OUTLINE_STALE_TIME_MS = 24 * 60 * 60 * 1000

export function useCircuitOutline(circuitId: string | null) {
  return useQuery({
    queryKey: ["circuit", "outline", circuitId],
    queryFn: () => circuitApi.getCircuitOutline(circuitId as string),
    enabled: Boolean(circuitId),
    staleTime: CIRCUIT_OUTLINE_STALE_TIME_MS,
    retry: false,
  })
}
