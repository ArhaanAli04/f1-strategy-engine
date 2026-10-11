import { COMPOUND_COLORS } from "./constants"

// Formats seconds as F1 broadcast-style lap/sector time: "1:31.234" once
// past a minute, "31.234" below it. null/undefined (no time set) -> "—".
export function formatLapTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—"
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds - minutes * 60
  if (minutes <= 0) return remainder.toFixed(3)
  return `${minutes}:${remainder.toFixed(3).padStart(6, "0")}`
}

// Formats seconds as a race clock: "1:16:44.000" once past an hour, otherwise
// delegates to formatLapTime ("16:44.000" past a minute, "44.000" below it).
// For a value that can genuinely span a full race (SimulatedRaceOutcome.
// predicted_finish_time/confidence_interval — see race_simulator.py's
// baseline_lap_time_seconds, which made this a real absolute elapsed time,
// not a small relative delta) — formatLapTime alone has no hours segment and
// would render a real ~90-minute race as "90:32.104", which reads as
// implausible lap/sector data rather than a real finish time.
// null/undefined -> "—".
export function formatRaceTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—"
  const hours = Math.floor(seconds / 3600)
  if (hours <= 0) return formatLapTime(seconds)
  const minutes = Math.floor((seconds - hours * 3600) / 60)
  const secondsRemainder = seconds - hours * 3600 - minutes * 60
  return `${hours}:${String(minutes).padStart(2, "0")}:${secondsRemainder.toFixed(3).padStart(6, "0")}`
}

// Formats a signed gap in seconds: "+0.234s" / "-1.052s". null/undefined -> "—".
export function formatGap(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—"
  const sign = seconds >= 0 ? "+" : "-"
  return `${sign}${Math.abs(seconds).toFixed(3)}s`
}

export function getCompoundColor(compound: string): string {
  return COMPOUND_COLORS[compound.toUpperCase()] ?? COMPOUND_COLORS.UNKNOWN
}

const COMPOUND_LABELS: Record<string, string> = {
  SOFT: "S",
  MEDIUM: "M",
  HARD: "H",
  INTERMEDIATE: "I",
  WET: "W",
}

export function getCompoundLabel(compound: string): string {
  return COMPOUND_LABELS[compound.toUpperCase()] ?? "?"
}
