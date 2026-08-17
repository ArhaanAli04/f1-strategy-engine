// RN port of web/src/components/circuit/TelemetryGauge.tsx — circular
// telemetry HUD, classic 270deg automotive-gauge shape. Same geometry/arc
// math as web; svg/path/text/rect/textPath -> react-native-svg's
// Svg/Path/Text/Rect/TextPath. No CSS transition on arc `d` changes here —
// react-native-svg can't interpolate path data directly the way a browser
// does; arcs snap to their new value on each 8s poll instead of sweeping
// (unlike AnimatedDriverDot's cx/cy, arc `d` strings aren't a single
// animatable numeric value Reanimated can tween between).
import Svg, { Path, Rect, Text as SvgText, TextPath, TSpan } from "react-native-svg"

const SIZE = 200
const CENTER = SIZE / 2
const OUTER_RADIUS = 74
const OUTER_STROKE = 20
const INNER_RADIUS = 52
const INNER_STROKE = 20
const LABEL_RADIUS = OUTER_RADIUS
const MAX_SPEED = 360
const SPEED_LABELS = [0, 60, 120, 180, 240, 300, 360]
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
  const throttlePathId = "telemetry-gauge-throttle-arc"
  const brakePathId = "telemetry-gauge-brake-arc"

  const speed = Math.max(0, Math.min(speedKmh ?? 0, MAX_SPEED))
  const throttle = Math.max(0, Math.min(throttlePct ?? 0, 100))
  const brakeValue = brake ? 100 : 0

  const speedTrack = describeArc(CENTER, CENTER, OUTER_RADIUS, ARC_START, ARC_END)
  const speedArc = describeArc(CENTER, CENTER, OUTER_RADIUS, ARC_START, ARC_START + (speed / MAX_SPEED) * ARC_SWEEP)

  const throttleTrack = describeArc(CENTER, CENTER, INNER_RADIUS, ARC_START, SPLIT_ANGLE)
  const throttleArc = describeArc(
    CENTER,
    CENTER,
    INNER_RADIUS,
    ARC_START,
    ARC_START + (throttle / 100) * THROTTLE_SWEEP,
  )

  const brakeTrack = describeArc(CENTER, CENTER, INNER_RADIUS, SPLIT_ANGLE, ARC_END)
  const brakeArc = describeArc(
    CENTER,
    CENTER,
    INNER_RADIUS,
    ARC_END - (brakeValue / 100) * BRAKE_SWEEP,
    ARC_END,
  )

  return (
    <Svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
      <Path d={speedTrack ?? undefined} fill="none" stroke={TRACK_COLOR} strokeWidth={OUTER_STROKE} strokeLinecap="butt" />
      {speedArc && (
        <Path d={speedArc} fill="none" stroke={SPEED_COLOR} strokeWidth={OUTER_STROKE} strokeLinecap="butt" />
      )}
      {SPEED_LABELS.map((label) => {
        const rawAngle = ARC_START + (label / MAX_SPEED) * ARC_SWEEP
        const angle = Math.min(
          Math.max(rawAngle, ARC_START + LABEL_EDGE_PADDING),
          ARC_END - LABEL_EDGE_PADDING,
        )
        const pos = polarToCartesian(CENTER, CENTER, LABEL_RADIUS, angle)
        return (
          <SvgText
            key={label}
            x={pos.x}
            y={pos.y}
            dy={2.5}
            textAnchor="middle"
            fontSize={8}
            fill={TEXT_COLOR}
          >
            {label}
          </SvgText>
        )
      })}

      <Path
        id={throttlePathId}
        d={throttleTrack ?? undefined}
        fill="none"
        stroke={TRACK_COLOR}
        strokeWidth={INNER_STROKE}
        strokeLinecap="butt"
      />
      {throttleArc && (
        <Path d={throttleArc} fill="none" stroke={THROTTLE_COLOR} strokeWidth={INNER_STROKE} strokeLinecap="butt" />
      )}
      <SvgText fontSize={8} fontWeight={700} fill={TEXT_COLOR}>
        <TextPath href={`#${throttlePathId}`} startOffset="50%" textAnchor="middle" alignmentBaseline="middle">
          THROTTLE
        </TextPath>
      </SvgText>

      <Path
        id={brakePathId}
        d={brakeTrack ?? undefined}
        fill="none"
        stroke={TRACK_COLOR}
        strokeWidth={INNER_STROKE}
        strokeLinecap="butt"
      />
      {brakeArc && (
        <Path d={brakeArc} fill="none" stroke={BRAKE_COLOR} strokeWidth={INNER_STROKE} strokeLinecap="butt" />
      )}
      <SvgText fontSize={8} fontWeight={700} fill={TEXT_COLOR}>
        <TextPath href={`#${brakePathId}`} startOffset="50%" textAnchor="middle" alignmentBaseline="middle">
          BRAKE
        </TextPath>
      </SvgText>

      <SvgText x={CENTER} y={CENTER + 2} dy={9} textAnchor="middle" fontSize={28} fontWeight={700} fill="#ffffff">
        {speedKmh == null ? "—" : Math.round(speedKmh)}
      </SvgText>
      <SvgText
        x={CENTER}
        y={CENTER + 22}
        dy={2.5}
        textAnchor="middle"
        fontSize={8}
        fill={TEXT_COLOR}
        letterSpacing={1}
      >
        KMH
      </SvgText>
      <Rect x={CENTER - 18} y={CENTER + 34} width={36} height={16} fill={drsOpen ? DRS_OPEN_BG : DRS_CLOSED_BG} />
      <SvgText
        x={CENTER}
        y={CENTER + 42}
        dy={3}
        textAnchor="middle"
        fontSize={9}
        fontWeight={700}
        fill={drsOpen ? DRS_OPEN_TEXT : DRS_CLOSED_TEXT}
      >
        DRS
      </SvgText>
      <SvgText x={CENTER} y={CENTER + 62} dy={3} textAnchor="middle">
        <TSpan fontSize={8} fill={LABEL_COLOR} letterSpacing={1}>
          GEAR{" "}
        </TSpan>
        <TSpan fontSize={16} fontWeight={700} fill="#ffffff">
          {gear == null ? "—" : gear}
        </TSpan>
      </SvgText>
    </Svg>
  )
}
