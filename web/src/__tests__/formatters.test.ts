import { describe, expect, it } from "vitest"
import { COMPOUND_COLORS } from "@/utils/constants"
import { formatLapTime, formatRaceTime } from "@/utils/formatters"

describe("formatLapTime", () => {
  it("formats a time past a minute as m:ss.sss", () => {
    expect(formatLapTime(90.123)).toBe("1:30.123")
  })

  // formatLapTime returns bare seconds (no "0:" prefix) below a minute — see
  // its own docstring: "1:31.234" once past a minute, "31.234" below it.
  // 0 falls in the below-a-minute branch, so "0.000" is correct, not "0:00.000".
  it("formats 0 as bare seconds, not m:ss.sss", () => {
    expect(formatLapTime(0)).toBe("0.000")
  })
})

describe("formatRaceTime", () => {
  it("formats a time past an hour as h:mm:ss.sss", () => {
    // 1h16m44.567s = 3600 + 960 + 44.567 = 4604.567 — VER's real Belgian GP
    // 2026 R10 finish time (see docs/day-deferred-fixes-session2-handoff.md
    // Test 2), a realistic full-race magnitude now that predicted_finish_time
    // is a genuine absolute elapsed time (race_simulator.py's
    // baseline_lap_time_seconds), not a small relative delta.
    expect(formatRaceTime(4604.567)).toBe("1:16:44.567")
  })

  it("delegates to formatLapTime below an hour", () => {
    expect(formatRaceTime(90.123)).toBe(formatLapTime(90.123))
    expect(formatRaceTime(30.5)).toBe(formatLapTime(30.5))
  })

  it("pads minutes and seconds within the hour segment", () => {
    // 2h03m05.000s — minutes and seconds must each be zero-padded even
    // though formatLapTime's own m:ss.sss form never pads minutes.
    expect(formatRaceTime(2 * 3600 + 3 * 60 + 5)).toBe("2:03:05.000")
  })

  it("returns an em dash for null/undefined, matching formatLapTime", () => {
    expect(formatRaceTime(null)).toBe("—")
    expect(formatRaceTime(undefined)).toBe("—")
  })
})

describe("COMPOUND_COLORS", () => {
  it("has a color entry for each of the 5 race compounds (S/M/H/I/W)", () => {
    expect(COMPOUND_COLORS.SOFT).toBeTruthy()
    expect(COMPOUND_COLORS.MEDIUM).toBeTruthy()
    expect(COMPOUND_COLORS.HARD).toBeTruthy()
    expect(COMPOUND_COLORS.INTERMEDIATE).toBeTruthy()
    expect(COMPOUND_COLORS.WET).toBeTruthy()
  })
})
