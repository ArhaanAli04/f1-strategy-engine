import Svg, { Circle, G, Path, Text as SvgText } from "react-native-svg"
import type { CircuitOutlineResponse } from "@/types"

// RN port of web/src/components/circuit/CircuitOutlineSvg.tsx — static
// outline + turn markers only (no live driver dots/telemetry gauge, those
// stay in CircuitMapPanel, ported in Checkpoint 6). svg/path/circle/text ->
// react-native-svg's Svg/Path/Circle/Text primitives; web's actual markup
// uses <path> (built from `points` via pointsToPath), not <polyline> —
// ported as-is rather than following the primitive-mapping note literally.
const FALLBACK_VIEWBOX = "0 0 1000 1000"
const DEFAULT_VIEWBOX_CENTER = 500
const CORNER_MARKER_RADIUS = 14
const CORNER_MARKER_FONT_SIZE = 15
const CORNER_OFFSET_DISTANCE = 22
const TRACK_LINE_COLOR = "rgba(161, 161, 170, 0.4)" // mirrors web's text-muted-foreground/40
const CORNER_LABEL_COLOR = "#a1a1aa" // mirrors web's fill-muted-foreground

function pointsToPath(points: number[][]): string | null {
  if (points.length === 0) return null
  const [first, ...rest] = points
  const move = `M ${first[0]} ${first[1]}`
  const lines = rest.map(([x, y]) => `L ${x} ${y}`).join(" ")
  return `${move} ${lines} Z`
}

function offsetFromCenter(x: number, y: number, center: number, distance: number) {
  const dx = x - center
  const dy = y - center
  const length = Math.hypot(dx, dy) || 1
  return { x: x + (dx / length) * distance, y: y + (dy / length) * distance }
}

interface CircuitOutlineSvgProps {
  outline: CircuitOutlineResponse | undefined
  width?: number | string
  height?: number | string
  backgroundColor?: string
}

export function CircuitOutlineSvg({
  outline,
  width = "100%",
  height = "100%",
  backgroundColor = "#0a0a0a",
}: CircuitOutlineSvgProps) {
  const viewBox = outline?.viewbox ?? FALLBACK_VIEWBOX
  const pathD = outline ? pointsToPath(outline.points) : null
  const viewboxCenter = outline?.transform?.viewbox_center ?? DEFAULT_VIEWBOX_CENTER

  return (
    <Svg width={width} height={height} viewBox={viewBox}>
      {pathD && (
        <Path
          d={pathD}
          fill="none"
          stroke={TRACK_LINE_COLOR}
          strokeWidth={10}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {(outline?.corners ?? []).map((corner) => {
        const { x, y } = offsetFromCenter(corner.x, corner.y, viewboxCenter, CORNER_OFFSET_DISTANCE)
        return (
          <G key={corner.number}>
            <Circle
              cx={x}
              cy={y}
              r={CORNER_MARKER_RADIUS}
              fill={backgroundColor}
              stroke="#ffffff"
              strokeOpacity={0.6}
              strokeWidth={1.5}
            />
            <SvgText
              x={x}
              y={y}
              textAnchor="middle"
              // react-native-svg's Text has no dominant-baseline "central" —
              // dy nudges the glyph to sit visually centered on cy instead.
              dy={CORNER_MARKER_FONT_SIZE * 0.35}
              fontSize={CORNER_MARKER_FONT_SIZE}
              fontWeight={700}
              fill={CORNER_LABEL_COLOR}
            >
              {corner.number}
            </SvgText>
          </G>
        )
      })}
    </Svg>
  )
}
