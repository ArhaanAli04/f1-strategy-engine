import { useState } from "react"
import { Pressable, Text, View } from "react-native"
import Svg, { Line, Polygon, Text as SvgText } from "react-native-svg"
import { useDriverAnalysis } from "@/hooks/useDriverAnalysis"
import type { DriverAnalysisResponse } from "@/types"

interface StyleRadarProps {
  driverId: string | null
  sessionId: string | null
  driverCode?: string | null
}

// RN port of web/src/components/driver/StyleRadar.tsx. Same 4 axes/metrics,
// same normalization/indicator/archetype logic — copied verbatim from web
// where it's pure data transformation. The chart itself is a hand-rolled
// react-native-svg radar (Polygon/Line/Text), not victory-native: victory-native
// 41.x (confirmed against its installed source, Checkpoint 3) has no radar/
// spider chart — its PolarChart only supports a Pie.Chart child. This follows
// the same manual polar-math convention as TelemetryGauge.tsx/
// CircuitOutlineSvg.tsx rather than pulling in a second charting approach.
// "About this chart" is a Pressable-toggled expand section here, not web's
// Dialog modal (no modal-in-modal-in-a-Card pattern established on mobile yet).
type RadarMetricKey =
  | "sector_time_variance"
  | "tyre_management_index"
  | "lap_time_consistency"
  | "stint_length_tendency"

