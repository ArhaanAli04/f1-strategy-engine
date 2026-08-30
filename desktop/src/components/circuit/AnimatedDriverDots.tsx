import { useCallback, useEffect, useLayoutEffect, useRef } from "react"
import { POSITIONS_POLL_INTERVAL_MS } from "@/hooks/useDriverPositions"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"
import type { CircuitOutlineTransform, DriverPosition } from "@/types"

// Verbatim copy of web/src/components/circuit/AnimatedDriverDots.tsx — see
// desktop/src/README.md's sync table. The render-behind delay is derived
// from this app's own POSITIONS_POLL_INTERVAL_MS import, so desktop's slower
// 2s poll widens the interpolation window automatically.

const DOT_RADIUS = 12
const SELECTED_DOT_RADIUS = 18
const DOT_STROKE_WIDTH = 1.5
const SELECTED_DOT_STROKE_WIDTH = 3

// Extra margin on top of the poll interval. The render cursor sits this far
// in the past, so it needs the real end-to-end update interval (poll +
// REST round-trip + render jitter), which is reliably a little OVER
// POSITIONS_POLL_INTERVAL_MS, to stay behind the newest buffered sample —
// otherwise the cursor catches up to the newest sample before the next one
// lands and the dot stalls at the tail every cycle (the exact pulse this
// component exists to remove). Bump to 250 if any residual micro-stall
// shows up in manual verification.
const RENDER_DELAY_JITTER_MS = 150
const DEFAULT_RENDER_DELAY_MS = POSITIONS_POLL_INTERVAL_MS + RENDER_DELAY_JITTER_MS

// Enough recent samples to always contain the pair straddling the render
// cursor (cursor = now - renderDelayMs) — the last 2 at the position poll
// cadence plus render delay; 4 leaves slack for one late poll.
const MAX_SAMPLES = 4

interface PositionSample {
  t: number // performance.now() when this sample was received
  x: number // raw Position.z-frame coordinate, pre-applyTransform
  y: number
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

// The raw (pre-transform) position to draw at renderTime: linear
// interpolation between the two buffered samples that straddle it. Before
// the buffer holds renderDelayMs of history (startup) it holds at the
// oldest sample; once renderTime passes the newest sample — a genuine data
// stall, no fresh positions arriving — it holds at the newest. In steady
// state the newest sample is always ~now and renderTime is always
// ~now - renderDelayMs, so neither hold branch is hit and motion never
// pauses between polls.
function rawPositionAt(
  buffer: PositionSample[],
  renderTime: number,
): { x: number; y: number } | null {
  if (buffer.length === 0) return null
  const first = buffer[0]
  if (renderTime <= first.t) return { x: first.x, y: first.y }
  const last = buffer[buffer.length - 1]
  if (renderTime >= last.t) return { x: last.x, y: last.y }
  for (let i = 0; i < buffer.length - 1; i += 1) {
    const a = buffer[i]
    const b = buffer[i + 1]
    if (a.t <= renderTime && renderTime <= b.t) {
      const span = b.t - a.t
      const f = span <= 0 ? 0 : (renderTime - a.t) / span
      return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f) }
    }
  }
  return { x: last.x, y: last.y }
}

interface AnimatedDriverDotsProps {
  positions: DriverPosition[]
  transform: CircuitOutlineTransform
  driverByCarNumber: Map<string, { color: string; driverId: string }>
  selectedDriverId: string | null
  prefersReducedMotion: boolean
  // Draw each dot where the field was this many ms ago, so there is always a
  // newer buffered sample to interpolate toward. Defaults to the position
  // poll interval plus a jitter margin; overridable for tests.
  renderDelayMs?: number
}

