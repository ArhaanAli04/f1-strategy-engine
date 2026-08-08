// Circular telemetry HUD, classic 270deg automotive-gauge shape: a 90deg
// gap centered at the bottom (6 o'clock), arc running from bottom-left
// (~7 o'clock) clockwise through 9/12 o'clock to bottom-right (~5
// o'clock). Outer ring = speed, left inner arc = throttle (larger portion,
// ~7 o'clock to the "240" speed-label position), right inner arc = brake
// (smaller portion, "240" to ~5 o'clock), center = speed/gear readout,
// GEAR + DRS pill sit in the open gap. Deliberately hardcoded dark colors
// (not theme-aware) —
// same precedent as LiveTimingTower's TyreIcon (TYRE_ICON_BG): a small
// self-contained instrument graphic styled to look like a real telemetry
// display regardless of the app's light/dark theme, not page-level text.

import { useId } from "react"

const SIZE = 200
const CENTER = SIZE / 2
const OUTER_RADIUS = 74
const OUTER_STROKE = 20
const INNER_RADIUS = 52
const INNER_STROKE = 20
const LABEL_RADIUS = OUTER_RADIUS
const MAX_SPEED = 360
const SPEED_LABELS = [0, 60, 120, 180, 240, 300, 360]
// The 0 and 360 labels sit exactly at ARC_START/ARC_END — the arc's flat
// (butt-capped) tips — so a label centered exactly there straddles the
// edge, half over the ring and half over empty space beyond it. Clamping
// pulls just those two labels a few degrees inward, fully onto the band.
const LABEL_EDGE_PADDING = 5

const TRACK_COLOR = "#1a1a2e"
const SPEED_COLOR = "#0080ff"
const THROTTLE_COLOR = "#00cc44"
const BRAKE_COLOR = "#ff3333"
const LABEL_COLOR = "#8a8a9e"
const TEXT_COLOR = "#ffffff"
const DRS_OPEN_BG = "#00cc44"
const DRS_CLOSED_BG = "#2a2a3e"
const DRS_OPEN_TEXT = "#ffffff"
const DRS_CLOSED_TEXT = "#6a6a7e"

// Angle convention: degrees clockwise from 12 o'clock (0deg = top, 90deg =
// right/3 o'clock, 180deg = bottom/6 o'clock, 270deg = left/9 o'clock).
// The gauge's usable arc spans 270deg, symmetric around the bottom (180deg)
// with a 90deg gap: ARC_START (225deg, ~7 o'clock, bottom-left) sweeps
// clockwise through west(270)/top(360=0) to ARC_END (495deg, wraps to
// 135deg, ~5 o'clock, bottom-right). The throttle/brake split sits where
// the "240" speed label lands (180deg into the 270deg sweep from
// ARC_START, since 240/360 * 270 = 180) — throttle gets the larger 180deg
// portion, brake the smaller 90deg portion.
const ARC_START = 225
const ARC_SWEEP = 270
const ARC_END = ARC_START + ARC_SWEEP
const THROTTLE_SWEEP = 180
const SPLIT_ANGLE = ARC_START + THROTTLE_SWEEP
const BRAKE_SWEEP = ARC_SWEEP - THROTTLE_SWEEP

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number) {
  const angleRad = (angleDeg * Math.PI) / 180
  return { x: cx + radius * Math.sin(angleRad), y: cy - radius * Math.cos(angleRad) }
}