interface ChartDataPoint {
  metric: string
  value: number
  rawValue: number
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

function formatSigned(value: number): string {
  const fixed = value.toFixed(2)
  return value >= 0 ? `+${fixed}` : fixed
}

type IndicatorColor = "green" | "yellow" | "red" | "grey"

interface Indicator {
  color: IndicatorColor
  label: string
}

const INDICATOR_DOT_COLOR: Record<IndicatorColor, string> = {
  green: "#34d399",
  yellow: "#fde047",
  red: "#f87171",
  grey: "#9ca3af",
}

const Z_SCORE_INDICATOR_THRESHOLD = 0.5

// Same threshold rule for all 3 z-score metrics — negative/low = green
// (better than peers), positive/high = red (worse than peers). Mirrors web
// exactly, including the tyre_management_index direction rationale there.
function zScoreIndicator(value: number, goodLabel: string, badLabel: string): Indicator {
  if (value < -Z_SCORE_INDICATOR_THRESHOLD) return { color: "green", label: goodLabel }
  if (value > Z_SCORE_INDICATOR_THRESHOLD) return { color: "red", label: badLabel }
  return { color: "yellow", label: "Near average" }
}

function stintLengthIndicator(value: number): Indicator {
  if (value < 12) return { color: "grey", label: "Short stints" }
  if (value > 20) return { color: "grey", label: "Long stints" }
  return { color: "grey", label: "Medium stints" }
}

interface RangeZone {
  color: string
  from: number
  to: number
}

interface RangeBarConfig {
  min: number
  max: number
  zones: RangeZone[]
  labels: { value: number; text: string }[]
}

const Z_SCORE_RANGE: RangeBarConfig = {
  min: -2,
  max: 2,
  zones: [
    { color: "rgba(52,211,153,0.5)", from: -2, to: -0.5 },
    { color: "rgba(253,224,71,0.5)", from: -0.5, to: 0.5 },
    { color: "rgba(248,113,113,0.5)", from: 0.5, to: 2 },
  ],
  labels: [
    { value: -2, text: "-2" },
    { value: 0, text: "0" },
    { value: 2, text: "+2" },
  ],
}

const STINT_LENGTH_RANGE: RangeBarConfig = {
  min: 5,
  max: 30,
  zones: [{ color: "rgba(209,213,219,0.5)", from: 5, to: 30 }],
  labels: [
    { value: 5, text: "5" },
    { value: 30, text: "30" },
  ],
}

function rangePercent(value: number, min: number, max: number): number {
  return clamp01((value - min) / (max - min)) * 100
}

function RangeBar({ value, range }: { value: number; range: RangeBarConfig }) {
  const markerPercent = rangePercent(value, range.min, range.max)
  return (
    <View className="mt-2">
      <View className="h-1.5 w-full flex-row overflow-hidden rounded-full bg-background">
        {range.zones.map((zone) => (
          <View
            key={`${zone.from}-${zone.to}`}
            style={{
              backgroundColor: zone.color,
              width: `${rangePercent(zone.to, range.min, range.max) - rangePercent(zone.from, range.min, range.max)}%`,
            }}
          />
        ))}
      </View>
      <View
        className="absolute top-0 h-3 w-3 -translate-x-1.5 -translate-y-[3px] rounded-full border-2 border-background bg-foreground"
        style={{ left: `${markerPercent}%` }}
      />
      <View className="mt-1 flex-row justify-between">
        {range.labels.map((label) => (
          <Text key={label.text} className="font-mono text-[10px] text-muted">
            {label.text}
          </Text>
        ))}
      </View>
    </View>
  )
}

const RADAR_AXES: {
  key: RadarMetricKey
  label: string
  modalLabel: string
  formatValue: (value: number) => string
  indicator: (value: number) => Indicator
  aboutDescription: string
  normalize: (value: number) => number
  range: RangeBarConfig
}[] = [
  {
    key: "sector_time_variance",
    label: "Sector Variance",
    modalLabel: "Sector Variance (z-score vs peers, per circuit)",
    formatValue: formatSigned,
    indicator: (value) =>
      zScoreIndicator(value, "More consistent than peers", "Less consistent than peers"),
    aboutDescription:
      "Lower z-score = more consistent sector execution than peers at the same circuit. Chart inverted — larger area = more consistent.",
    normalize: (value) => clamp01(1 - (value + 2) / 4),
    range: Z_SCORE_RANGE,
  },
  {
    key: "tyre_management_index",
    label: "Tyre Management",
    modalLabel: "Tyre Management (z-score vs peers)",
    formatValue: formatSigned,
    indicator: (value) => zScoreIndicator(value, "Better preservation", "Faster degradation"),
    aboutDescription:
      "0 = average degradation. Positive = faster tyre wear than peers. Negative = better preservation.",
    normalize: (value) => clamp01((value + 2) / 4),
    range: Z_SCORE_RANGE,
  },
  {
    key: "lap_time_consistency",
    label: "Consistency",
    modalLabel: "Consistency (z-score vs peers, per circuit)",
    formatValue: formatSigned,
    indicator: (value) =>
      zScoreIndicator(value, "More consistent than peers", "Less consistent than peers"),
    aboutDescription:
      "Lower z-score = more consistent lap pace than peers at the same circuit. Chart inverted — larger area = more consistent.",
    normalize: (value) => clamp01(1 - (value + 2) / 4),
    range: Z_SCORE_RANGE,
  },
  {
    key: "stint_length_tendency",
    label: "Stint Length",
    modalLabel: "Stint Length (laps, season average)",
    formatValue: (value) => `${value.toFixed(1)} laps`,
    indicator: stintLengthIndicator,
    aboutDescription: "Higher = prefers longer stints. Not z-scored — raw season average lap count.",
    normalize: (value) => clamp01((value - 5) / 25),
    range: STINT_LENGTH_RANGE,
  },
]

// One paragraph per archetype — mirrors web's _label_clusters-derived
// descriptions verbatim.
const ARCHETYPE_DESCRIPTIONS: Record<
  string,
  (driverCode: string, data: DriverAnalysisResponse) => string
> = {
  aggressive: (driverCode, data) =>
    `${driverCode} is classified as an Aggressive driver (tyre index: ${formatSigned(data.tyre_management_index)}) — characterised by high tyre degradation rates relative to peers. Pushes hard on tyres to extract maximum pace, typically requiring earlier pit stops.`,
  conservative: (driverCode, data) =>
    `${driverCode} is classified as a Conservative driver (tyre index: ${formatSigned(data.tyre_management_index)}) — characterised by low tyre degradation relative to peers. Preserves rubber effectively, enabling longer stints and flexible strategy options.`,
  technical: (driverCode, data) =>
    `${driverCode} is classified as a Technical driver (sector variance: ${formatSigned(data.sector_time_variance)}) — characterised by highly consistent sector execution. Delivers precise, repeatable lap times with minimal variation between sectors.`,
  inconsistent: (driverCode, data) =>
    `${driverCode} is classified as an Inconsistent driver (lap consistency: ${formatSigned(data.lap_time_consistency)}) — shows high variation in both sector execution and overall lap pace. Performance fluctuates across stints and conditions.`,
  balanced: (driverCode) =>
    `${driverCode} is classified as a Balanced driver — no strongly dominant characteristic. Adapts well across different conditions without extreme tendencies in any dimension.`,
}

const TYRE_DETAIL_THRESHOLD = 0.5

function buildTyreDetail(value: number): string | null {
  if (value > TYRE_DETAIL_THRESHOLD) {
    return `Tyre degradation index: ${formatSigned(value)} (above average — degrades tyres faster than peers)`
  }
  if (value < -TYRE_DETAIL_THRESHOLD) {
    return `Tyre degradation index: ${formatSigned(value)} (below average — preserves tyres better than peers)`
  }
  return null
}

function buildDriverInsight(
  data: DriverAnalysisResponse,
  driverCode: string,
): { paragraph: string; tyreDetail: string | null } {
  const describeArchetype = ARCHETYPE_DESCRIPTIONS[data.archetype]
  const paragraph = describeArchetype
    ? describeArchetype(driverCode, data)
    : `${driverCode} is classified as a ${data.archetype} driver.`
  return { paragraph, tyreDetail: buildTyreDetail(data.tyre_management_index) }
}

const CHART_SIZE = 260
const CHART_CENTER = CHART_SIZE / 2
const MAX_RADIUS = 92
const AXIS_COUNT = 4
const GRID_RING_FRACTIONS = [0.25, 0.5, 0.75, 1]
const GRID_COLOR = "rgba(255,255,255,0.14)"
// No `--primary` CSS-variable token exists in mobile's minimal NativeWind
// palette (tailwind.config.js only defines background/surface/pill/
// foreground/muted/destructive) — this is a fixed accent color for the data
// polygon, distinct from destructive red and plain foreground white.
const ACCENT_COLOR = "#60a5fa"
const LABEL_RADIUS_FRACTION = 1.28

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number) {
  const angleRad = (angleDeg * Math.PI) / 180
  return { x: cx + radius * Math.sin(angleRad), y: cy - radius * Math.cos(angleRad) }
}

