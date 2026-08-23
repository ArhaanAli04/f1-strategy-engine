import { useEffect, useMemo, useRef, useState } from "react"
import { Check, Trash2 } from "lucide-react"
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSimulateStrategy, useSimulationResult } from "@/hooks/useStrategy"
import { useSessionStore } from "@/stores/sessionStore"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { CHART_TOOLTIP_STYLE, FALLBACK_TEAM_COLOR } from "@/utils/constants"
import { isActiveDriver } from "@/utils/drivers"
import { formatLapTime } from "@/utils/formatters"
import type { DriverResponse, SimulatedRaceOutcome, SimulateStrategyRequest } from "@/types"

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
    <div className="mb-6 flex items-start">
      {([1, 2, 3, 4] as Step[]).map((s, index) => {
        const isComplete = s < step
        const isActive = s === step
        return (
          <div key={s} className="flex flex-1 items-start last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={cn(
                  "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums transition-colors",
                  isComplete
                    ? "border-primary bg-primary text-primary-foreground"
                    : isActive
                      ? "border-primary text-primary"
                      : "border-border text-muted-foreground",
                )}
              >
                {isComplete ? <Check className="h-4 w-4" /> : s}
              </div>
              <span
                className={cn(
                  "max-w-[84px] text-center text-[10px] uppercase tracking-wide",
                  isActive ? "font-semibold text-foreground" : "text-muted-foreground",
                )}
              >
                {STEP_LABELS[s]}
              </span>
            </div>
            {index < 3 && (
              <div className={cn("mx-2 mt-4 h-px flex-1 transition-colors", isComplete ? "bg-primary" : "bg-border")} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${Math.abs(count) === 1 ? "" : "s"}`
}

interface PlanExplanationCardProps {
  planLabel: string
  strategy: SimulatedRaceOutcome
  driversById: Map<string, DriverResponse>
}

// Row styling mirrors LiveTimingTower's timing-tower aesthetic (monospace
// position, thin team-color bar, semibold code) rather than DriverChip's
// pill style, per the "matching timing tower aesthetic" request.
function PlanExplanationCard({ planLabel, strategy, driversById }: PlanExplanationCardProps) {
  const { position_gain_loss, explanation } = strategy
  const isGain = position_gain_loss > 0
  const isLoss = position_gain_loss < 0
  const freshCompound = strategy.compounds.at(-1)

  const heading = isGain
    ? `Why ${planLabel} gains ${pluralize(position_gain_loss, "position")}`
    : isLoss
      ? `Why ${planLabel} loses ${pluralize(Math.abs(position_gain_loss), "position")}`
      : `Why ${planLabel} doesn't change your position`

  const driverListLabel = isGain ? "Drivers you overtake after pit" : "Drivers who overtook you in pitstop"
  const arrowLabel = isGain ? "→ you overtake" : "→ now ahead of you"

  const sufficient = explanation.total_recoverable_seconds >= explanation.pit_cost_seconds

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <p
        className={cn(
          "text-sm font-semibold",
          isGain ? "text-[#10B981]" : isLoss ? "text-[#EF4444]" : "text-foreground",
        )}
      >
        {heading}
      </p>

      <p className="text-xs text-muted-foreground">
        Pit stop cost:{" "}
        <span className="font-mono tabular-nums text-foreground">
          {explanation.pit_cost_seconds.toFixed(1)}s
        </span>
      </p>

      {explanation.drivers_overtaken.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No drivers within pit stop window — position unchanged by pit stop timing
        </p>
      ) : (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">{driverListLabel}</p>
          <div className="space-y-0.5">
            {explanation.drivers_overtaken.map((entry) => {
              const driver = driversById.get(entry.driver_id)
              const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR
              return (
                <div
                  key={entry.driver_id}
                  className="flex items-center gap-2 py-0.5 font-mono text-xs tabular-nums"
                >
                  <span className="w-7 text-muted-foreground">P{entry.position}</span>
                  <span
                    className="h-3 w-1 flex-shrink-0 rounded-full"
                    style={{ backgroundColor: teamColor }}
                  />
                  <span className="w-10 font-semibold text-foreground">{driver?.code ?? "???"}</span>
                  <span className="text-muted-foreground">
                    +{entry.gap_seconds.toFixed(1)}s behind {arrowLabel}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {isLoss && (
        <p className="text-xs text-muted-foreground">
          Only {pluralize(explanation.remaining_laps, "lap")} remaining after pit —{" "}
          {sufficient ? "sufficient" : "not enough"} to recover on fresh tyres.
        </p>
      )}

      {explanation.fresh_tyre_gain_per_lap > 0 && freshCompound && (
        <p className="text-xs text-muted-foreground">
          Fresh {freshCompound} tyre advantage: ~{explanation.fresh_tyre_gain_per_lap.toFixed(1)}s/lap —{" "}
          {isGain
            ? `recovers ~${explanation.total_recoverable_seconds.toFixed(1)}s over ${pluralize(
                explanation.remaining_laps,
                "lap",
              )}, enough to pass ${pluralize(explanation.drivers_overtaken.length, "driver")}.`
            : isLoss
              ? `recovers only ~${explanation.total_recoverable_seconds.toFixed(1)}s in ${pluralize(
                  explanation.remaining_laps,
                  "lap",
                )}.`
              : `roughly offsets the pit-stop loss over ${pluralize(
                  explanation.remaining_laps,
                  "lap",
                )} (~${explanation.total_recoverable_seconds.toFixed(1)}s recovered).`}
        </p>
      )}
    </div>
  )
}

export function SimulatorPage() {
  // Pre-filled from whichever race was last viewed (sessionStore is cleared
  // when RacePage unmounts) — SimulatorPage's route carries no sessionId of
  // its own, so this is editable rather than assumed.
  const selectedSessionId = useSessionStore((state) => state.selectedSessionId)
  const { data: drivers } = useDrivers()
  // GET /drivers returns every driver ever ingested, including retired
  // historical ones with no current-season contract — same filter as
  // DriverRosterGrid.tsx/AlertSubscriptionsForm.tsx, so only drivers on the
  // current grid are selectable here.
  const activeDrivers = (drivers ?? []).filter(isActiveDriver)
  // driver_id -> DriverResponse for resolving PlanExplanationCard's
  // drivers_overtaken entries to code/team color, same lookup pattern as
  // LiveTimingTower's driversById.
  const driversById = useMemo(() => {
    const map = new Map<string, DriverResponse>()
    for (const driver of drivers ?? []) map.set(driver.id, driver)
    return map
  }, [drivers])

  // LIVE MODE vs MANUAL MODE for the Session field — invisible to the user,
  // no error state. LIVE MODE only kicks in when there's no explicit prior
  // selection from navigating in via RacePage (selectedSessionId), AND
  // /races/current resolves a Race (not FP/Q) session, AND that session
  // actually has ingested telemetry (gaps.length > 0) — /races/current
  // resolves to the next race on the calendar whose date hasn't passed yet
  // (race_service._fetch_current_race's own docstring: "currently
  // active/upcoming"), which is scheduled — and therefore has a Race row —
  // well before it's actually live. Without the gaps check, a race days
  // away would lock the field to a session with no real data. useSessionGaps
  // is the same session-level, no-driver-needed hook LiveTimingTower already
  // polls; empty gaps means nothing's been ingested yet. Otherwise this
  // falls back to the plain manual text input, unchanged from before this
  // feature existed. useCurrentRace already treats its own 404 (no
  // live/upcoming race right now) as a normal, silent state — see its hook.
  const { data: currentRace } = useCurrentRace()
  const liveRaceSession = currentRace?.sessions.find((s) => s.session_type === "R")
  const liveSessionGaps = useSessionGaps(liveRaceSession?.id ?? null)
  const isLiveSessionMode =
    !selectedSessionId && Boolean(liveRaceSession) && (liveSessionGaps.data?.gaps.length ?? 0) > 0

  const [step, setStep] = useState<Step>(1)
  const [sessionId, setSessionId] = useState(selectedSessionId ?? "")
  const [driverId, setDriverId] = useState("")
  const [currentLap, setCurrentLap] = useState(1)
  const [currentCompound, setCurrentCompound] = useState("MEDIUM")
  const [currentTyreAge, setCurrentTyreAge] = useState(0)
  const [remainingLaps, setRemainingLaps] = useState(20)
  const [pitStops, setPitStops] = useState<PitStopRow[]>([{ lap: 15, compound: "HARD" }])
  const [taskId, setTaskId] = useState<string | null>(null)

  useEffect(() => {
    if (isLiveSessionMode && liveRaceSession) setSessionId(liveRaceSession.id)
  }, [isLiveSessionMode, liveRaceSession])

  // Driver stays a fully manual choice (no default) — once picked, default
  // Current Lap/Compound/Tyre Age from their latest lap in this session.
  // lastAutoFilledDriverRef guards against clobbering a manual edit: it only
  // applies once per driver selection, not on useDriverLaps' background
  // 10s poll (which changes .data every tick without driverId changing).
  const driverLaps = useDriverLaps(sessionId || null, driverId || null)
  const lastAutoFilledDriverRef = useRef<string | null>(null)

  useEffect(() => {
    if (!driverId || lastAutoFilledDriverRef.current === driverId) return
    const items = driverLaps.data?.items ?? []
    if (items.length === 0) return
    const latest = items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b))
    setCurrentLap(latest.lap_number)
    setCurrentCompound(latest.compound)
    setCurrentTyreAge(latest.tyre_age_laps)
    lastAutoFilledDriverRef.current = driverId
  }, [driverId, driverLaps.data])

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
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl">
        <h1 className="mb-1 text-xl font-semibold">Strategy Simulator</h1>
        <StepHeader step={step} />

      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Driver & Current Race State</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="sessionId">Session</Label>
              {isLiveSessionMode ? (
                <div
                  id="sessionId"
                  className="flex h-9 items-center rounded-md border bg-muted/30 px-3 text-sm text-foreground"
                >
                  {currentRace?.event_name ?? currentRace?.circuit?.name ?? "Current race"} — Race
                </div>
              ) : (
                <Input
                  id="sessionId"
                  value={sessionId}
                  onChange={(e) => setSessionId(e.target.value)}
                  placeholder="Session UUID"
                />
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="driverId">Driver</Label>
              <Select value={driverId} onValueChange={setDriverId}>
                <SelectTrigger id="driverId">
                  <SelectValue placeholder="Select a driver…" />
                </SelectTrigger>
                <SelectContent>
                  {activeDrivers.map((driver) => (
                    <SelectItem key={driver.id} value={driver.id}>
                      {driver.code} — {driver.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
                <Select value={currentCompound} onValueChange={setCurrentCompound}>
                  <SelectTrigger id="currentCompound">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {COMPOUNDS.map((compound) => (
                      <SelectItem key={compound} value={compound}>
                        {compound}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
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
                  <Select
                    value={row.compound}
                    onValueChange={(value) => updatePitStop(index, { compound: value })}
                  >
                    <SelectTrigger className="flex-1" aria-label={`Pit stop ${index + 1} compound`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {COMPOUNDS.map((compound) => (
                        <SelectItem key={compound} value={compound}>
                          {compound}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    onClick={() => removePitStop(index)}
                    aria-label={`Remove pit stop ${index + 1}`}
                  >
                    <Trash2 className="h-4 w-4" />
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
            {/* worker offline outside race weekends (Day 40 hybrid
                deployment, see fly.toml) — a task enqueued then never
                resolves, so useSimulationResult's timedOut swaps this in
                after 60s instead of spinning forever. */}
            {simulationResult.data?.status !== "FAILURE" && simulationResult.timedOut ? (
              <p className="max-w-sm text-center text-sm text-muted-foreground">
                Strategy simulation requires an active race weekend. The worker is currently
                offline — scale up before the next race to enable this feature.
              </p>
            ) : (
              <>
                <div className="h-10 w-10 animate-spin rounded-full border-4 border-muted border-t-primary" />
                <p className="text-sm text-muted-foreground">
                  {simulationResult.data?.status === "FAILURE"
                    ? "Simulation failed."
                    : `Running Monte Carlo simulation… (${simulationResult.data?.status ?? "PENDING"})`}
                </p>
              </>
            )}
            {(simulationResult.data?.status === "FAILURE" || simulationResult.timedOut) && (
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
                    {...CHART_TOOLTIP_STYLE}
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

            {/* The bar chart's tooltip only shows one strategy's position
                change + finish time at a time, on hover — this list surfaces
                every strategy at once, including confidence_interval (the
                Monte Carlo predicted-finish-time range), which the backend
                already returns but nothing in the UI displayed until now. */}
            {chartData.length > 0 && (
              <div className="space-y-1">
                <div className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-2 py-1 text-xs font-medium text-muted-foreground">
                  <span>Strategy</span>
                  <span className="text-right">Change</span>
                  <span className="text-right">Finish Time</span>
                  <span className="text-right">Finish Time Range</span>
                </div>
                {chartData.map((entry, index) => (
                  <div
                    key={index}
                    className={cn(
                      "grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 rounded px-2 py-1.5 text-xs",
                      index % 2 === 0 ? "bg-row-void" : "bg-row-recede",
                    )}
                  >
                    <span className="truncate">{entry.name}</span>
                    <span
                      className={cn(
                        "text-right font-mono font-semibold tabular-nums",
                        entry.positionChange >= 0 ? "text-[#10B981]" : "text-[#EF4444]",
                      )}
                    >
                      {entry.positionChange > 0 ? "+" : ""}
                      {entry.positionChange}
                    </span>
                    <span className="text-right font-mono tabular-nums text-muted-foreground">
                      {formatLapTime(entry.finishTime)}
                    </span>
                    <span className="text-right font-mono tabular-nums text-muted-foreground">
                      {formatLapTime(entry.confidenceInterval[0])}–{formatLapTime(entry.confidenceInterval[1])}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {strategies.length > 0 && (
              <div className="space-y-3">
                {strategies.map((strategy, index) => (
                  <PlanExplanationCard
                    key={index}
                    planLabel={`Plan ${index + 1}`}
                    strategy={strategy}
                    driversById={driversById}
                  />
                ))}
              </div>
            )}

            <Button variant="outline" onClick={handleReset}>
              Run Another Simulation
            </Button>
          </CardContent>
        </Card>
      )}
      </div>
    </div>
  )
}
