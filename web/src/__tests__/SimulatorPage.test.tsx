import { useState } from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLastIngestedSession } from "@/hooks/useLastIngestedSession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSimulateStrategy, useSimulationResult } from "@/hooks/useStrategy"
import { SimulatorPage } from "@/pages/SimulatorPage"
import { useSessionStore } from "@/stores/sessionStore"
import type { DriverResponse, SimulateStrategyRequest, SimulateTaskStatusResponse } from "@/types"

// Item 12 (docs/day-deferred-fixes-session2-handoff.md): the initial
// POST /simulate rejection (validate_current_lap's 404/422) and the async
// task-FAILURE path both previously told the user nothing beyond a bare
// "Simulation failed." — these tests cover the fix, not the rest of the
// page (PitWindowCard.test.tsx/ReplaySelectorPanel.test.tsx already
// establish this codebase's hook-mocking convention, followed here).
vi.mock("@/hooks/useCurrentRace", () => ({ useCurrentRace: vi.fn() }))
vi.mock("@/hooks/useDriverLaps", () => ({ useDriverLaps: vi.fn() }))
vi.mock("@/hooks/useDrivers", () => ({ useDrivers: vi.fn() }))
vi.mock("@/hooks/useLastIngestedSession", () => ({ useLastIngestedSession: vi.fn() }))
vi.mock("@/hooks/useSessionGaps", () => ({ useSessionGaps: vi.fn() }))
vi.mock("@/hooks/useStrategy", () => ({
  useSimulateStrategy: vi.fn(),
  useSimulationResult: vi.fn(),
}))
vi.mock("@/stores/sessionStore", () => ({ useSessionStore: vi.fn() }))

const DRIVER: DriverResponse = {
  id: "driver-1",
  code: "VER",
  full_name: "Max Verstappen",
  nationality: "NED",
  date_of_birth: null,
  contracts: [
    {
      season: 2026,
      team_id: "team-1",
      team: { id: "team-1", name: "Red Bull Racing", color_hex: "#3671C6" },
    },
  ],
} as unknown as DriverResponse

function baseSetup() {
  // No explicit parameter type here — letting it infer from useSessionStore's
  // own signature (rather than a narrower hand-written selector-state shape)
  // is what makes this assignable to mockImplementation's expected callback.
  vi.mocked(useSessionStore).mockImplementation((selector) =>
    selector({
      selectedSessionId: null,
      selectedDriverId: null,
      setSelectedSession: vi.fn(),
      setSelectedDriver: vi.fn(),
    }),
  )
  vi.mocked(useCurrentRace).mockReturnValue({
    data: undefined,
  } as unknown as ReturnType<typeof useCurrentRace>)
  vi.mocked(useSessionGaps).mockReturnValue({
    data: undefined,
  } as unknown as ReturnType<typeof useSessionGaps>)
  vi.mocked(useLastIngestedSession).mockReturnValue({
    data: {
      session_id: "session-1",
      event_name: "Belgian Grand Prix",
      circuit_name: "Spa",
      season: 2026,
      round_number: 10,
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useLastIngestedSession>)
  vi.mocked(useDrivers).mockReturnValue({
    data: [DRIVER],
  } as unknown as ReturnType<typeof useDrivers>)
  vi.mocked(useDriverLaps).mockReturnValue({
    data: { items: [] },
  } as unknown as ReturnType<typeof useDriverLaps>)
}

// A minimal, real-React-state stand-in for useMutation's shape — reactive
// (unlike a static vi.fn().mockReturnValue(...)) so the component's
// isError/error-driven JSX actually re-renders after mutateAsync rejects,
// the same way the real hook would.
function useFakeSimulateStrategy(mutateAsync: (payload: SimulateStrategyRequest) => Promise<{
  task_id: string
  status: string
}>) {
  const [state, setState] = useState<{ isError: boolean; error: unknown }>({
    isError: false,
    error: null,
  })
  return {
    mutateAsync: async (payload: SimulateStrategyRequest) => {
      try {
        const result = await mutateAsync(payload)
        setState({ isError: false, error: null })
        return result
      } catch (error) {
        setState({ isError: true, error })
        throw error
      }
    },
    isError: state.isError,
    error: state.error,
    reset: () => setState({ isError: false, error: null }),
  } as unknown as ReturnType<typeof useSimulateStrategy>
}

async function goToDesignStrategyStep() {
  render(<SimulatorPage />)
  fireEvent.click(screen.getByRole("combobox", { name: "Driver" }))
  fireEvent.click(await screen.findByText("VER — Max Verstappen"))
  fireEvent.click(screen.getByRole("button", { name: "Next: Design Strategy" }))
  await screen.findByRole("heading", { name: "Design Strategy" })
}

describe("SimulatorPage — error surfacing (item 12)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("surfaces a validate_current_lap rejection and stays on Design Strategy, not stranded on a spinner", async () => {
    baseSetup()
    const axiosLikeError = Object.assign(new Error("current_lap 68 exceeds session progress"), {
      isAxiosError: true,
      response: {
        data: {
          error: "VALIDATION_ERROR",
          message: "current_lap 68 exceeds session progress by more than one lap",
          detail: null,
        },
      },
    })
    const rejectingMutateAsync = vi.fn().mockRejectedValue(axiosLikeError)
    vi.mocked(useSimulateStrategy).mockImplementation(() =>
      useFakeSimulateStrategy(rejectingMutateAsync),
    )
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }))

    expect(
      await screen.findByText("current_lap 68 exceeds session progress by more than one lap"),
    ).toBeInTheDocument()
    // Still on step 2 — no spinner card, no unhandled-rejection stranding.
    expect(screen.getByRole("heading", { name: "Design Strategy" })).toBeInTheDocument()
    expect(rejectingMutateAsync).toHaveBeenCalledTimes(1)
  })

  it("shows the task's own error message, not a fixed string, when the async simulation FAILS", async () => {
    baseSetup()
    const resolvingMutateAsync = vi.fn().mockResolvedValue({ task_id: "task-1", status: "PENDING" })
    vi.mocked(useSimulateStrategy).mockImplementation(() =>
      useFakeSimulateStrategy(resolvingMutateAsync),
    )
    const failedResult: SimulateTaskStatusResponse = {
      task_id: "task-1",
      status: "FAILURE",
      result: null,
      error: "Simulation failed due to an unexpected error.",
    }
    vi.mocked(useSimulationResult).mockReturnValue({
      data: failedResult,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }))

    await waitFor(() => expect(resolvingMutateAsync).toHaveBeenCalledTimes(1))
    expect(
      await screen.findByText("Simulation failed due to an unexpected error."),
    ).toBeInTheDocument()
    expect(screen.queryByText("Simulation failed.")).not.toBeInTheDocument()
  })
})