function axisAngle(index: number): number {
  return -90 + (360 / AXIS_COUNT) * index
}

function polygonPoints(radiusFraction: number, values?: number[]): string {
  return Array.from({ length: AXIS_COUNT }, (_, i) => {
    const radius = MAX_RADIUS * (values ? values[i]! : radiusFraction)
    const { x, y } = polarToCartesian(CHART_CENTER, CHART_CENTER, radius, axisAngle(i))
    return `${x},${y}`
  }).join(" ")
}

function labelAnchor(angleDeg: number): "start" | "middle" | "end" {
  const rad = (angleDeg * Math.PI) / 180
  const cos = Math.sin(rad)
  if (Math.abs(cos) < 0.3) return "middle"
  return cos > 0 ? "start" : "end"
}

function StyleRadarChart({ chartData }: { chartData: ChartDataPoint[] }) {
  const dataValues = chartData.map((point) => point.value)
  return (
    <Svg width={CHART_SIZE} height={CHART_SIZE} viewBox={`0 0 ${CHART_SIZE} ${CHART_SIZE}`}>
      {GRID_RING_FRACTIONS.map((fraction) => (
        <Polygon
          key={fraction}
          points={polygonPoints(fraction)}
          fill="none"
          stroke={GRID_COLOR}
          strokeWidth={1}
        />
      ))}
      {chartData.map((_, i) => {
        const { x, y } = polarToCartesian(CHART_CENTER, CHART_CENTER, MAX_RADIUS, axisAngle(i))
        return (
          <Line
            key={i}
            x1={CHART_CENTER}
            y1={CHART_CENTER}
            x2={x}
            y2={y}
            stroke={GRID_COLOR}
            strokeWidth={1}
          />
        )
      })}
      <Polygon
        points={polygonPoints(1, dataValues)}
        fill={ACCENT_COLOR}
        fillOpacity={0.35}
        stroke={ACCENT_COLOR}
        strokeWidth={2}
      />
      {chartData.map((point, i) => {
        const angle = axisAngle(i)
        const { x, y } = polarToCartesian(CHART_CENTER, CHART_CENTER, MAX_RADIUS * LABEL_RADIUS_FRACTION, angle)
        return (
          <SvgText
            key={point.metric}
            x={x}
            y={y}
            dy={3}
            textAnchor={labelAnchor(angle)}
            fontSize={10}
            fill="#9ca3af"
          >
            {point.metric}
          </SvgText>
        )
      })}
    </Svg>
  )
}

