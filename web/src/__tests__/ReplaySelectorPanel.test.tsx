import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ReplaySelectorPanel } from "@/components/demo/ReplaySelectorPanel"
import {
  useCuratedSessions,
  useReplayAvailable,
  useReplayStatus,
  useStartReplay,
  useStopReplay,
} from "@/hooks/useDemoReplay"
import { useSessionGaps } from "@/hooks/useSessionGaps"

vi.mock("@/hooks/useDemoReplay", () => ({
  useCuratedSessions: vi.fn(),
  useReplayAvailable: vi.fn(),
  useReplayStatus: vi.fn(),
  useStartReplay: vi.fn(),
  useStopReplay: vi.fn(),
}))
vi.mock("@/hooks/useSessionGaps", () => ({ useSessionGaps: vi.fn() }))

const CURATED = [
  {
    session_id: "brit-1",
    race_name: "British Grand Prix 2026",
    circuit_name: "Silverstone Circuit",
    description: "Safety Car pit stampede.",
    start_lap: 43,
    end_lap: 52,
    estimated_duration_minutes: 22,
  },
  {
    session_id: "belg-1",
    race_name: "Belgian Grand Prix 2026",
    circuit_name: "Spa",
    description: "VSC cluster.",
    start_lap: 14,
    end_lap: 23,
    estimated_duration_minutes: 19,
  },
  {
    session_id: "can-1",
    race_name: "Canadian Grand Prix 2026",
    circuit_name: "Montreal",
    description: "VSC undercut fight.",
    start_lap: 26,
    end_lap: 35,
    estimated_duration_minutes: 19,
  },
]

const startMutate = vi.fn()
const stopMutate = vi.fn()

interface SetupOverrides {
  available?: { available: boolean; reason: string | null }
  status?: Record<string, unknown>
  gapsLap?: number
}

function setup(overrides: SetupOverrides = {}) {
  vi.mocked(useReplayAvailable).mockReturnValue({
    data: overrides.available ?? { available: true, reason: null },
  } as unknown as ReturnType<typeof useReplayAvailable>)
  vi.mocked(useReplayStatus).mockReturnValue({
    data: overrides.status ?? { running: false },
  } as unknown as ReturnType<typeof useReplayStatus>)
  vi.mocked(useCuratedSessions).mockReturnValue({
    data: { sessions: CURATED },
  } as unknown as ReturnType<typeof useCuratedSessions>)
  vi.mocked(useStartReplay).mockReturnValue({
    mutate: startMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useStartReplay>)
  vi.mocked(useStopReplay).mockReturnValue({
    mutate: stopMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useStopReplay>)
  vi.mocked(useSessionGaps).mockReturnValue({
    data:
      overrides.gapsLap !== undefined
        ? { session_id: "s", gaps: [{ lap_number: overrides.gapsLap }] }
        : undefined,
  } as unknown as ReturnType<typeof useSessionGaps>)
}

function renderPanel(props: { sessionId?: string | null; isLive?: boolean } = {}) {
  return render(
    <MemoryRouter>
      <ReplaySelectorPanel sessionId={props.sessionId ?? "s1"} isLive={props.isLive ?? false} />
    </MemoryRouter>,
  )
}

describe("ReplaySelectorPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders nothing during a live race", () => {
    setup()
    const { container } = renderPanel({ isLive: true })
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when the backend reports it unavailable", () => {
    setup({ available: { available: false, reason: "live timing feed active for 2026 round 10" } })
    const { container } = renderPanel()
    expect(container).toBeEmptyDOMElement()
  })

  it("shows the three curated session cards with lap ranges when idle", () => {
    setup()
    renderPanel()

    expect(screen.getByText("Watch a Replay")).toBeInTheDocument()
    expect(screen.getByText("British Grand Prix 2026")).toBeInTheDocument()
    expect(screen.getByText(/Laps 43–52/)).toBeInTheDocument()
    expect(screen.getByText(/Laps 14–23/)).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Start Replay" })).toHaveLength(3)
  })

  it("starts a replay for the chosen session", () => {
    setup()
    renderPanel()

    fireEvent.click(screen.getAllByRole("button", { name: "Start Replay" })[0])

    expect(startMutate).toHaveBeenCalledWith("brit-1", expect.anything())
  })

  it("shows the currently-replaying indicator with the current lap and stops on click", () => {
    setup({
      status: {
        running: true,
        race_name: "British Grand Prix 2026",
        session_id: "brit-1",
        start_lap: 43,
        end_lap: 52,
      },
      gapsLap: 47,
    })
    renderPanel({ sessionId: "brit-1" })

    expect(screen.getByText(/Currently replaying:/)).toBeInTheDocument()
    expect(screen.getByText(/Lap 47\/52/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Stop Replay" }))
    expect(stopMutate).toHaveBeenCalled()
  })
})
