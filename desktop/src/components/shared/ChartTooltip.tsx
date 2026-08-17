interface ChartTooltipPayloadEntry {
  value?: unknown
  name?: string | number
  color?: string
  payload?: Record<string, unknown>
}

interface SegmentedLineTooltipProps {
  active?: boolean
  payload?: readonly ChartTooltipPayloadEntry[]
  label?: string | number
  xKey: string
  labelText: (label: string | number) => string
  valueText: (value: unknown) => string
}

// Recharts' default tooltip shows the *nearest* data point from every
// <Line> on a chart, even ones with no real point at the hovered x-value —
// an unavoidable side effect of giving each compound stint its own <Line>
// with its own `data` array (needed since Recharts has no native "recolor
// this line where the series value changes" primitive; see
// LapTimeChart.tsx/LapTimesChart.tsx's buildCompoundSegments/Series). That
// surfaced as a lap appearing to show more than one tyre compound when
// hovered. Filtering payload entries to only the one whose underlying data
// point actually has this x-value removes those phantom "nearest point
// from a different stint" entries.
export function SegmentedLineTooltip({
  active,
  payload,
  label,
  xKey,
  labelText,
  valueText,
}: SegmentedLineTooltipProps) {
  if (!active || !payload || payload.length === 0 || label === undefined) return null

  const realEntries = payload.filter((entry) => entry.payload?.[xKey] === label)
  if (realEntries.length === 0) return null

  return (
    <div className="rounded-[10px] border border-border bg-card px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 text-muted-foreground">{labelText(label)}</div>
      <div className="space-y-1">
        {realEntries.map((entry, index) => (
          <div key={index} className="flex items-center gap-1.5 font-mono tabular-nums text-foreground">
            <span className="h-2 w-2 flex-shrink-0 rounded-full" style={{ backgroundColor: entry.color }} />
            {entry.name ? `${entry.name}: ` : ""}
            {valueText(entry.value)}
          </div>
        ))}
      </div>
    </div>
  )
}
