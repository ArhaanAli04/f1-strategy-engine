import { Picker } from "@react-native-picker/picker"
import { TitilliumWeb_400Regular } from "@expo-google-fonts/titillium-web/400Regular"
import { useFont } from "@shopify/react-native-skia"
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
import { useDriverLaps } from "@/hooks/useDriverLaps"
import { useDrivers } from "@/hooks/useDrivers"
import { useSimulateStrategy, useSimulationResult } from "@/hooks/useStrategy"
import { useSessionStore } from "@/stores/sessionStore"
import { isActiveDriver } from "@/utils/drivers"
import { getApiErrorMessage } from "@/utils/errors"
import type { SimulateStrategyRequest } from "@/types"

type Step = 1 | 2 | 3 | 4

interface PitStopRow {
  lap: number
  compound: string
}

// Backend validates each compounds[] entry against exactly this set
// (backend/schemas/simulate_schema.py) — same list as web/desktop.
const COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]

const STEP_LABELS: Record<Step, string> = {
  1: "Session & Driver",
  2: "Design Strategy",
  3: "Simulating",
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
              className={`text-center text-[9px] uppercase tracking-wide ${
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

const inputClassName = "rounded-md border border-white/10 bg-surface px-3 py-2.5 text-base text-foreground"
const pickerWrapperClassName = "overflow-hidden rounded-md border border-white/10 bg-surface"

const CHART_HEIGHT_PER_ROW = 56
const GAIN_COLOR = "#10B981"
const LOSS_COLOR = "#EF4444"
const AXIS_LABEL_COLOR = "#9ca3af"
const AXIS_LINE_COLOR = "rgba(255,255,255,0.1)"

// Simulator screen — reachable via the "Run Simulator" button on the
// Strategy tab (Day 32 spec's explicit choice over a 6th tab). Ports the
// same 4-step flow as web/src/pages/SimulatorPage.tsx and
// desktop/src/pages/SimulatorPage.tsx: session ID stays a plain manual text
// input like desktop's version (no web-only live-mode auto-detect), no CSV
// export (desktop-exclusive), no drag-drop (add/remove buttons only).
export default function SimulatorScreen() {
  const selectedSessionId = useSessionStore((state) => state.selectedSessionId)
  const { data: drivers } = useDrivers()
  const activeDrivers = (drivers ?? []).filter(isActiveDriver)
  // Skia's Canvas has its own independent font subsystem from RN Text/
  // expo-font — reuses the same bundled Titillium Web .ttf as a second load.
  // Called unconditionally (Rules of Hooks) even though it's only rendered
  // in step 4 — same as every other hook in this component.
  const chartFont = useFont(TitilliumWeb_400Regular, 10)

  const [step, setStep] = useState<Step>(1)
  const [sessionId, setSessionId] = useState(selectedSessionId ?? "")
  const [driverId, setDriverId] = useState("")
  const [currentLap, setCurrentLap] = useState("1")
  const [currentCompound, setCurrentCompound] = useState("MEDIUM")
  const [currentTyreAge, setCurrentTyreAge] = useState("0")
  const [remainingLaps, setRemainingLaps] = useState("20")
  const [pitStops, setPitStops] = useState<PitStopRow[]>([{ lap: 15, compound: "HARD" }])
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

  useEffect(() => {
    if (simulationResult.data?.status === "SUCCESS") setStep(4)
  }, [simulationResult.data?.status])

  function addPitStop() {
    setPitStops((rows) => [...rows, { lap: Number(remainingLaps) || 1, compound: "HARD" }])
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
      current_lap: Number(currentLap) || 1,
      current_compound: currentCompound,
      current_tyre_age: Number(currentTyreAge) || 0,
      remaining_laps: Number(remainingLaps) || 1,
      pit_laps: pitStops.map((row) => row.lap),
      compounds: pitStops.map((row) => row.compound),
    }
    // A bad current_lap (validate_current_lap, see CLAUDE.md's Deferred
    // Wiring) rejects synchronously here with a 404/422 — stay on step 2 and
    // surface it via simulateMutation.error below instead of advancing to
    // step 3's spinner, which would otherwise strand the user with no task
    // ever created and no FAILURE condition to show a "Try Again". Mirrors
    // web/src/pages/SimulatorPage.tsx and desktop's copy.
    try {
      const accepted = await simulateMutation.mutateAsync(payload)
      setTaskId(accepted.task_id)
      setStep(3)
    } catch {
      // Rendered from simulateMutation.error in step 2's JSX — nothing more
      // to do here.
    }
  }

  function handleReset() {
    setStep(1)
    setTaskId(null)
    simulateMutation.reset()
  }

  const step1Valid = sessionId.trim() !== "" && driverId !== "" && Number(remainingLaps) > 0

  const strategies = simulationResult.data?.result?.strategies ?? []
  const chartData = strategies.map((strategy, index) => ({
    name: `Plan ${index + 1}`,
    gain: strategy.position_gain_loss > 0 ? strategy.position_gain_loss : 0,
    loss: strategy.position_gain_loss < 0 ? strategy.position_gain_loss : 0,
  }))

  return (
    <ScrollView className="flex-1 bg-background" contentContainerClassName="p-4">
      <StepHeader step={step} />

      {step === 1 && (
        <View className="gap-4">
          <View>
            <FieldLabel>Session ID</FieldLabel>
            <TextInput
              value={sessionId}
              onChangeText={setSessionId}
              placeholder="Session UUID"
              placeholderTextColor="#555"
              autoCapitalize="none"
              className={inputClassName}
            />
          </View>
          <View>
            <FieldLabel>Driver</FieldLabel>
            <View className={pickerWrapperClassName}>
              <Picker selectedValue={driverId} onValueChange={setDriverId} dropdownIconColor="#fafafa">
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
              <View className={pickerWrapperClassName}>
                <Picker selectedValue={currentCompound} onValueChange={setCurrentCompound} dropdownIconColor="#fafafa">
                  {COMPOUNDS.map((compound) => (
                    <Picker.Item key={compound} label={compound} value={compound} color="#fafafa" />
                  ))}
                </Picker>
              </View>
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
            className="items-center rounded-md bg-foreground py-3 disabled:opacity-40"
          >
            <Text className="text-base font-semibold text-background">Next: Design Strategy</Text>
          </Pressable>
        </View>
      )}

      {step === 2 && (
        <View className="gap-4">
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
                <View className={`${pickerWrapperClassName} flex-1`}>
                  <Picker
                    selectedValue={row.compound}
                    onValueChange={(value) => updatePitStop(index, { compound: value })}
                    dropdownIconColor="#fafafa"
                  >
                    {COMPOUNDS.map((compound) => (
                      <Picker.Item key={compound} label={compound} value={compound} color="#fafafa" />
                    ))}
                  </Picker>
                </View>
                <Pressable
                  onPress={() => removePitStop(index)}
                  className="h-10 w-10 items-center justify-center rounded-md border border-white/10"
                  accessibilityLabel={`Remove pit stop ${index + 1}`}
                >
                  <Text className="text-lg text-destructive">×</Text>
                </Pressable>
              </View>
            )}
          />
          <Pressable
            onPress={addPitStop}
            className="items-center rounded-md border border-white/10 py-2.5"
          >
            <Text className="text-sm font-medium text-foreground">+ Add Pit Stop</Text>
          </Pressable>
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
              onPress={() => void handleRunSimulation()}
              className="flex-1 items-center rounded-md bg-foreground py-3"
            >
              <Text className="text-base font-semibold text-background">Run Simulation</Text>
            </Pressable>
          </View>
        </View>
      )}

      {step === 3 && (
        <View className="items-center gap-4 py-16">
          <ActivityIndicator size="large" color="#fafafa" />
          <Text className="text-sm text-muted">
            {simulationResult.data?.status === "FAILURE"
              ? (simulationResult.data.error ?? "Simulation failed.")
              : `Running Monte Carlo simulation… (${simulationResult.data?.status ?? "PENDING"})`}
          </Text>
          {simulationResult.data?.status === "FAILURE" && (
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
              {/* Horizontal bar chart via victory-native's CartesianChart +
                  HorizontalBar (confirmed against the installed source:
                  orientation="horizontal" keeps xKey as the category field
                  and yKeys as the numeric field, only the rendering
                  direction flips). Per-bar gain/loss coloring isn't a
                  built-in Bar/HorizontalBar prop (single `color` per
                  component instance, no Recharts-style per-Cell coloring) —
                  worked around with two synthetic y-series (gain/loss,
                  whichever applies to a given plan is nonzero) rendered as
                  two independently-colored HorizontalBar layers, same
                  visual result as web's <Cell fill={...}> without manual
                  Skia path geometry. `chartData` is passed inline here
                  (not through a separately-typed component prop) so
                  CartesianChart's generic RawData infers directly from the
                  literal — same working pattern as SectorComparison.tsx;
                  routing it through a named-interface prop first breaks
                  overload resolution (see Checkpoint 4 verification notes). */}
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
          {strategies.length > 0 && (
            <View className="gap-3">
              {strategies.map((strategy, index) => (
                <PlanExplanationCard key={index} planLabel={`Plan ${index + 1}`} strategy={strategy} />
              ))}
            </View>
          )}
          <Pressable
            onPress={handleReset}
            className="items-center rounded-md border border-white/10 py-3"
          >
            <Text className="text-base font-semibold text-foreground">Run Another Simulation</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
  )
}

