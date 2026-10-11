import { render, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { AnimatedDriverDots, type DriverDotMeta } from "@/components/circuit/AnimatedDriverDots"
import type { CircuitOutlineTransform, DriverPosition } from "@/types"

// Identity-ish transform: rotation 0, no centering, unit scale, origin
// viewBox centre — so applyTransform(x, y) collapses to cx = -x, cy = y and
// the assertions can reason about raw coordinates directly.
const TRANSFORM: CircuitOutlineTransform = {
  rotation_degrees: 0,
  center_x: 0,
  center_y: 0,
  scale: 1,
  viewbox_center: 0,
}

const RENDER_DELAY_MS = 250

let mockNow = 0
let rafCallbacks: FrameRequestCallback[] = []

beforeEach(() => {
  mockNow = 0
  rafCallbacks = []
  vi.spyOn(performance, "now").mockImplementation(() => mockNow)
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafCallbacks.push(cb)
    return rafCallbacks.length
  })
  vi.stubGlobal("cancelAnimationFrame", () => {})
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function pos(x: number, y = 0): DriverPosition {
  return { driver_number: "44", x, y, z: null, timestamp: null }
}

function dotsEl(positions: DriverPosition[], prefersReducedMotion = false) {
  return (
    <svg>
      <AnimatedDriverDots
        positions={positions}
        transform={TRANSFORM}
        driverByCarNumber={new Map()}
        selectedDriverId={null}
        prefersReducedMotion={prefersReducedMotion}
        renderDelayMs={RENDER_DELAY_MS}
      />
    </svg>
  )
}

// Each driver is a <g> carrying the position (the dot, and for the selected
// driver its label, move together).
function dotTransform(container: HTMLElement): string {
  const group = container.querySelector<SVGGElement>("g[data-driver-number]")
  if (!group) throw new Error("no driver <g> rendered")
  return group.style.transform
}

function dotX(container: HTMLElement): number {
  const match = dotTransform(container).match(/translate\(\s*(-?[\d.]+)px/)
  if (!match) throw new Error(`no translate() in "${dotTransform(container)}"`)
  return Number(match[1])
}

// Advance the mock clock and run exactly one queued animation frame (which
// re-queues the next one, matching the component's self-perpetuating loop).
function frame(toMs: number) {
  mockNow = toMs
  const pending = rafCallbacks
  rafCallbacks = []
  act(() => {
    for (const cb of pending) cb(mockNow)
  })
}

describe("AnimatedDriverDots render-behind buffer", () => {
  it("keeps moving every frame while positions keep arriving (no stop-start pulse)", () => {
    // Poll cadence 200ms, render delay 250ms > cadence, so the cursor always
    // trails inside the last two samples and never catches the newest one.
    mockNow = 0
    const { container, rerender } = render(dotsEl([pos(0)]))

    mockNow = 200
    rerender(dotsEl([pos(20)]))
    mockNow = 400
    rerender(dotsEl([pos(40)]))
    const xAt400 = dotX(container)
    mockNow = 600
    rerender(dotsEl([pos(60)]))
    const xAt600 = dotX(container)
    mockNow = 800
    rerender(dotsEl([pos(80)]))
    const xAt800 = dotX(container)

    // No more new data — but frames within one render-delay window still
    // have a newer sample (t=800) ahead of the cursor to glide toward.
    frame(850)
    const xFrame850 = dotX(container)
    frame(900)
    const xFrame900 = dotX(container)

    const series = [xAt400, xAt600, xAt800, xFrame850, xFrame900]
    // applyTransform here is cx = -x, so raw x increasing => translate x
    // strictly decreasing, every step, with no repeated value.
    for (let i = 1; i < series.length; i += 1) {
      expect(series[i]).toBeLessThan(series[i - 1])
    }
  })

  it("holds still only on a genuine data stall", () => {
    mockNow = 0
    const { container, rerender } = render(dotsEl([pos(0)]))
    mockNow = 1000
    rerender(dotsEl([pos(100)]))

    // Cursor still behind the newest sample (t=1000) — dot is mid-glide.
    frame(1200)
    expect(dotX(container)).toBeGreaterThan(-100)
    expect(dotX(container)).toBeLessThan(0)

    // Cursor passes the newest sample: no data newer than renderTime, so
    // the dot parks at the last known position and stays there.
    frame(1300)
    const parked = dotTransform(container)
    expect(parked).toBe("translate(-100px, 0px)")
    frame(1400)
    expect(dotTransform(container)).toBe(parked)
    frame(1500)
    expect(dotTransform(container)).toBe(parked)
  })

  it("renders at the first sample on startup without a NaN / origin flash", () => {
    mockNow = 0
    const { container } = render(dotsEl([pos(10, 20)]))
    const transform = dotTransform(container)
    expect(transform).toBe("translate(-10px, 20px)")
    expect(transform).not.toContain("NaN")
  })

  it("snaps straight to the latest position under prefers-reduced-motion", () => {
    mockNow = 0
    const { container, rerender } = render(dotsEl([pos(10)], true))
    expect(dotX(container)).toBe(-10)

    mockNow = 50
    rerender(dotsEl([pos(99)], true))
    expect(dotX(container)).toBe(-99)

    // No animation loop running — advancing frames changes nothing.
    frame(500)
    expect(dotX(container)).toBe(-99)
  })
})

// The selected driver's code label (2026-10-09): a pill inside the selected
// driver's group, drawn last so it sits above the other dots, flipped below
// the dot near the top edge of the map.
describe("AnimatedDriverDots selected-driver label", () => {
  const META = new Map<string, DriverDotMeta>([
    ["1", { color: "#3671C6", driverId: "ver", code: "VER" }],
    ["44", { color: "#E8002D", driverId: "ham", code: "HAM" }],
    ["16", { color: "#E8002D", driverId: "lec", code: "LEC" }],
  ])

  function fieldEl(selectedDriverId: string | null, y = 500) {
    const positions: DriverPosition[] = ["1", "44", "16"].map((driverNumber, index) => ({
      driver_number: driverNumber,
      x: -100 * index,
      y,
      z: null,
      timestamp: null,
    }))
    return (
      <svg>
        <AnimatedDriverDots
          positions={positions}
          transform={TRANSFORM}
          driverByCarNumber={META}
          selectedDriverId={selectedDriverId}
          prefersReducedMotion
          renderDelayMs={RENDER_DELAY_MS}
        />
      </svg>
    )
  }

  it("labels only the selected driver, with their code", () => {
    const { container } = render(fieldEl("ham"))
    const labels = container.querySelectorAll("[data-driver-label]")
    expect(labels).toHaveLength(1)
    expect(labels[0].textContent).toBe("HAM")
    expect(labels[0].closest("g[data-driver-number]")?.getAttribute("data-driver-number")).toBe("44")
  })

  it("shows no label when no driver is selected", () => {
    const { container } = render(fieldEl(null))
    expect(container.querySelectorAll("[data-driver-label]")).toHaveLength(0)
  })

  it("draws the selected driver last, above the other dots", () => {
    const { container } = render(fieldEl("ver"))
    const order = Array.from(container.querySelectorAll("g[data-driver-number]")).map((group) =>
      group.getAttribute("data-driver-number"),
    )
    expect(order).toEqual(["44", "16", "1"])
  })

  it("flips the label below the dot only when there is no room above it", () => {
    const { container, rerender } = render(fieldEl("ham", 500))
    const label = () => container.querySelector<SVGGElement>("[data-driver-label]")
    expect(label()?.style.transform).toBe("")

    rerender(fieldEl("ham", 10))
    expect(label()?.style.transform).toMatch(/^translateY\(\d+px\)$/)
  })
})

