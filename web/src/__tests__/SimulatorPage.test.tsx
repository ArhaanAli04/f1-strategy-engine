import { useState } from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLastIngestedSession } from "@/hooks/useLastIngestedSession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSimulateStrategy, useSimulationQuota, useSimulationResult } from "@/hooks/useStrategy"
import { SimulatorPage } from "@/pages/SimulatorPage"
import { useSessionStore } from "@/stores/sessionStore"
import type {
  DriverResponse,
  QuotaCounter,
  SimulateStrategyRequest,
  SimulateTaskStatusResponse,
} from "@/types"

// Item 12 (docs/internal/day-deferred-fixes-session2-handoff.md): the initial
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
  useSimulationQuota: vi.fn(),
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
  mockQuota({ limit: null, used: 0, remaining: null }, { limit: null, used: 0, remaining: null })
}

function mockQuota(user: QuotaCounter, demo: QuotaCounter) {
  vi.mocked(useSimulationQuota).mockReturnValue({
    data: {
      day: "2026-10-01",
      resets_at: "2026-10-02T00:00:00Z",
      user_quota: user,
      global_quota: demo,
    },
  } as unknown as ReturnType<typeof useSimulationQuota>)
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

// What-If Simulator multi-scenario rebuild, Checkpoint 4 — see
// docs/internal/core-feature-rebuild-whatif-simulator.md. Single Plan mode's own
// existing payload shape is already covered by the two tests above (both
// stay on the default mode); these cover Compare mode specifically.
describe("SimulatorPage — Compare Scenarios mode (Checkpoint 4)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("defaults to Single Plan mode with the sequential pit-stop builder, not scenario rows", async () => {
    baseSetup()
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()

    expect(screen.getByLabelText("Pit stop 1 lap")).toBeInTheDocument()
    expect(screen.queryByLabelText("Scenario 1 pit lap")).not.toBeInTheDocument()
  })

  it("switching to Compare Scenarios shows scenario rows instead of the pit-stop builder", async () => {
    baseSetup()
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Compare Scenarios" }))

    expect(screen.getByLabelText("Scenario 1 pit lap")).toBeInTheDocument()
    expect(screen.getByLabelText("Scenario 2 pit lap")).toBeInTheDocument()
    expect(screen.queryByLabelText("Pit stop 1 lap")).not.toBeInTheDocument()
  })

  it("submits scenarios (not pit_laps/compounds) when running a compare-mode simulation", async () => {
    baseSetup()
    const resolvingMutateAsync = vi.fn().mockResolvedValue({ task_id: "task-1", status: "PENDING" })
    vi.mocked(useSimulateStrategy).mockImplementation(() =>
      useFakeSimulateStrategy(resolvingMutateAsync),
    )
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Compare Scenarios" }))
    fireEvent.change(screen.getByLabelText("Scenario 1 label"), {
      target: { value: "My custom label" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }))

    await waitFor(() => expect(resolvingMutateAsync).toHaveBeenCalledTimes(1))
    const payload = resolvingMutateAsync.mock.calls[0][0] as SimulateStrategyRequest
    expect(payload.pit_laps).toBeUndefined()
    expect(payload.compounds).toBeUndefined()
    expect(payload.scenarios).toEqual([
      { pit_laps: [30], compounds: ["HARD"], label: "My custom label" },
      { pit_laps: [33], compounds: ["HARD"], label: "Pit lap 33" },
    ])
  })

  it("caps scenarios at 4 and disables Add Scenario once the cap is reached", async () => {
    baseSetup()
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Compare Scenarios" }))
    // Starts with 2 default rows — two more clicks reach the cap of 4.
    fireEvent.click(screen.getByRole("button", { name: /\+ Add Scenario/ }))
    fireEvent.click(screen.getByRole("button", { name: /\+ Add Scenario/ }))

    expect(screen.getByLabelText("Scenario 4 pit lap")).toBeInTheDocument()
    expect(screen.queryByLabelText("Scenario 5 pit lap")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /\+ Add Scenario/ })).toBeDisabled()
  })

  it("disables Run Simulation when every scenario has been removed in Compare mode", async () => {
    baseSetup()
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Compare Scenarios" }))
    fireEvent.click(screen.getByLabelText("Remove scenario 1"))
    fireEvent.click(screen.getByLabelText("Remove scenario 1"))

    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeDisabled()
  })
})

