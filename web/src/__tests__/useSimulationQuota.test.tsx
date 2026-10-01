import type { ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import * as strategyApi from "@/api/strategy"
import { useSimulateStrategy, useSimulationQuota } from "@/hooks/useStrategy"
import type { SimulateStrategyRequest, SimulationQuotaResponse } from "@/types"

vi.mock("@/api/strategy", () => ({
  getSimulationQuota: vi.fn(),
  simulateStrategy: vi.fn(),
}))

function quota(used: number): SimulationQuotaResponse {
  return {
    day: "2026-10-01",
    resets_at: "2026-10-02T00:00:00Z",
    user_quota: { limit: 5, used, remaining: 5 - used },
    global_quota: { limit: 15, used, remaining: 15 - used },
  }
}

const PAYLOAD = {
  driver_id: "driver-1",
  current_lap: 12,
  current_compound: "MEDIUM",
  current_tyre_age: 12,
  remaining_laps: 30,
} as SimulateStrategyRequest

// Both hooks share one QueryClient, as they do on the Simulator page.
function renderBoth() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return renderHook(
    () => ({ quota: useSimulationQuota(), simulate: useSimulateStrategy("session-1") }),
    { wrapper },
  )
}

describe("useSimulationQuota", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("refetches the quota after a run is accepted", async () => {
    vi.mocked(strategyApi.getSimulationQuota)
      .mockResolvedValueOnce(quota(0))
      .mockResolvedValueOnce(quota(1))
    vi.mocked(strategyApi.simulateStrategy).mockResolvedValue({ task_id: "t", status: "PENDING" })
    const { result } = renderBoth()
    await waitFor(() => expect(result.current.quota.data?.user_quota.used).toBe(0))

    await act(() => result.current.simulate.mutateAsync(PAYLOAD))

    await waitFor(() => expect(result.current.quota.data?.user_quota.used).toBe(1))
  })

  it("refetches the quota after a run is refused, since others share the demo limit", async () => {
    vi.mocked(strategyApi.getSimulationQuota)
      .mockResolvedValueOnce(quota(0))
      .mockResolvedValueOnce(quota(15))
    vi.mocked(strategyApi.simulateStrategy).mockRejectedValue(new Error("429"))
    const { result } = renderBoth()
    await waitFor(() => expect(result.current.quota.data?.global_quota.used).toBe(0))

    await act(async () => {
      await expect(result.current.simulate.mutateAsync(PAYLOAD)).rejects.toThrow("429")
    })

    await waitFor(() => expect(result.current.quota.data?.global_quota.remaining).toBe(0))
  })
})
