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
