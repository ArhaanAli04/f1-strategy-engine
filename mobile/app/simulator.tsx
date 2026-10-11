import { Picker } from "@react-native-picker/picker"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { useFont } from "@shopify/react-native-skia"
import { isAxiosError } from "axios"
import { useLocalSearchParams } from "expo-router"
import { useEffect, useRef, useState } from "react"
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native"
import { CartesianChart, HorizontalBar } from "victory-native"
import { PlanExplanationCard } from "@/components/strategy/PlanExplanationCard"
import { PositionDistributionChart, strategyLabel } from "@/components/strategy/PositionDistributionChart"
import { useCurrentRace } from "@/hooks/useCurrentRace"
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useLastIngestedSession } from "@/hooks/useLastIngestedSession"
import { useRaceBySession } from "@/hooks/useRaceBySession"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useSimulateStrategy, useSimulationQuota, useSimulationResult } from "@/hooks/useStrategy"
import { SCENARIO_SERIES_COLORS } from "@/utils/constants"
import { isActiveDriver } from "@/utils/drivers"
import { getApiErrorMessage } from "@/utils/errors"
import { formatRaceTime } from "@/utils/formatters"
import * as haptics from "@/utils/haptics"
import type { SimulateStrategyRequest } from "@/types"

type Step = 1 | 2 | 3 | 4

// Single Plan: one sequential multi-stop plan. Compare Scenarios: up to
// MAX_SCENARIOS independent single-pit-lap alternatives, run on the same
// random race conditions. Same two modes as web's SimulatorPage.
type SimulationMode = "single" | "compare"

interface PitStopRow {
  lap: number
  compound: string
}

interface ScenarioRow {
  lap: number
  compound: string
  label: string
}

// Backend validates each compounds[] entry against exactly this set
// (backend/schemas/simulate_schema.py) — same list as web/desktop.
const COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]

// Matches backend/schemas/simulate_schema.py's _MAX_SCENARIOS.
const MAX_SCENARIOS = 4

// One word each so the four fit under their circles on a phone (owner's
// choice, 2026-10-07); web's longer labels wrapped to three lines here.
const STEP_LABELS: Record<Step, string> = {
  1: "Setup",
  2: "Strategy",
  3: "Simulate",
  4: "Results",
}

function StepHeader({ step }: { step: Step }) {
  return (
    <View className="mb-4 flex-row">
      {([1, 2, 3, 4] as Step[]).map((s) => {
        const isComplete = s < step
        const isActive = s === step
        return (
          <View key={s} className="flex-1 items-center gap-1">
            <View
              className={`h-7 w-7 items-center justify-center rounded-full border ${
                isComplete
                  ? "border-foreground bg-foreground"
                  : isActive
                    ? "border-foreground"
                    : "border-white/20"
              }`}
            >
              <Text
                className={`text-xs font-semibold ${
                  isComplete ? "text-background" : isActive ? "text-foreground" : "text-muted"
                }`}
              >
                {s}
              </Text>
            </View>
            <Text
              className={`text-center text-[10px] uppercase tracking-wide ${
                isActive ? "font-semibold text-foreground" : "text-muted"
              }`}
            >
              {STEP_LABELS[s]}
            </Text>
          </View>
        )
      })}
    </View>
  )
}

function FieldLabel({ children }: { children: string }) {
  return <Text className="mb-1.5 text-sm font-medium text-foreground">{children}</Text>
}

function ModeButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      className={`flex-1 items-center rounded-md border py-2 ${
        active ? "border-foreground bg-foreground" : "border-white/10"
      }`}
    >
      <Text className={`text-sm font-semibold ${active ? "text-background" : "text-foreground"}`}>
        {label}
      </Text>
    </Pressable>
  )
}

const inputClassName = "rounded-md border border-white/10 bg-surface px-3 py-2.5 text-base text-foreground"
const pickerWrapperClassName = "overflow-hidden rounded-md border border-white/10 bg-surface"
// iOS draws the picker as a wheel whose labels follow itemStyle; without it
// they came out black on the dark field (found on the iPhone, 2026-10-07).
// Same colour and size as the text inputs (text-foreground, text-base).
const PICKER_ITEM_STYLE = { color: "#fafafa", fontSize: 16 }

