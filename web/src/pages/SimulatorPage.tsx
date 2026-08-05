import { useEffect, useState } from "react"
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { useDrivers } from "@/hooks/useDrivers"
import { useSimulateStrategy, useSimulationResult } from "@/hooks/useStrategy"
import { useSessionStore } from "@/stores/sessionStore"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatLapTime } from "@/utils/formatters"
import type { SimulateStrategyRequest } from "@/types"

type Step = 1 | 2 | 3 | 4

interface PitStopRow {
  lap: number
  compound: string
}

// Backend validates each compounds[] entry against exactly this set
// (backend/schemas/simulate_schema.py).
const COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]

const STEP_LABELS: Record<Step, string> = {
  1: "Driver & Race State",
  2: "Design Strategy",
  3: "Simulating",
  4: "Results",
}

function StepHeader({ step }: { step: Step }) {
  return (
    <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
      {([1, 2, 3, 4] as Step[]).map((s) => (
        <span key={s} className={s === step ? "font-semibold text-foreground" : ""}>
          {s}. {STEP_LABELS[s]}
          {s !== 4 && <span className="mx-2">→</span>}
        </span>
      ))}
    </div>
  )
}

export function SimulatorPage() {
  // Pre-filled from whichever race was last viewed (sessionStore is cleared
  // when RacePage unmounts) — SimulatorPage's route carries no sessionId of
  // its own, so this is editable rather than assumed.
  const selectedSessionId = useSessionStore((state) => state.selectedSessionId)
  const { data: drivers } = useDrivers()

  const [step, setStep] = useState<Step>(1)
  const [sessionId, setSessionId] = useState(selectedSessionId ?? "")
  const [driverId, setDriverId] = useState("")
  const [currentLap, setCurrentLap] = useState(1)
  const [currentCompound, setCurrentCompound] = useState("MEDIUM")
  const [currentTyreAge, setCurrentTyreAge] = useState(0)
  const [remainingLaps, setRemainingLaps] = useState(20)
  const [pitStops, setPitStops] = useState<PitStopRow[]>([{ lap: 15, compound: "HARD" }])
  const [taskId, setTaskId] = useState<string | null>(null)

  const simulateMutation = useSimulateStrategy(sessionId)
  const simulationResult = useSimulationResult(taskId)

  useEffect(() => {
    if (simulationResult.data?.status === "SUCCESS") setStep(4)
  }, [simulationResult.data?.status])

  function addPitStop() {
    setPitStops((rows) => [...rows, { lap: remainingLaps, compound: "HARD" }])
  }

  function removePitStop(index: number) {
    setPitStops((rows) => rows.filter((_, i) => i !== index))
  }

  function updatePitStop(index: number, patch: Partial<PitStopRow>) {
    setPitStops((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  async function handleRunSimulation() {
    const payload: SimulateStrategyRequest = {
      driver_id: driverId,
      current_lap: currentLap,
      current_compound: currentCompound,
      current_tyre_age: currentTyreAge,
      remaining_laps: remainingLaps,
      pit_laps: pitStops.map((row) => row.lap),
      compounds: pitStops.map((row) => row.compound),
    }
    setStep(3)
    const accepted = await simulateMutation.mutateAsync(payload)
    setTaskId(accepted.task_id)
  }

  function handleReset() {
    setStep(1)
    setTaskId(null)
  }

  const step1Valid = sessionId.trim() !== "" && driverId !== "" && remainingLaps > 0

  const strategies = simulationResult.data?.result?.strategies ?? []
  const chartData = strategies.map((strategy, index) => ({
    name: `Plan ${index + 1} (L${strategy.pit_laps.join(", L")})`,
    positionChange: strategy.position_gain_loss,
    finishTime: strategy.predicted_finish_time,
    confidenceInterval: strategy.confidence_interval,
  }))

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="mb-1 text-xl font-semibold">Strategy Simulator</h1>
      <StepHeader step={step} />

      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Driver & Current Race State</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="sessionId">Session ID</Label>
              <Input
                id="sessionId"
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                placeholder="Session UUID"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="driverId">Driver</Label>
              <select
                id="driverId"
                value={driverId}
                onChange={(e) => setDriverId(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="">Select a driver…</option>
                {(drivers ?? []).map((driver) => (
                  <option key={driver.id} value={driver.id}>
                    {driver.code} — {driver.full_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="currentLap">Current Lap</Label>
                <Input
                  id="currentLap"
                  type="number"
                  min={1}
                  value={currentLap}
                  onChange={(e) => setCurrentLap(Number(e.target.value))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="remainingLaps">Remaining Laps</Label>
                <Input
                  id="remainingLaps"
                  type="number"
                  min={1}
                  value={remainingLaps}
                  onChange={(e) => setRemainingLaps(Number(e.target.value))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="currentCompound">Current Compound</Label>
                <select
                  id="currentCompound"
                  value={currentCompound}
                  onChange={(e) => setCurrentCompound(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                >
                  {COMPOUNDS.map((compound) => (
                    <option key={compound} value={compound}>
                      {compound}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="currentTyreAge">Current Tyre Age (laps)</Label>
                <Input
                  id="currentTyreAge"
                  type="number"
                  min={0}
                  value={currentTyreAge}
                  onChange={(e) => setCurrentTyreAge(Number(e.target.value))}
                />
              </div>
            </div>
            <Button disabled={!step1Valid} onClick={() => setStep(2)}>
              Next: Design Strategy
            </Button>
          </CardContent>
        </Card>
      )}

      {step === 2 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Design Strategy</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-xs text-muted-foreground">
              Add planned pit stops (lap + compound). Leave empty to let the Monte Carlo
              simulation decide pit timing autonomously.
            </p>
            <div className="space-y-2">
              {pitStops.map((row, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    type="number"
                    min={1}
                    value={row.lap}
                    onChange={(e) => updatePitStop(index, { lap: Number(e.target.value) })}
                    className="w-24"
                    aria-label={`Pit stop ${index + 1} lap`}
                  />
                  <select
                    value={row.compound}
                    onChange={(e) => updatePitStop(index, { compound: e.target.value })}
                    className="flex h-10 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
                    aria-label={`Pit stop ${index + 1} compound`}
                  >
                    {COMPOUNDS.map((compound) => (
                      <option key={compound} value={compound}>
                        {compound}
                      </option>
                    ))}
                  </select>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    onClick={() => removePitStop(index)}
                  >
                    −
                  </Button>
                </div>
              ))}
            </div>
            <Button type="button" variant="outline" size="sm" onClick={addPitStop}>
              + Add Pit Stop
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button onClick={handleRunSimulation}>Run Simulation</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {step === 3 && (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-muted border-t-primary" />
            <p className="text-sm text-muted-foreground">
              {simulationResult.data?.status === "FAILURE"
                ? "Simulation failed."
                : `Running Monte Carlo simulation… (${simulationResult.data?.status ?? "PENDING"})`}
            </p>
            {simulationResult.data?.status === "FAILURE" && (
              <Button variant="outline" onClick={handleReset}>
                Try Again
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {step === 4 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Predicted Position Change by Strategy</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {chartData.length === 0 ? (
              <p className="text-sm text-muted-foreground">No strategy variants returned.</p>
            ) : (
              <ResponsiveContainer width="100%" height={320}>
                <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 48, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis
                    dataKey="name"
                    angle={-20}
                    textAnchor="end"
                    interval={0}
                    height={60}
                    className="text-xs fill-muted-foreground"
                  />
                  <YAxis
                    label={{ value: "Position change", angle: -90, position: "insideLeft" }}
                    className="text-xs fill-muted-foreground"
                  />
                  <Tooltip
                    formatter={(value, _name, item) => {
                      const change = typeof value === "number" ? value : 0
                      const finishTime =
                        typeof item.payload?.finishTime === "number"
                          ? formatLapTime(item.payload.finishTime)
                          : "—"
                      return [
                        `${change > 0 ? "+" : ""}${change} position(s), finish ${finishTime}`,
                        "Change",
                      ]
                    }}
                  />
                  {/* minPointSize: a 0-change bar has zero pixel height at
                      the baseline by default and reads as a missing bar —
                      this floors every bar to at least 3px so it stays
                      visible/hoverable regardless of value. */}
                  <Bar dataKey="positionChange" isAnimationActive={false} minPointSize={3}>
                    {chartData.map((entry, index) => (
                      <Cell
                        key={index}
                        fill={entry.positionChange >= 0 ? "#10B981" : "#EF4444"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
            <Button variant="outline" onClick={handleReset}>
              Run Another Simulation
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
