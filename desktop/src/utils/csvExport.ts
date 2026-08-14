import { invoke } from "@tauri-apps/api/core"
import { save } from "@tauri-apps/plugin-dialog"
import { writeTextFile } from "@tauri-apps/plugin-fs"
import type { SimulatedRaceOutcome } from "@/types"

const CSV_HEADER = ["Strategy Plan", "Pit Laps", "Position Change", "Finish Time (s)", "Confidence Interval (s)"]

function escapeCsvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

function buildCsv(strategies: SimulatedRaceOutcome[]): string {
  const rows = strategies.map((strategy, index) => [
    `Plan ${index + 1}`,
    strategy.pit_laps.join(" | "),
    String(strategy.position_gain_loss),
    strategy.predicted_finish_time.toFixed(3),
    `${strategy.confidence_interval[0].toFixed(3)}–${strategy.confidence_interval[1].toFixed(3)}`,
  ])
  return [CSV_HEADER, ...rows].map((row) => row.map(escapeCsvField).join(",")).join("\n")
}

// Returns false if the user cancelled the save dialog, true once the file is
// written. allow_csv_export_path (Rust) grants the fs plugin write access to
// exactly this one user-chosen path before writeTextFile runs — fs:default
// alone only covers the app's own data directories, not arbitrary paths.
export async function exportStrategiesAsCsv(strategies: SimulatedRaceOutcome[]): Promise<boolean> {
  const path = await save({
    defaultPath: "strategy-simulation.csv",
    filters: [{ name: "CSV", extensions: ["csv"] }],
  })
  if (!path) return false
  await invoke("allow_csv_export_path", { path })
  await writeTextFile(path, buildCsv(strategies))
  return true
}