// Dot movement is driven by a render-behind interpolation buffer — not a CSS
// transition, and not a fixed-window lerp that catches up then waits. Each
// poll appends a raw (x, y, receive-time) sample per driver; a
// requestAnimationFrame loop draws every dot at (now - renderDelayMs),
// linearly interpolating between the two buffered samples that straddle that
// cursor. Because the cursor sits a poll interval (plus margin) in the past,
// a newer sample has almost always already arrived by the time the cursor
// reaches any given one, so the dot always has somewhere to move toward and
// never freezes between updates — the visible stop-start pulse the old
// CSS-transition / fixed-window approach produced (it finished early every
// cycle, then clamped at the target until the next poll) is gone. Motion
// only holds still on a genuine data stall (no fresh positions for >
// renderDelayMs), which is the correct thing to show then.
//
// Positions are buffered RAW (pre-applyTransform) and transformed per frame,
// so a circuit-outline change can't strand stale screen coordinates in the
// buffer. Position is written straight to each <circle>'s style.transform
// via a ref — routing a 60fps loop through React state for up to 22 dots
// would be wasteful; radius/fill/stroke (selection state, changing rarely)
// stay declarative below.
export function AnimatedDriverDots({
  positions,
  transform,
  driverByCarNumber,
  selectedDriverId,
  prefersReducedMotion,
  renderDelayMs = DEFAULT_RENDER_DELAY_MS,
}: AnimatedDriverDotsProps) {
  const dotRefs = useRef<Map<string, SVGCircleElement>>(new Map())
  const buffersRef = useRef<Map<string, PositionSample[]>>(new Map())
  const transformRef = useRef<CircuitOutlineTransform>(transform)

  // One position pass: draw every registered dot at the render-behind
  // cursor. Called synchronously from the layout effect (correct position
  // before the browser paints, including for a just-appeared dot) and every
  // frame from the rAF loop below.
  const renderFrame = useCallback(() => {
    const activeTransform = transformRef.current
    if (!activeTransform) return
    const renderTime = performance.now() - renderDelayMs
    for (const [driverNumber, buffer] of buffersRef.current) {
      const el = dotRefs.current.get(driverNumber)
      if (!el) continue
      const raw = rawPositionAt(buffer, renderTime)
      if (!raw) continue
      const { cx, cy } = applyTransform(raw.x, raw.y, activeTransform)
      el.style.transform = `translate(${cx}px, ${cy}px)`
    }
  }, [renderDelayMs])

  // New data arrived: append one raw sample per driver, drop the oldest
  // beyond MAX_SAMPLES, and forget any driver no longer in the field.
  // useLayoutEffect so the synchronous renderFrame() below repositions
  // everything before paint — a new dot never flashes at the SVG origin.
  useLayoutEffect(() => {
    transformRef.current = transform
    const now = performance.now()
    const seen = new Set<string>()

    for (const position of positions) {
      seen.add(position.driver_number)

      if (prefersReducedMotion) {
        // No interpolation under reduced motion — snap straight to the
        // latest sample and skip the buffer/loop entirely.
        const el = dotRefs.current.get(position.driver_number)
        if (el) {
          const { cx, cy } = applyTransform(position.x, position.y, transform)
          el.style.transform = `translate(${cx}px, ${cy}px)`
        }
        continue
      }

      const buffer = buffersRef.current.get(position.driver_number) ?? []
      buffer.push({ t: now, x: position.x, y: position.y })
      if (buffer.length > MAX_SAMPLES) buffer.splice(0, buffer.length - MAX_SAMPLES)
      buffersRef.current.set(position.driver_number, buffer)
    }

    for (const driverNumber of buffersRef.current.keys()) {
      if (!seen.has(driverNumber)) buffersRef.current.delete(driverNumber)
    }

    if (!prefersReducedMotion) renderFrame()
  }, [positions, transform, prefersReducedMotion, renderFrame])

  // Continuous playback between polls — skipped under reduced motion (the
  // layout effect above already applied each update's final position, with
  // no interpolation to animate).
  useEffect(() => {
    if (prefersReducedMotion) return
    let frameId = 0
    const tick = () => {
      renderFrame()
      frameId = requestAnimationFrame(tick)
    }
    frameId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameId)
  }, [prefersReducedMotion, renderFrame])

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
