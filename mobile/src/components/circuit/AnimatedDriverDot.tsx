import { useEffect } from "react"
import Animated, {
  useAnimatedProps,
  useFrameCallback,
  useReducedMotion,
  useSharedValue,
} from "react-native-reanimated"
import { Circle } from "react-native-svg"
import { POSITIONS_POLL_INTERVAL_MS } from "@/hooks/useDriverPositions"
import type { CircuitOutlineTransform } from "@/types"

const AnimatedCircle = Animated.createAnimatedComponent(Circle)

const DOT_RADIUS = 12
const SELECTED_DOT_RADIUS = 18
const DOT_STROKE_WIDTH = 1.5
const SELECTED_DOT_STROKE_WIDTH = 3

// See web/src/components/circuit/AnimatedDriverDots.tsx for the full
// rationale. The render cursor sits this far in the past so a newer buffered
// sample is (almost) always available to glide toward; the margin covers the
// poll interval plus REST round-trip and render jitter. Bump to 250 if a
// residual micro-stall shows up on device.
const RENDER_DELAY_JITTER_MS = 150
const DEFAULT_RENDER_DELAY_MS = POSITIONS_POLL_INTERVAL_MS + RENDER_DELAY_JITTER_MS

// Enough recent samples to always contain the pair straddling the render
// cursor; 4 leaves slack for one late poll.
const MAX_SAMPLES = 4

interface PositionSample {
  t: number // Date.now() when this sample was received
  x: number // raw Position.z-frame coordinate, pre-applyTransform
  y: number
}

// Mirrors extract_circuit_outlines.py's _build_geometry — the same
// X-mirror/rotation/center/scale the outline's own points went through.
// Marked "worklet" so the frame callback below can run it on the UI thread
// (it is also called from the JS thread during render / reduced-motion).
function applyTransform(x: number, y: number, transform: CircuitOutlineTransform) {
  "worklet"
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
  "worklet"
  return from + (to - from) * t
}

// Raw (pre-transform) position at renderTime: linear interpolation between
// the two buffered samples straddling it. Holds at the oldest sample before
// the buffer has renderDelayMs of history (startup), and at the newest once
// renderTime passes it (a genuine data stall). In steady state neither hold
// branch is hit, so motion never pauses between polls.
function rawPositionAt(
  buffer: PositionSample[],
  renderTime: number,
): { x: number; y: number } | null {
  "worklet"
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

interface AnimatedDriverDotProps {
  x: number
  y: number
  transform: CircuitOutlineTransform
  color: string
  isSelected: boolean
  renderDelayMs?: number
}

// RN port of web's AnimatedDriverDots, one instance per driver (hooks can't
// run in the parent's .map()). Same render-behind interpolation buffer:
// every poll appends a raw (x, y, Date.now()) sample; a Reanimated
// useFrameCallback draws the dot at (Date.now() - renderDelayMs),
// interpolating between the two buffered samples straddling that cursor, so
// the dot always has somewhere to move toward and never freezes between the
// 2s polls — the visible stop-start pulse withTiming(1.8s)-per-update
// produced is gone. Samples are buffered RAW and transformed per frame so a
// circuit-outline change can't strand stale screen coordinates.
export function AnimatedDriverDot({
  x,
  y,
  transform,
  color,
  isSelected,
  renderDelayMs = DEFAULT_RENDER_DELAY_MS,
}: AnimatedDriverDotProps) {
  const initial = applyTransform(x, y, transform)
  const animatedCx = useSharedValue(initial.cx)
  const animatedCy = useSharedValue(initial.cy)
  const buffer = useSharedValue<PositionSample[]>([])
  const prefersReducedMotion = useReducedMotion()

  // New data: append a raw sample (reassign, don't mutate, so the shared
  // value propagates to the UI thread). Under reduced motion, skip the
  // buffer entirely and snap straight to the latest position.
  useEffect(() => {
    if (prefersReducedMotion) {
      const point = applyTransform(x, y, transform)
      animatedCx.value = point.cx
      animatedCy.value = point.cy
      return
    }
    const next = [...buffer.value, { t: Date.now(), x, y }]
    if (next.length > MAX_SAMPLES) next.splice(0, next.length - MAX_SAMPLES)
    buffer.value = next
  }, [x, y, transform, prefersReducedMotion, animatedCx, animatedCy, buffer])

  const frameCallback = useFrameCallback(() => {
    "worklet"
    const raw = rawPositionAt(buffer.value, Date.now() - renderDelayMs)
    if (raw === null) return
    const point = applyTransform(raw.x, raw.y, transform)
    animatedCx.value = point.cx
    animatedCy.value = point.cy
  })

  // useFrameCallback's autostart arg is only read once (its internal
  // isActive is seeded on mount and never updated) — toggle explicitly so a
  // reduced-motion change actually stops/starts the loop.
  useEffect(() => {
    frameCallback.setActive(!prefersReducedMotion)
  }, [frameCallback, prefersReducedMotion])

  const animatedProps = useAnimatedProps(() => ({
    cx: animatedCx.value,
    cy: animatedCy.value,
  }))

  return (
    <AnimatedCircle
      animatedProps={animatedProps}
      r={isSelected ? SELECTED_DOT_RADIUS : DOT_RADIUS}
      fill={color}
      stroke="#ffffff"
      strokeWidth={isSelected ? SELECTED_DOT_STROKE_WIDTH : DOT_STROKE_WIDTH}
    />
  )
}