export function StyleRadar({ driverId, sessionId, driverCode }: StyleRadarProps) {
  const [expanded, setExpanded] = useState(false)
  const { data, isLoading } = useDriverAnalysis(driverId, sessionId)

  if (!driverId || !sessionId) {
    return (
      <View className="h-72 items-center justify-center">
        <Text className="text-sm text-muted">No active session to derive a style profile from.</Text>
      </View>
    )
  }

  if (isLoading) {
    return <View className="h-72 w-full rounded-md bg-surface" />
  }

  if (!data) {
    return (
      <View className="h-72 items-center justify-center">
        <Text className="text-sm text-muted">No style profile available for this driver.</Text>
      </View>
    )
  }

  const chartData: ChartDataPoint[] = RADAR_AXES.map(({ key, label, normalize }) => ({
    metric: label,
    value: normalize(data[key]),
    rawValue: data[key],
  }))

  const insight = buildDriverInsight(data, driverCode ?? data.archetype)

  return (
    <View className="items-center">
      <Text className="mb-2 self-start text-xs text-muted">
        Archetype: <Text className="font-semibold text-foreground">{data.archetype}</Text>
      </Text>
      <StyleRadarChart chartData={chartData} />
      <Pressable
        onPress={() => setExpanded((v) => !v)}
        className="mt-3 self-start flex-row items-center gap-1"
        hitSlop={8}
      >
        <Text className="text-xs text-muted">{expanded ? "Hide chart details" : "About this chart"}</Text>
      </Pressable>
      {expanded && (
        <View className="mt-3 w-full gap-4">
          <View className="gap-3">
            {RADAR_AXES.map(({ key, modalLabel, formatValue, indicator, aboutDescription, range }) => {
              const rawValue = data[key]
              const ind = indicator(rawValue)
              return (
                <View key={key} className="rounded-md bg-surface p-3">
                  <Text className="text-sm font-semibold text-foreground">{modalLabel}</Text>
                  <View className="mt-2 flex-row flex-wrap items-baseline justify-between gap-x-3">
                    <Text className="font-mono text-sm font-semibold text-foreground">
                      {formatValue(rawValue)}
                    </Text>
                    <View className="flex-row items-center gap-1.5">
                      <View
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: INDICATOR_DOT_COLOR[ind.color] }}
                      />
                      <Text className="text-xs text-muted">{ind.label}</Text>
                    </View>
                  </View>
                  <RangeBar value={rawValue} range={range} />
                  <Text className="mt-2.5 text-xs leading-relaxed text-muted">{aboutDescription}</Text>
                </View>
              )
            })}
          </View>
          <View>
            <Text className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Driver insight
            </Text>
            <View className="rounded-md border border-white/10 bg-surface p-3">
              <Text className="text-sm font-medium leading-relaxed text-foreground">
                {insight.paragraph}
              </Text>
              {insight.tyreDetail && (
                <Text className="mt-2 font-mono text-xs text-muted">{insight.tyreDetail}</Text>
              )}
            </View>
          </View>
          <Text className="text-xs leading-relaxed text-muted">
            Based on race sessions only ({data.season} season) — per-circuit comparison against all
            drivers at each circuit.
          </Text>
        </View>
      )}
    </View>
  )
}
