import type { ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import * as strategyApi from "@/api/strategy"
import { useSimulationResult } from "@/hooks/useStrategy"
import type { SimulateTaskStatusResponse } from "@/types"

vi.mock("@/api/strategy", () => ({ getSimulationResult: vi.fn() }))

const PENDING: SimulateTaskStatusResponse = {
  task_id: "task-1",
  status: "PENDING",
  result: null,
  error: null,
}

function render(taskId: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return renderHook(({ id }) => useSimulationResult(id), {
    wrapper,
    initialProps: { id: taskId },
  })
}

// A worker started on demand takes about a minute from cold (demo deployment
// Day 6): the hook flags slowStart after 10 s and only gives up at 3 minutes.
describe("useSimulationResult — waiting for a worker started on demand", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(strategyApi.getSimulationResult).mockResolvedValue(PENDING)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("flags slowStart after 10 s and timedOut only after 3 minutes", async () => {
    const { result } = render("task-1")
    expect(result.current.slowStart).toBe(false)

    await act(() => vi.advanceTimersByTimeAsync(9_000))
    expect(result.current.slowStart).toBe(false)
    await act(() => vi.advanceTimersByTimeAsync(1_500))
    expect(result.current.slowStart).toBe(true)
    expect(result.current.timedOut).toBe(false)

    await act(() => vi.advanceTimersByTimeAsync(60_000)) // the old 60 s limit, passed
    expect(result.current.timedOut).toBe(false)
    await act(() => vi.advanceTimersByTimeAsync(110_000))
    expect(result.current.timedOut).toBe(true)
  })

  it("clears both flags when there is no task", async () => {
    const { result, rerender } = render("task-1")
    await act(() => vi.advanceTimersByTimeAsync(11_000))
    expect(result.current.slowStart).toBe(true)

    rerender({ id: null })

    expect(result.current.slowStart).toBe(false)
    expect(result.current.timedOut).toBe(false)
  })
})
