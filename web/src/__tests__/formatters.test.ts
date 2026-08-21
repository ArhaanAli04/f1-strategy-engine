import { describe, expect, it } from "vitest"
import { COMPOUND_COLORS } from "@/utils/constants"
import { formatLapTime } from "@/utils/formatters"

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

describe("COMPOUND_COLORS", () => {
  it("has a color entry for each of the 5 race compounds (S/M/H/I/W)", () => {
    expect(COMPOUND_COLORS.SOFT).toBeTruthy()
    expect(COMPOUND_COLORS.MEDIUM).toBeTruthy()
    expect(COMPOUND_COLORS.HARD).toBeTruthy()
    expect(COMPOUND_COLORS.INTERMEDIATE).toBeTruthy()
    expect(COMPOUND_COLORS.WET).toBeTruthy()
  })
})