// Disabled buttons dim through a plain style: NativeWind's disabled variant
// is used nowhere else in mobile, so it may not apply on the device.
const DISABLED_OPACITY = 0.4

const CHART_HEIGHT_PER_ROW = 56
const GAIN_COLOR = "#10B981"
const LOSS_COLOR = "#EF4444"
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"

function CompoundPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <View className={pickerWrapperClassName}>
      <Picker
        selectedValue={value}
        onValueChange={onChange}
        dropdownIconColor="#fafafa"
        itemStyle={PICKER_ITEM_STYLE}
      >
        {COMPOUNDS.map((compound) => (
          <Picker.Item key={compound} label={compound} value={compound} color="#fafafa" />
        ))}
      </Picker>
    </View>
  )
}

function RemoveButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      className="h-10 w-10 items-center justify-center rounded-md border border-white/10"
      accessibilityLabel={label}
    >
      <Text className="text-lg text-destructive">×</Text>
    </Pressable>
  )
}

function formatChange(change: number): string {
  return `${change > 0 ? "+" : ""}${change}`
}

// Simulator screen — reachable from the Strategy tab's "Run Simulator" button
// and a driver sheet's "Simulate this driver". Same 4-step flow and results as
// web/src/pages/SimulatorPage.tsx since 2026-10-07 (owner: the results must
// match web): the session is picked automatically, Single Plan or Compare
// Scenarios, and the results show each strategy's finish time and range and
// the finishing-position distribution. Phone differences: no drag-drop and no
// CSV export (desktop only), the results table is one card per strategy, and
// a driver sheet's session comes first (web has no such sheet).
export default function SimulatorScreen() {
  // A driver sheet opens this screen with its session and driver filled in.
  const params = useLocalSearchParams<{ sessionId?: string; driverId?: string }>()
  const { data: drivers } = useDrivers()
  const activeDrivers = (drivers ?? []).filter(isActiveDriver)
  // Skia's Canvas has its own independent font subsystem from RN Text/
  // expo-font — reuses the same bundled Titillium Web .ttf as a second load.
  const chartFont = useFont(TitilliumWeb_400Regular, 10)

  // Session, as on web: the live race when /races/current has a Race session
  // with timing data (a race days away has none yet), else the newest
  // ingested race. A driver sheet's session wins over both. Shown read-only.
  const paramSessionId = params.sessionId ?? null
  const { data: currentRace } = useCurrentRace()
  const liveRaceSession = currentRace?.sessions.find((s) => s.session_type === "R")
  const liveSessionGaps = useSessionGaps(paramSessionId ? null : (liveRaceSession?.id ?? null))
  const isLiveSessionMode =
    !paramSessionId && Boolean(liveRaceSession) && (liveSessionGaps.data?.gaps.length ?? 0) > 0
  const lastIngestedQuery = useLastIngestedSession(!paramSessionId && !isLiveSessionMode)
  const lastIngestedSession = lastIngestedQuery.data
  const paramRace = useRaceBySession(paramSessionId).data
  const sessionId =
    paramSessionId ??
    (isLiveSessionMode ? liveRaceSession?.id : lastIngestedSession?.session_id) ??
    ""

  const [step, setStep] = useState<Step>(1)
  const [driverId, setDriverId] = useState(params.driverId ?? "")
  const [currentLap, setCurrentLap] = useState("1")
  const [currentCompound, setCurrentCompound] = useState("MEDIUM")
  const [currentTyreAge, setCurrentTyreAge] = useState("0")
  const [remainingLaps, setRemainingLaps] = useState("20")
  const [mode, setMode] = useState<SimulationMode>("single")
  const [pitStops, setPitStops] = useState<PitStopRow[]>([{ lap: 15, compound: "HARD" }])
  const [scenarios, setScenarios] = useState<ScenarioRow[]>([
    { lap: 30, compound: "HARD", label: "" },
    { lap: 33, compound: "HARD", label: "" },
  ])
  const [taskId, setTaskId] = useState<string | null>(null)

  // Once a driver is picked, default Current Lap/Compound/Tyre Age from
  // their latest lap in this session — same auto-fill as web/desktop.
  // lastAutoFilledDriverRef guards against clobbering a manual edit: only
  // applies once per driver selection, not on useDriverLaps' background poll.
  const driverLaps = useDriverLaps(sessionId || null, driverId || null)
  const lastAutoFilledDriverRef = useRef<string | null>(null)

  useEffect(() => {
    if (!driverId || lastAutoFilledDriverRef.current === driverId) return
    const items = driverLaps.data?.items ?? []
    if (items.length === 0) return
    const latest = items.reduce((a, b) => (a.lap_number > b.lap_number ? a : b))
    setCurrentLap(String(latest.lap_number))
    setCurrentCompound(latest.compound)
    setCurrentTyreAge(String(latest.tyre_age_laps))
    lastAutoFilledDriverRef.current = driverId
  }, [driverId, driverLaps.data])

  const simulateMutation = useSimulateStrategy(sessionId)
  const simulationResult = useSimulationResult(taskId)
  // A run is refused if it goes over the visitor's own or the demo-wide daily
  // limit, so the lower remaining count is what matters; null means
  // unlimited. A plan costs one scenario, a comparison one per scenario.
  const quota = useSimulationQuota().data
  const remainingCounts = [quota?.user_quota.remaining, quota?.global_quota.remaining].filter(
    (n): n is number => typeof n === "number",
  )
  const scenariosLeft = remainingCounts.length > 0 ? Math.min(...remainingCounts) : null
  const scenariosNeeded = mode === "compare" ? scenarios.length : 1
  const overQuota = scenariosLeft !== null && scenariosNeeded > scenariosLeft

  useEffect(() => {
    if (simulationResult.data?.status === "SUCCESS") {
      haptics.success()
      setStep(4)
    } else if (simulationResult.data?.status === "FAILURE") {
      haptics.error()
    }
  }, [simulationResult.data?.status])

  useEffect(() => {
    if (simulationResult.timedOut) haptics.warning()
  }, [simulationResult.timedOut])

  function selectMode(next: SimulationMode) {
    if (next === mode) return
    haptics.selectionTick()
    setMode(next)
  }

  function addPitStop() {
    setPitStops((rows) => [...rows, { lap: Number(remainingLaps) || 1, compound: "HARD" }])
  }

  function removePitStop(index: number) {
    setPitStops((rows) => rows.filter((_, i) => i !== index))
  }

  function updatePitStop(index: number, patch: Partial<PitStopRow>) {
    setPitStops((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  function addScenario() {
    setScenarios((rows) =>
      rows.length >= MAX_SCENARIOS ? rows : [...rows, { lap: Number(remainingLaps) || 1, compound: "HARD", label: "" }],
    )
  }

  function removeScenario(index: number) {
    setScenarios((rows) => rows.filter((_, i) => i !== index))
  }

  function updateScenario(index: number, patch: Partial<ScenarioRow>) {
    setScenarios((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  async function handleRunSimulation() {
    const basePayload = {
      driver_id: driverId,
      current_lap: Number(currentLap) || 1,
      current_compound: currentCompound,
      current_tyre_age: Number(currentTyreAge) || 0,
      remaining_laps: Number(remainingLaps) || 1,
    }
    // scenarios and pit_laps/compounds are mutually exclusive on the backend
    // (SimulateStrategyRequest._validate_pit_plan), so each mode sends only
    // its own; the scenario label defaults to "Pit lap N" as on web.
    const payload: SimulateStrategyRequest =
      mode === "compare"
        ? {
            ...basePayload,
            scenarios: scenarios.map((row) => ({
              pit_laps: [row.lap],
              compounds: [row.compound],
              label: row.label.trim() || `Pit lap ${row.lap}`,
            })),
          }
        : {
            ...basePayload,
            pit_laps: pitStops.map((row) => row.lap),
            compounds: pitStops.map((row) => row.compound),
          }
    // A bad current_lap (validate_current_lap, see CLAUDE.md's Deferred
    // Wiring) rejects synchronously here with a 404/422 — stay on step 2 and
    // surface it via simulateMutation.error below instead of advancing to
    // step 3's spinner, which would otherwise strand the user with no task
    // ever created and no FAILURE condition to show a "Try Again".
    haptics.confirm()
    try {
      const accepted = await simulateMutation.mutateAsync(payload)
      setTaskId(accepted.task_id)
      setStep(3)
    } catch (error) {
      // Rendered from simulateMutation.error in step 2's JSX. The daily
      // scenario limit (429) is a warning, anything else an error.
      if (isAxiosError(error) && error.response?.status === 429) haptics.warning()
      else haptics.error()
    }
  }

  function handleReset() {
    setStep(1)
    setTaskId(null)
    simulateMutation.reset()
  }

  const step1Valid = sessionId !== "" && driverId !== "" && Number(remainingLaps) > 0
  // The backend rejects an empty scenarios list; an empty single plan is the
  // valid "let the simulation decide" case.
  const step2Valid = mode === "single" || scenarios.length > 0

  const strategies = simulationResult.data?.result?.strategies ?? []
  const startingPosition = simulationResult.data?.result?.starting_position ?? 0
  const chartData = strategies.map((strategy, index) => ({
    name: strategyLabel(strategy, index),
    gain: strategy.position_gain_loss > 0 ? strategy.position_gain_loss : 0,
    loss: strategy.position_gain_loss < 0 ? strategy.position_gain_loss : 0,
  }))

  let sessionText: string
  let sessionNote: string | null = null
  if (paramSessionId) {
    sessionText = paramRace
      ? `${paramRace.event_name ?? paramRace.circuit?.name ?? "Race"} — ${paramRace.season} Round ${paramRace.round_number}`
      : "Loading race…"
  } else if (isLiveSessionMode) {
    sessionText = `${currentRace?.event_name ?? currentRace?.circuit?.name ?? "Current race"} — Race`
    sessionNote = "live"
  } else if (lastIngestedSession) {
    sessionText = `${lastIngestedSession.event_name ?? lastIngestedSession.circuit_name} — ${lastIngestedSession.season} Round ${lastIngestedSession.round_number}`
    sessionNote = "last ingested race"
  } else {
    sessionText = lastIngestedQuery.isLoading ? "Resolving last ingested race…" : "No ingested race available"
  }

  return (
    <ScrollView className="flex-1 bg-background" contentContainerClassName="p-4">
      <StepHeader step={step} />

      {step === 1 && (
        <View className="gap-4">
          <View>
            <FieldLabel>Session</FieldLabel>
            <View className="rounded-md border border-white/10 bg-surface px-3 py-2.5">
              <Text className={`text-base ${sessionId ? "text-foreground" : "text-muted"}`}>{sessionText}</Text>
              {sessionNote && <Text className="text-xs text-muted">{sessionNote}</Text>}
            </View>
          </View>
          <View>
            <FieldLabel>Driver</FieldLabel>
            <View className={pickerWrapperClassName}>
              <Picker
                selectedValue={driverId}
                onValueChange={setDriverId}
                dropdownIconColor="#fafafa"
                itemStyle={PICKER_ITEM_STYLE}
              >
                <Picker.Item label="Select a driver…" value="" color="#555" />
                {activeDrivers.map((driver) => (
                  <Picker.Item
                    key={driver.id}
                    label={`${driver.code} — ${driver.full_name}`}
                    value={driver.id}
                    color="#fafafa"
                  />
                ))}
              </Picker>
            </View>
          </View>
          <View className="flex-row gap-3">
            <View className="flex-1">
              <FieldLabel>Current Lap</FieldLabel>
              <TextInput
                value={currentLap}
                onChangeText={setCurrentLap}
                keyboardType="number-pad"
                className={inputClassName}
              />
            </View>
            <View className="flex-1">
              <FieldLabel>Remaining Laps</FieldLabel>
              <TextInput
                value={remainingLaps}
                onChangeText={setRemainingLaps}
                keyboardType="number-pad"
                className={inputClassName}
              />
            </View>
          </View>
          <View className="flex-row gap-3">
            <View className="flex-1">
              <FieldLabel>Current Compound</FieldLabel>
              <CompoundPicker value={currentCompound} onChange={setCurrentCompound} />
            </View>
            <View className="flex-1">
              <FieldLabel>Tyre Age (laps)</FieldLabel>
              <TextInput
                value={currentTyreAge}
                onChangeText={setCurrentTyreAge}
                keyboardType="number-pad"
                className={inputClassName}
              />
            </View>
          </View>
          <Pressable
            disabled={!step1Valid}
            onPress={() => setStep(2)}
            className="items-center rounded-md bg-foreground py-3"
            style={{ opacity: step1Valid ? 1 : DISABLED_OPACITY }}
          >
            <Text className="text-base font-semibold text-background">Next: Design Strategy</Text>
          </Pressable>
        </View>
      )}

      {step === 2 && (
        <View className="gap-4">
          <View className="flex-row gap-2" accessibilityLabel="Simulation mode">
            <ModeButton label="Single Plan" active={mode === "single"} onPress={() => selectMode("single")} />
            <ModeButton
              label="Compare Scenarios"
              active={mode === "compare"}
              onPress={() => selectMode("compare")}
            />
          </View>

          {mode === "single" ? (
            <>
              <Text className="text-xs text-muted">
                Add planned pit stops (lap + compound). Leave empty to let the Monte Carlo simulation
                decide pit timing autonomously.
              </Text>
              <FlatList
                data={pitStops}
                keyExtractor={(_, index) => String(index)}
                scrollEnabled={false}
                ItemSeparatorComponent={() => <View className="h-2" />}
                renderItem={({ item: row, index }) => (
                  <View className="flex-row items-center gap-2">
                    <TextInput
                      value={String(row.lap)}
                      onChangeText={(text) => updatePitStop(index, { lap: Number(text) || 0 })}
                      keyboardType="number-pad"
                      className={`${inputClassName} w-20`}
                    />
                    <View className="flex-1">
                      <CompoundPicker
                        value={row.compound}
                        onChange={(value) => updatePitStop(index, { compound: value })}
                      />
                    </View>
                    <RemoveButton label={`Remove pit stop ${index + 1}`} onPress={() => removePitStop(index)} />
                  </View>
                )}
              />
              <Pressable onPress={addPitStop} className="items-center rounded-md border border-white/10 py-2.5">
                <Text className="text-sm font-medium text-foreground">+ Add Pit Stop</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text className="text-xs text-muted">
                Compare up to {MAX_SCENARIOS} candidate pit laps side by side. Each scenario shares the
                same random race conditions (safety cars, lap-time variance), so any difference between
                them reflects the pit-lap decision alone, not chance.
              </Text>
              {/* A row per scenario was too wide for a phone (web puts lap,
                  compound, label and remove on one line): the lap and label
                  share a line, the compound wheel sits below. */}
              {scenarios.map((row, index) => (
                <View key={index} className="flex-row gap-2">
                  <View
                    className="w-1 rounded-full"
                    style={{ backgroundColor: SCENARIO_SERIES_COLORS[index % SCENARIO_SERIES_COLORS.length] }}
                  />
                  <View className="flex-1 gap-2">
                    <View className="flex-row items-center gap-2">
                      <TextInput
                        value={String(row.lap)}
                        onChangeText={(text) => updateScenario(index, { lap: Number(text) || 0 })}
                        keyboardType="number-pad"
                        accessibilityLabel={`Scenario ${index + 1} pit lap`}
                        className={`${inputClassName} w-20`}
                      />
                      <TextInput
                        value={row.label}
                        onChangeText={(text) => updateScenario(index, { label: text })}
                        placeholder={`Pit lap ${row.lap}`}
                        placeholderTextColor="#555"
                        accessibilityLabel={`Scenario ${index + 1} label`}
                        className={`${inputClassName} flex-1`}
                      />
                      <RemoveButton label={`Remove scenario ${index + 1}`} onPress={() => removeScenario(index)} />
                    </View>
                    <CompoundPicker
                      value={row.compound}
                      onChange={(value) => updateScenario(index, { compound: value })}
                    />
                  </View>
                </View>
              ))}
              <Pressable
                onPress={addScenario}
                disabled={scenarios.length >= MAX_SCENARIOS}
                className="items-center rounded-md border border-white/10 py-2.5"
                style={{ opacity: scenarios.length >= MAX_SCENARIOS ? DISABLED_OPACITY : 1 }}
              >
                <Text className="text-sm font-medium text-foreground">
                  + Add Scenario ({scenarios.length}/{MAX_SCENARIOS})
                </Text>
              </Pressable>
            </>
          )}

          {scenariosLeft !== null && (
            <Text className="text-xs text-muted">
              {scenariosLeft} simulation {scenariosLeft === 1 ? "scenario" : "scenarios"} left today.
              Resets at 00:00 UTC.
              {overQuota &&
                (scenariosLeft === 0
                  ? " Come back tomorrow to run more."
                  : ` This run needs ${scenariosNeeded}; remove scenarios to fit.`)}
            </Text>
          )}
          {simulateMutation.isError && (
            <Text role="alert" className="text-sm font-medium text-destructive">
              {getApiErrorMessage(simulateMutation.error, "Failed to start simulation")}
            </Text>
          )}
          <View className="flex-row gap-3">
            <Pressable
              onPress={() => setStep(1)}
              className="flex-1 items-center rounded-md border border-white/10 py-3"
            >
              <Text className="text-base font-semibold text-foreground">Back</Text>
            </Pressable>
            <Pressable
              disabled={!step2Valid || overQuota}
              onPress={() => void handleRunSimulation()}
              className="flex-1 items-center rounded-md bg-foreground py-3"
              style={{ opacity: !step2Valid || overQuota ? DISABLED_OPACITY : 1 }}
            >
              <Text className="text-base font-semibold text-background">Run Simulation</Text>
            </Pressable>
          </View>
        </View>
      )}

      {step === 3 && (
        <View className="items-center gap-4 py-16">
          {/* The worker starts on demand in production (about a minute from
              cold): slowStart explains a slow first result, timedOut (3 min)
              stops waiting. Same as web/desktop. */}
          {simulationResult.data?.status !== "FAILURE" && simulationResult.timedOut ? (
            <Text className="text-center text-sm text-muted">
              The simulation engine didn&apos;t respond in time. Please try again in a minute.
            </Text>
          ) : (
            <>
              <ActivityIndicator size="large" color="#fafafa" />
              <Text className="text-sm text-muted">
                {simulationResult.data?.status === "FAILURE"
                  ? (simulationResult.data.error ?? "Simulation failed.")
                  : "Running Monte Carlo simulation…"}
              </Text>
              {simulationResult.data?.status !== "FAILURE" && simulationResult.slowStart && (
                <Text className="text-center text-xs text-muted">
                  If the simulation engine was asleep, it takes about a minute to start.
                </Text>
              )}
            </>
          )}
          {(simulationResult.data?.status === "FAILURE" || simulationResult.timedOut) && (
            <Pressable onPress={handleReset} className="rounded-md border border-white/10 px-4 py-2">
              <Text className="text-sm font-medium text-foreground">Try Again</Text>
            </Pressable>
          )}
        </View>
      )}

      {step === 4 && (
        <View className="gap-4">
          <Text className="text-base font-semibold text-foreground">
            Predicted Position Change by Strategy
          </Text>
          {chartData.length === 0 ? (
            <Text className="text-sm text-muted">No strategy variants returned.</Text>
          ) : (
            <View style={{ height: Math.max(120, chartData.length * CHART_HEIGHT_PER_ROW) }}>
              {/* Horizontal bars via victory-native's CartesianChart +
                  HorizontalBar. Per-bar gain/loss colouring isn't a built-in
                  prop (one `color` per HorizontalBar), so two synthetic
                  series (gain/loss, whichever applies is nonzero) are drawn
                  as two differently coloured layers, same result as web's
                  <Cell fill>. chartData is passed inline so CartesianChart's
                  generic RawData infers from the literal. */}
              <CartesianChart
                data={chartData}
                xKey="name"
                yKeys={["gain", "loss"]}
                orientation="horizontal"
                domainPadding={{ left: 16, right: 16, top: 16, bottom: 16 }}
                axisOptions={{ font: chartFont, labelColor: AXIS_LABEL_COLOR, lineColor: AXIS_LINE_COLOR }}
              >
                {({ points, chartBounds }) => (
                  <>
                    <HorizontalBar
                      points={points.gain}
                      chartBounds={chartBounds}
                      color={GAIN_COLOR}
                      roundedCorners={{ topRight: 4, bottomRight: 4 }}
                    />
                    <HorizontalBar
                      points={points.loss}
                      chartBounds={chartBounds}
                      color={LOSS_COLOR}
                      roundedCorners={{ topLeft: 4, bottomLeft: 4 }}
                    />
                  </>
                )}
              </CartesianChart>
            </View>
          )}

          {/* Web's Strategy / Change / Finish Time / Finish Time Range table,
              as one card per strategy: four columns didn't fit a phone. The
              range is the Monte Carlo confidence_interval. */}
          {strategies.map((strategy, index) => (
            <View
              key={index}
              className="flex-row gap-3 rounded-md border border-white/10 bg-surface p-3"
            >
              <View
                className="w-1 rounded-full"
                style={{ backgroundColor: SCENARIO_SERIES_COLORS[index % SCENARIO_SERIES_COLORS.length] }}
              />
              <View className="flex-1 gap-1">
                <View className="flex-row items-baseline justify-between gap-2">
                  <Text numberOfLines={1} className="flex-1 text-sm font-semibold text-foreground">
                    {strategyLabel(strategy, index)}
                  </Text>
                  <Text
                    className="font-mono text-base font-semibold"
                    style={{ color: strategy.position_gain_loss >= 0 ? GAIN_COLOR : LOSS_COLOR }}
                  >
                    {formatChange(strategy.position_gain_loss)}
                  </Text>
                </View>
                <View className="flex-row justify-between">
                  <Text className="text-xs text-muted">Finish time</Text>
                  <Text className="font-mono text-xs text-foreground">
                    {formatRaceTime(strategy.predicted_finish_time)}
                  </Text>
                </View>
                <View className="flex-row justify-between">
                  <Text className="text-xs text-muted">Range</Text>
                  <Text className="font-mono text-xs text-muted">
                    {formatRaceTime(strategy.confidence_interval[0])}–
                    {formatRaceTime(strategy.confidence_interval[1])}
                  </Text>
                </View>
              </View>
            </View>
          ))}

          {strategies.length > 0 && (
            <View className="gap-2 border-t border-white/10" style={{ paddingTop: 16 }}>
              <Text className="text-sm font-semibold text-foreground">Finishing Position Distribution</Text>
              <Text className="text-xs text-muted">
                Full probability breakdown from the same 1000-simulation Monte Carlo run — the
                risk/reward profile behind each plan&apos;s headline position change above.
              </Text>
              <PositionDistributionChart strategies={strategies} startingPosition={startingPosition} />
            </View>
          )}

          {strategies.length > 0 && (
            <View className="gap-3">
              {strategies.map((strategy, index) => (
                <PlanExplanationCard
                  key={index}
                  planLabel={strategy.label ?? `Plan ${index + 1}`}
                  strategy={strategy}
                />
              ))}
            </View>
          )}
          <Pressable onPress={handleReset} className="items-center rounded-md border border-white/10 py-3">
            <Text className="text-base font-semibold text-foreground">Run Another Simulation</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
  )
}
