import type { CircuitOutlineResponse } from "@/types"

// Static outline + turn markers only, extracted from CircuitMapPanel so
// DashboardPage's upcoming-race card can reuse the same rendering without
// duplicating the path/corner-marker geometry. Deliberately excludes live
// driver dots, the telemetry gauge, and mode/countdown logic — those stay in
// CircuitMapPanel, which still owns its own <svg> wrapper and viewBox.
const FALLBACK_VIEWBOX = "0 0 1000 1000"
const DEFAULT_VIEWBOX_CENTER = 500
const CORNER_MARKER_RADIUS = 14
const CORNER_MARKER_FONT_SIZE = 15
const CORNER_OFFSET_DISTANCE = 22

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
  className?: string
}

export function CircuitOutlineSvg({ outline, className }: CircuitOutlineSvgProps) {
  const viewBox = outline?.viewbox ?? FALLBACK_VIEWBOX
  const pathD = outline ? pointsToPath(outline.points) : null
  const viewboxCenter = outline?.transform?.viewbox_center ?? DEFAULT_VIEWBOX_CENTER

  return (
    <svg viewBox={viewBox} preserveAspectRatio="xMidYMid meet" className={className} aria-hidden="true">
      {pathD && (
        <path
          d={pathD}
          fill="none"
          stroke="currentColor"
          strokeWidth={10}
          strokeLinejoin="round"
          strokeLinecap="round"
          className="text-muted-foreground/40"
        />
      )}
      {(outline?.corners ?? []).map((corner) => {
        const { x, y } = offsetFromCenter(corner.x, corner.y, viewboxCenter, CORNER_OFFSET_DISTANCE)
        return (
          <g key={corner.number}>
            <circle
              cx={x}
              cy={y}
              r={CORNER_MARKER_RADIUS}
              className="fill-background"
              stroke="#ffffff"
              strokeOpacity={0.6}
              strokeWidth={1.5}
            />
            <text
              x={x}
              y={y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={CORNER_MARKER_FONT_SIZE}
              fontWeight={700}
              className="select-none fill-muted-foreground"
            >
              {corner.number}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