function describeArc(
  cx: number,
  cy: number,
  radius: number,
  startAngle: number,
  endAngle: number,
): string | null {
  if (endAngle <= startAngle) return null
  const start = polarToCartesian(cx, cy, radius, startAngle)
  const end = polarToCartesian(cx, cy, radius, endAngle)
  const largeArcFlag = endAngle - startAngle > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`
}

export interface TelemetryGaugeProps {
  speedKmh: number | null
  gear: number | null
  throttlePct: number | null
  brake: boolean | null
  drsOpen: boolean
}

export function TelemetryGauge({ speedKmh, gear, throttlePct, brake, drsOpen }: TelemetryGaugeProps) {
  // Instance-unique path ids — a fixed string id would collide if this
  // component were ever mounted more than once on the same page, breaking
  // the textPath href reference for one of the instances.
  const instanceId = useId()
  const throttlePathId = `telemetry-gauge-throttle-arc-${instanceId}`
  const brakePathId = `telemetry-gauge-brake-arc-${instanceId}`

  const speed = Math.max(0, Math.min(speedKmh ?? 0, MAX_SPEED))
  const throttle = Math.max(0, Math.min(throttlePct ?? 0, 100))
  const brakeValue = brake ? 100 : 0

  const speedTrack = describeArc(CENTER, CENTER, OUTER_RADIUS, ARC_START, ARC_END)
  const speedArc = describeArc(CENTER, CENTER, OUTER_RADIUS, ARC_START, ARC_START + (speed / MAX_SPEED) * ARC_SWEEP)

  // Left/larger arc (bottom-left -> ~2 o'clock): fill grows from the start
  // (0%) toward the split point (100%) as throttle rises.
  const throttleTrack = describeArc(CENTER, CENTER, INNER_RADIUS, ARC_START, SPLIT_ANGLE)
  const throttleArc = describeArc(
    CENTER,
    CENTER,
    INNER_RADIUS,
    ARC_START,
    ARC_START + (throttle / 100) * THROTTLE_SWEEP,
  )

  // Right/smaller arc (~2 o'clock -> bottom-right): fill grows from the
  // bottom-right end (0%) back toward the split point (100%) — same
  // "anchored at the far end, grows toward the split" shape as throttle.
  const brakeTrack = describeArc(CENTER, CENTER, INNER_RADIUS, SPLIT_ANGLE, ARC_END)
  const brakeArc = describeArc(
    CENTER,
    CENTER,
    INNER_RADIUS,
    ARC_END - (brakeValue / 100) * BRAKE_SWEEP,
    ARC_END,
  )

  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      className="select-none"
      aria-hidden="true"
    >
      <path
        d={speedTrack ?? undefined}
        fill="none"
        stroke={TRACK_COLOR}
        strokeWidth={OUTER_STROKE}
        strokeLinecap="butt"
      />
      {speedArc && (
        <path
          d={speedArc}
          fill="none"
          stroke={SPEED_COLOR}
          strokeWidth={OUTER_STROKE}
          strokeLinecap="butt"
        />
      )}
      {SPEED_LABELS.map((label) => {
        const rawAngle = ARC_START + (label / MAX_SPEED) * ARC_SWEEP
        const angle = Math.min(
          Math.max(rawAngle, ARC_START + LABEL_EDGE_PADDING),
          ARC_END - LABEL_EDGE_PADDING,
        )
        const pos = polarToCartesian(CENTER, CENTER, LABEL_RADIUS, angle)
        return (
          <text
            key={label}
            x={pos.x}
            y={pos.y}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={8}
            fill={TEXT_COLOR}
          >
            {label}
          </text>
        )
      })}

      <path
        id={throttlePathId}
        d={throttleTrack ?? undefined}
        fill="none"
        stroke={TRACK_COLOR}
        strokeWidth={INNER_STROKE}
        strokeLinecap="butt"
      />
      {throttleArc && (
        <path
          d={throttleArc}
          fill="none"
          stroke={THROTTLE_COLOR}
          strokeWidth={INNER_STROKE}
          strokeLinecap="butt"
        />
      )}
      <text fontSize={8} fontWeight={700} fill={TEXT_COLOR}>
        <textPath href={`#${throttlePathId}`} startOffset="50%" textAnchor="middle" dy={3}>
          THROTTLE
        </textPath>
      </text>

      <path
        id={brakePathId}
        d={brakeTrack ?? undefined}
        fill="none"
        stroke={TRACK_COLOR}
        strokeWidth={INNER_STROKE}
        strokeLinecap="butt"
      />
      {brakeArc && (
        <path
          d={brakeArc}
          fill="none"
          stroke={BRAKE_COLOR}
          strokeWidth={INNER_STROKE}
          strokeLinecap="butt"
        />
      )}
      <text fontSize={8} fontWeight={700} fill={TEXT_COLOR}>
        <textPath href={`#${brakePathId}`} startOffset="50%" textAnchor="middle" dy={3}>
          BRAKE
        </textPath>
      </text>

      <text
        x={CENTER}
        y={CENTER + 2}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={28}
        fontWeight={700}
        fill="#ffffff"
      >
        {speedKmh == null ? "—" : Math.round(speedKmh)}
      </text>
      <text
        x={CENTER}
        y={CENTER + 22}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={8}
        fill={TEXT_COLOR}
        letterSpacing={1}
      >
        KMH
      </text>
      <rect
        x={CENTER - 18}
        y={CENTER + 34}
        width={36}
        height={16}
        rx={0}
        fill={drsOpen ? DRS_OPEN_BG : DRS_CLOSED_BG}
      />
      <text
        x={CENTER}
        y={CENTER + 42}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={9}
        fontWeight={700}
        fill={drsOpen ? DRS_OPEN_TEXT : DRS_CLOSED_TEXT}
      >
        DRS
      </text>
      <text x={CENTER} y={CENTER + 62} textAnchor="middle" dominantBaseline="central">
        <tspan fontSize={8} fill={LABEL_COLOR} letterSpacing={1}>
          GEAR{" "}
        </tspan>
        <tspan fontSize={16} fontWeight={700} fill="#ffffff">
          {gear == null ? "—" : gear}
        </tspan>
      </text>
    </svg>
  )
}
