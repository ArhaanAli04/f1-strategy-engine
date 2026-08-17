import { FlatList, Text, View } from "react-native"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { DriverResponse, SimulatedRaceOutcome } from "@/types"

interface PlanExplanationCardProps {
  planLabel: string
  strategy: SimulatedRaceOutcome
}

function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${Math.abs(count) === 1 ? "" : "s"}`
}

const GAIN_COLOR = "#10B981"
const LOSS_COLOR = "#EF4444"

// RN port of the inline PlanExplanationCard in web/src/pages/SimulatorPage.tsx
// (desktop's copy-and-adapted version is identical apart from CSV export,
// not relevant here). drivers_overtaken rows use LiveTimingTower's
// team-color-bar + code convention (see app/(tabs)/live.tsx), same choice
// web/desktop made for the same reason — not DriverChip's pill style.
export function PlanExplanationCard({ planLabel, strategy }: PlanExplanationCardProps) {
  const { data: drivers } = useDrivers()
  const driversById = new Map<string, DriverResponse>()
  for (const driver of drivers ?? []) driversById.set(driver.id, driver)

  const { position_gain_loss, explanation } = strategy
  const isGain = position_gain_loss > 0
  const isLoss = position_gain_loss < 0
  const freshCompound = strategy.compounds.at(-1)

  const heading = isGain
    ? `Why ${planLabel} gains ${pluralize(position_gain_loss, "position")}`
    : isLoss
      ? `Why ${planLabel} loses ${pluralize(Math.abs(position_gain_loss), "position")}`
      : `Why ${planLabel} doesn't change your position`

  const driverListLabel = isGain
    ? "Drivers you overtake after pit"
    : "Drivers who overtook you in pitstop"
  const arrowLabel = isGain ? "you overtake" : "now ahead of you"

  const sufficient = explanation.total_recoverable_seconds >= explanation.pit_cost_seconds
  const headingColor = isGain ? GAIN_COLOR : isLoss ? LOSS_COLOR : "#fafafa"

  return (
    <View className="gap-3 rounded-lg border border-white/10 bg-surface p-4">
      <Text style={{ color: headingColor }} className="text-sm font-semibold">
        {heading}
      </Text>

      <Text className="text-xs text-muted">
        Pit stop cost:{" "}
        <Text className="font-mono text-foreground">{explanation.pit_cost_seconds.toFixed(1)}s</Text>
      </Text>

      {explanation.drivers_overtaken.length === 0 ? (
        <Text className="text-xs text-muted">
          No drivers within pit stop window — position unchanged by pit stop timing
        </Text>
      ) : (
        <View className="gap-1">
          <Text className="text-xs font-medium text-muted">{driverListLabel}</Text>
          {/* scrollEnabled=false — this card sits inside the Simulator's outer
              ScrollView (Step 4). Lists here are short (a handful of cars
              within one pit-stop window), so the nested-list perf cost RN
              normally warns about doesn't apply in practice. */}
          <FlatList
            data={explanation.drivers_overtaken}
            keyExtractor={(entry) => entry.driver_id}
            scrollEnabled={false}
            renderItem={({ item: entry }) => {
              const driver = driversById.get(entry.driver_id)
              const teamColor = driver?.contracts[0]?.team?.color_hex ?? FALLBACK_TEAM_COLOR
              return (
                <View className="flex-row items-center gap-2 py-0.5">
                  <Text className="w-7 font-mono text-xs text-muted">P{entry.position}</Text>
                  <View className="h-3 w-1 rounded-full" style={{ backgroundColor: teamColor }} />
                  <Text className="w-10 font-mono text-xs font-semibold text-foreground">
                    {driver?.code ?? "???"}
                  </Text>
                  <Text className="flex-1 font-mono text-xs text-muted">
                    +{entry.gap_seconds.toFixed(1)}s behind {arrowLabel}
                  </Text>
                </View>
              )
            }}
          />
        </View>
      )}

      {isLoss && (
        <Text className="text-xs text-muted">
          Only {pluralize(explanation.remaining_laps, "lap")} remaining after pit —{" "}
          {sufficient ? "sufficient" : "not enough"} to recover on fresh tyres.
        </Text>
      )}

      {explanation.fresh_tyre_gain_per_lap > 0 && freshCompound && (
        <Text className="text-xs text-muted">
          Fresh {freshCompound} tyre advantage: ~{explanation.fresh_tyre_gain_per_lap.toFixed(1)}s/lap —{" "}
          {isGain
            ? `recovers ~${explanation.total_recoverable_seconds.toFixed(1)}s over ${pluralize(explanation.remaining_laps, "lap")}, enough to pass ${pluralize(explanation.drivers_overtaken.length, "driver")}.`
            : isLoss
              ? `recovers only ~${explanation.total_recoverable_seconds.toFixed(1)}s in ${pluralize(explanation.remaining_laps, "lap")}.`
              : `roughly offsets the pit-stop loss over ${pluralize(explanation.remaining_laps, "lap")} (~${explanation.total_recoverable_seconds.toFixed(1)}s recovered).`}
        </Text>
      )}
    </View>
  )
}
