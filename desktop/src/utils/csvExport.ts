import { invoke } from "@tauri-apps/api/core"
import { save } from "@tauri-apps/plugin-dialog"
import { writeTextFile } from "@tauri-apps/plugin-fs"
import type { SimulatedRaceOutcome } from "@/types"

// Mean Position/P(Gain)/P(Hold)/P(Lose) added alongside the What-If
// Simulator multi-scenario rebuild (see
// docs/core-feature-rebuild-whatif-simulator.md) — the same breakdown
// web/desktop's PositionDistributionChart.tsx renders, exported here too
// since CSV export is desktop-exclusive functionality that should reflect
// the full richness of a simulation result, not just its pre-existing
// summary fields.
const CSV_HEADER = [
  "Strategy Plan",
  "Pit Laps",
  "Position Change",
  "Mean Position",
  "P(Gain)",
  "P(Hold)",
  "P(Lose)",
  "Finish Time (s)",
  "Confidence Interval (s)",
]

function escapeCsvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

// Same split as PositionDistributionChart.tsx's risk/reward table — kept as
// a small local duplicate (not a shared import) since desktop's chart
// component and this CSV exporter have no other coupling and the
// calculation is a few lines of pure arithmetic, not worth a shared module.
function splitGainHoldLose(
  strategy: SimulatedRaceOutcome,
  startingPosition: number,
): { gain: number; hold: number; lose: number } {
  const gain = strategy.position_probabilities
    .filter((p) => p.position < startingPosition)
    .reduce((sum, p) => sum + p.probability, 0)
  const hold =
    strategy.position_probabilities.find((p) => p.position === startingPosition)?.probability ?? 0
  const lose = strategy.position_probabilities
    .filter((p) => p.position > startingPosition)
    .reduce((sum, p) => sum + p.probability, 0)
  return { gain, hold, lose }
}

function buildCsv(strategies: SimulatedRaceOutcome[], startingPosition: number): string {
  const rows = strategies.map((strategy, index) => {
    const { gain, hold, lose } = splitGainHoldLose(strategy, startingPosition)
    return [
      strategy.label ?? `Plan ${index + 1}`,
      strategy.pit_laps.join(" | "),
      String(strategy.position_gain_loss),
      strategy.mean_position.toFixed(2),
      `${(gain * 100).toFixed(0)}%`,
      `${(hold * 100).toFixed(0)}%`,
      `${(lose * 100).toFixed(0)}%`,
      strategy.predicted_finish_time.toFixed(3),
      `${strategy.confidence_interval[0].toFixed(3)}–${strategy.confidence_interval[1].toFixed(3)}`,
    ]
  })
  return [CSV_HEADER, ...rows].map((row) => row.map(escapeCsvField).join(",")).join("\n")
}

// Returns false if the user cancelled the save dialog, true once the file is
// written. allow_csv_export_path (Rust) grants the fs plugin write access to
// exactly this one user-chosen path before writeTextFile runs — fs:default
// alone only covers the app's own data directories, not arbitrary paths.
// startingPosition: SimulateStrategyResponse.starting_position — needed to
// split each strategy's position_probabilities into P(Gain)/P(Hold)/P(Lose).
export async function exportStrategiesAsCsv(
  strategies: SimulatedRaceOutcome[],
  startingPosition: number,
): Promise<boolean> {
  const path = await save({
    defaultPath: "strategy-simulation.csv",
    filters: [{ name: "CSV", extensions: ["csv"] }],
  })
  if (!path) return false
  await invoke("allow_csv_export_path", { path })
  await writeTextFile(path, buildCsv(strategies, startingPosition))
  return true
}
