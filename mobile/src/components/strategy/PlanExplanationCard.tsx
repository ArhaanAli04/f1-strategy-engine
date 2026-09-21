import { FlatList, Text, View } from "react-native"
import { useDrivers } from "@/hooks/useDrivers"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { DriverResponse, OvertakingDriver, SimulatedRaceOutcome } from "@/types"

interface PlanExplanationCardProps {
  planLabel: string
  strategy: SimulatedRaceOutcome
}

function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${Math.abs(count) === 1 ? "" : "s"}`
}

// Summarizes an OvertakingDriver row's real Monte Carlo enrichment fields
// (What-If Simulator rebuild part (b), see docs/core-feature-rebuild-whatif-
// simulator.md §7) into one short line — RN port of the identical web/
// desktop helper. Each piece is independently optional (see OvertakingDriver's
// own docstring — both null together, never one set without the other for
// the pit-lap pair) — renders whichever pieces are available, joined by
// " · ", or "" (falsy, so the caller can skip rendering entirely) when
// neither is available.
function formatOvertakingEnrichment(entry: OvertakingDriver): string {
  const parts: string[] = []
  if (entry.finish_ahead_probability != null) {
    parts.push(`${Math.round(entry.finish_ahead_probability * 100)}% chance you finish ahead`)
  }
  if (entry.rival_projected_pit_lap != null && entry.rival_pit_probability != null) {
    parts.push(
      `pits ~lap ${entry.rival_projected_pit_lap} (${Math.round(entry.rival_pit_probability * 100)}%)`,
    )
  }
  return parts.join(" · ")
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

  // fresh_tyre_gain_per_lap is now a real tire_deg-model projection (backend
  // What-If Simulator rebuild part (a) — see docs/core-feature-rebuild-
  // whatif-simulator.md §7), not the old hardcoded per-compound constant —
  // it can legitimately come out NEGATIVE when the new compound is a worse
  // choice for the remaining laps than staying out would have been (e.g. a
  // dry-track INTERMEDIATE pit). isFasterOnFreshTyre picks which half of the
  // sentence applies; >= 0 (not > 0) so an exact-zero projection still reads
  // as a (trivial) "faster" statement rather than needing a third branch.
  const isFasterOnFreshTyre = explanation.fresh_tyre_gain_per_lap >= 0
  const degradationMessage = isFasterOnFreshTyre
    ? `Fresh ${freshCompound} tyre pace: ~${explanation.fresh_tyre_gain_per_lap.toFixed(1)}s/lap faster than staying out over ${pluralize(explanation.remaining_laps, "lap")}, recovering ~${explanation.total_recoverable_seconds.toFixed(1)}s of the ${explanation.pit_cost_seconds.toFixed(1)}s pit-stop cost.`
    : `Fresh ${freshCompound} tyre pace: ~${Math.abs(explanation.fresh_tyre_gain_per_lap).toFixed(1)}s/lap SLOWER than staying out over ${pluralize(explanation.remaining_laps, "lap")} — this pit adds ~${Math.abs(explanation.total_recoverable_seconds).toFixed(1)}s on top of the ${explanation.pit_cost_seconds.toFixed(1)}s pit-stop cost, instead of recovering it.`

  const heading = isGain
    ? `Why ${planLabel} gains ${pluralize(position_gain_loss, "position")}`
    : isLoss
      ? `Why ${planLabel} loses ${pluralize(Math.abs(position_gain_loss), "position")}`
      : `Why ${planLabel} doesn't change your position`

  const driverListLabel = isGain
    ? "Drivers you overtake after pit"
    : "Drivers who overtook you in pitstop"
  const arrowLabel = isGain ? "you overtake" : "now ahead of you"

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
              const enrichmentText = formatOvertakingEnrichment(entry)
              return (
                <View className="gap-0.5 py-0.5">
                  <View className="flex-row items-center gap-2">
                    <Text className="w-7 font-mono text-xs text-muted">P{entry.position}</Text>
                    <View
                      className="h-3 w-1 rounded-full"
                      style={{ backgroundColor: teamColor }}
                    />
                    <Text className="w-10 font-mono text-xs font-semibold text-foreground">
                      {driver?.code ?? "???"}
                    </Text>
                    <Text className="flex-1 font-mono text-xs text-muted">
                      +{entry.gap_seconds.toFixed(1)}s behind {arrowLabel}
                    </Text>
                  </View>
                  {/* Real Monte Carlo outputs from the SAME simulate_race call
                      behind position_gain_loss above — What-If Simulator
                      rebuild part (b), see docs/core-feature-rebuild-whatif-
                      simulator.md §7. Rendered only when at least one piece
                      is available (both fields can independently be null —
                      see OvertakingDriver's own docstring), never a
                      fabricated placeholder. */}
                  {enrichmentText ? (
                    <Text className="pl-9 font-mono text-[10px] text-muted">{enrichmentText}</Text>
                  ) : null}
                </View>
              )
            }}
          />
        </View>
      )}

      {/* fresh_tyre_gain_per_lap/total_recoverable_seconds are a real
          tire_deg-model projection as of the What-If Simulator rebuild part
          (a) fix (see docs/core-feature-rebuild-whatif-simulator.md §7) —
          the OLD compound continuing to degrade vs. the NEW compound
          starting fresh, both projected over the laps remaining after this
          plan's last pit stop. This can genuinely come out negative (the
          new compound projected SLOWER than staying out — see
          isFasterOnFreshTyre above), which the old hardcoded constant could
          never represent. Still deliberately does NOT assert a "sufficient"/
          "not enough to recover" verdict about POSITION — mirrors web/src/
          pages/SimulatorPage.tsx's Option 3 fix: drivers_overtaken above is
          a frozen current-lap snapshot, and this line's rival-pace
          assumption (see the trailing sentence below) is unchanged by this
          fix — the real Monte Carlo simulation behind position_gain_loss
          models every rival's own tyre wear and pit decisions lap by lap,
          which this simplified pace comparison does not. */}
      {explanation.remaining_laps > 0 && freshCompound && (
        <Text className="text-xs text-muted">
          {`${degradationMessage} This is a simplified snapshot that assumes rivals hold their current pace with no further pit stops of their own — the Monte Carlo position change above already accounts for rivals' own tyre wear and pit stops, so treat that as the number to trust and this line as partial context, not the full picture.`}
        </Text>
      )}
    </View>
  )
}
