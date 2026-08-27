import { useEffect, useLayoutEffect, useRef } from "react"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { CircuitOutlineTransform, DriverPosition } from "@/types"

const DOT_RADIUS = 12
const SELECTED_DOT_RADIUS = 18
const DOT_STROKE_WIDTH = 1.5
const SELECTED_DOT_STROKE_WIDTH = 3
// Matches useDriverPositions' poll interval — the window a dot's
// interpolation travels its last-known-to-newest-known segment over. Kept
// as its own constant (not imported from that hook) since this component
// only needs the number, not the hook itself.
const INTERPOLATION_WINDOW_MS = 1000

interface DotAnimationState {
  fromCx: number
  fromCy: number
  toCx: number
  toCy: number
  updatedAt: number
}

// Mirrors extract_circuit_outlines.py's _build_geometry — applies the same
// X-mirror-correction/rotation/center/scale to a raw live Position.z X/Y
// sample that was applied to the outline's own points, so both land in the
// same viewBox frame. See backend/schemas/circuit_schema.py's
// CircuitOutlineTransform docstring for why the X negation happens first.
function applyTransform(x: number, y: number, transform: CircuitOutlineTransform) {
  const correctedX = -x
  const angle = (transform.rotation_degrees * Math.PI) / 180
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const rotatedX = correctedX * cos - y * sin
  const rotatedY = correctedX * sin + y * cos
  return {
    cx: (rotatedX - transform.center_x) * transform.scale + transform.viewbox_center,
    cy: (rotatedY - transform.center_y) * transform.scale + transform.viewbox_center,
  }
}

function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t
}

interface AnimatedDriverDotsProps {
  positions: DriverPosition[]
  transform: CircuitOutlineTransform
  driverByCarNumber: Map<string, { color: string; driverId: string }>
  selectedDriverId: string | null
  prefersReducedMotion: boolean
}

// Drives dot movement via requestAnimationFrame against performance.now()
// timestamps captured in the browser, instead of a CSS transition with a
// guessed fixed duration. A CSS transition has to assume the interval
// between position updates never varies and pick a duration relative to
// that guess — confirmed during Day 43 manual verification that no
// duration value eliminated a visible stop-start pulse, since the REAL
// interval (poll interval + REST round-trip + render timing) doesn't hold
// perfectly still. Interpolating in JS against real captured timestamps
// adapts to whatever the actual interval turns out to be on every single
// update, so motion never actually pauses.
//
// Writes position directly to each <circle>'s DOM node via a ref, not
// through React state/props — a 60fps loop driving React re-renders would
// be needlessly expensive for up to 22 simultaneously moving dots; radius/
// fill/stroke (the only genuinely React-driven visual state, changing only
// on selection) stay declarative below.
export function AnimatedDriverDots({
  positions,
  transform,
  driverByCarNumber,
  selectedDriverId,
  prefersReducedMotion,
}: AnimatedDriverDotsProps) {
  const dotRefs = useRef<Map<string, SVGCircleElement>>(new Map())
  const animationStateRef = useRef<Map<string, DotAnimationState>>(new Map())

  // New data arrived: shift each driver's animation target forward.
  // useLayoutEffect (not useEffect) so this applies before the browser
  // paints — a newly-appearing dot must never flash at the SVG origin for
  // even one frame. The new segment starts from wherever that dot is
  // CURRENTLY interpolated to be (not from its old raw target) — otherwise
  // a dot would jump backward to its last sample whenever a new update
  // lands before the previous interpolation window had actually finished.
  useLayoutEffect(() => {
    if (!transform) return
    const now = performance.now()
    const seen = new Set<string>()
    for (const position of positions) {
      seen.add(position.driver_number)
      const { cx, cy } = applyTransform(position.x, position.y, transform)
      const existing = animationStateRef.current.get(position.driver_number)
      let fromCx = cx
      let fromCy = cy
      if (existing) {
        const t = Math.min(1, (now - existing.updatedAt) / INTERPOLATION_WINDOW_MS)
        fromCx = lerp(existing.fromCx, existing.toCx, t)
        fromCy = lerp(existing.fromCy, existing.toCy, t)
      }
      animationStateRef.current.set(position.driver_number, {
        fromCx,
        fromCy,
        toCx: cx,
        toCy: cy,
        updatedAt: now,
      })
      if (prefersReducedMotion) {
        const el = dotRefs.current.get(position.driver_number)
        if (el) el.style.transform = `translate(${cx}px, ${cy}px)`
      }
    }
    // Drop state for any driver no longer present, so a reappearing driver
    // starts fresh at their new spot instead of gliding in from a stale
    // last-known one.
    for (const driverNumber of animationStateRef.current.keys()) {
      if (!seen.has(driverNumber)) animationStateRef.current.delete(driverNumber)
    }
  }, [positions, transform, prefersReducedMotion])

  // The animation loop — skipped entirely under prefers-reduced-motion
  // (the effect above already applies each update's final position
  // directly, with no interpolation to animate).
  useEffect(() => {
    if (prefersReducedMotion) return
    let frameId: number
    const tick = () => {
      const now = performance.now()
      for (const [driverNumber, state] of animationStateRef.current) {
        const el = dotRefs.current.get(driverNumber)
        if (!el) continue
        const t = Math.min(1, (now - state.updatedAt) / INTERPOLATION_WINDOW_MS)
        const cx = lerp(state.fromCx, state.toCx, t)
        const cy = lerp(state.fromCy, state.toCy, t)
        el.style.transform = `translate(${cx}px, ${cy}px)`
      }
      frameId = requestAnimationFrame(tick)
    }
    frameId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameId)
  }, [prefersReducedMotion])

  return (
    <>
      {positions.map((position) => {
        const meta = driverByCarNumber.get(position.driver_number)
        const isSelected = meta !== undefined && meta.driverId === selectedDriverId
        return (
          <circle
            key={position.driver_number}
            ref={(el) => {
              if (el) dotRefs.current.set(position.driver_number, el)
              else dotRefs.current.delete(position.driver_number)
            }}
            r={isSelected ? SELECTED_DOT_RADIUS : DOT_RADIUS}
            fill={meta?.color ?? FALLBACK_TEAM_COLOR}
            stroke="#fff"
            strokeWidth={isSelected ? SELECTED_DOT_STROKE_WIDTH : DOT_STROKE_WIDTH}
          />
        )
      })}
    </>
  )
}