// Waiting for a worker that starts on demand (demo deployment Day 6).
describe("SimulatorPage — waiting for the simulation engine", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    baseSetup()
    vi.mocked(useSimulateStrategy).mockImplementation(() =>
      useFakeSimulateStrategy(vi.fn().mockResolvedValue({ task_id: "task-1", status: "PENDING" })),
    )
  })

  async function runAndWait(result: { slowStart: boolean; timedOut: boolean }) {
    vi.mocked(useSimulationResult).mockReturnValue({
      data: { task_id: "task-1", status: "PENDING", result: null, error: null },
      ...result,
    } as unknown as ReturnType<typeof useSimulationResult>)
    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }))
    await screen.findByText(/Running Monte Carlo simulation|didn't respond in time/)
  }

  it("shows only the running message at first", async () => {
    await runAndWait({ slowStart: false, timedOut: false })

    expect(screen.getByText("Running Monte Carlo simulation…")).toBeInTheDocument()
    expect(screen.queryByText(/engine was asleep/)).not.toBeInTheDocument()
  })

  it("explains a cold start once the run has been waiting a while", async () => {
    await runAndWait({ slowStart: true, timedOut: false })

    expect(
      screen.getByText("If the simulation engine was asleep, it takes about a minute to start."),
    ).toBeInTheDocument()
  })

  it("stops waiting with a retry when the engine never responds", async () => {
    await runAndWait({ slowStart: true, timedOut: true })

    expect(
      screen.getByText("The simulation engine didn't respond in time. Please try again in a minute."),
    ).toBeInTheDocument()
    expect(screen.queryByText(/race weekend/)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try Again" })).toBeInTheDocument()
  })
})

// Daily simulation quota (demo deployment Day 5): a single plan costs one
// scenario, a comparison one per scenario, and a run is refused if it goes
// over the visitor's own or the demo-wide limit.
describe("SimulatorPage — daily scenario quota", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    baseSetup()
    vi.mocked(useSimulationResult).mockReturnValue({
      data: undefined,
      timedOut: false,
    } as unknown as ReturnType<typeof useSimulationResult>)
  })

  it("shows nothing about a quota when no limit is configured", async () => {
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))

    await goToDesignStrategyStep()

    expect(screen.queryByText(/scenarios left today/)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeEnabled()
  })

  it("shows the visitor's own remaining scenarios when theirs is the lower limit", async () => {
    mockQuota({ limit: 5, used: 2, remaining: 3 }, { limit: 15, used: 9, remaining: 6 })
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))

    await goToDesignStrategyStep()

    expect(
      screen.getByText("3 of 5 simulation scenarios left today. Resets at 00:00 UTC."),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeEnabled()
  })

  it("shows the demo-wide remaining scenarios when that is the lower limit", async () => {
    mockQuota({ limit: 5, used: 0, remaining: 5 }, { limit: 15, used: 13, remaining: 2 })
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))

    await goToDesignStrategyStep()

    expect(
      screen.getByText(/The demo has 2 simulation scenarios left today/),
    ).toBeInTheDocument()
  })

  it("disables a comparison that needs more scenarios than are left", async () => {
    mockQuota({ limit: 5, used: 4, remaining: 1 }, { limit: 15, used: 4, remaining: 11 })
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))

    await goToDesignStrategyStep()
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "Compare Scenarios" }))

    expect(screen.getByText(/This run needs 2; remove scenarios to fit\./)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeDisabled()
    fireEvent.click(screen.getByLabelText("Remove scenario 2"))
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeEnabled()
  })

  it("disables Run once nothing is left today", async () => {
    mockQuota({ limit: 5, used: 5, remaining: 0 }, { limit: 15, used: 5, remaining: 10 })
    vi.mocked(useSimulateStrategy).mockImplementation(() => useFakeSimulateStrategy(vi.fn()))

    await goToDesignStrategyStep()

    expect(screen.getByText(/Come back tomorrow to run more\./)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeDisabled()
  })

  it("shows the server's refusal message when a run is refused with 429", async () => {
    mockQuota({ limit: 5, used: 0, remaining: 5 }, { limit: 15, used: 0, remaining: 15 })
    const message =
      "The demo's daily limit of 15 simulation scenarios has 0 left and this request needs 1. It resets at 00:00 UTC."
    const refusal = Object.assign(new Error("Request failed with status code 429"), {
      isAxiosError: true,
      response: { status: 429, data: { error: "QUOTA_EXCEEDED", message, detail: null } },
    })
    vi.mocked(useSimulateStrategy).mockImplementation(() =>
      useFakeSimulateStrategy(vi.fn().mockRejectedValue(refusal)),
    )

    await goToDesignStrategyStep()
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(message)
    expect(screen.getByRole("heading", { name: "Design Strategy" })).toBeInTheDocument()
  })
})
