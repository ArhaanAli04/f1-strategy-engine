import { useQuery } from "@tanstack/react-query"
import { Info } from "lucide-react"
import type { ReactElement } from "react"
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts"
import * as driverApi from "@/api/driver"
import { LoadingSkeleton } from "@/components/shared/LoadingSkeleton"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import type { DriverAnalysisResponse } from "@/types/driver"
import { CHART_TOOLTIP_STYLE } from "@/utils/constants"

interface StyleRadarProps {
  driverId: string | null
  sessionId: string | null
  // Human-readable driver code (e.g. "VER") for the modal's generated insight
  // sentence — DriverAnalysisResponse only carries driver_id (a UUID), so the
  // caller passes this down from its own driver roster lookup.
  driverCode?: string | null
}

// Axes are exactly the 4 real fields backend/schemas/driver_schema.py's
// DriverAnalysisResponse returns (see services/driver_service.py's
// _fit_population / backend/services/ml/driver_style.py's compute_* functions)
// — there is no aggression/smoothness/qualifying_pace field on the backend,
// and no UMAP scatter is rendered here (see CLAUDE.md's Deferred Telemetry
// Features: braking/throttle-derived style features were never ingested, so
// driver_style.py ships these 4 lap/stint-level proxies instead of the
// original spec's metrics).
//
// All 4 values are now on a comparable z-score scale, except
// stint_length_tendency (a raw lap count):
//   - sector_time_variance:  z-score, per-circuit std of race-session sector
//     times vs same-circuit peers. Lower = more consistent. Originally a
//     single std() pooled across the driver's whole season (every circuit,
//     every session type) — that pooled version mostly measured cross-circuit
//     sector-length differences, not driver consistency (the whole 2025 grid
//     spanned barely 6.7-7.9s on it). Fixed backend-side in
//     feature/style-radar-improvements: see driver_style.py's module docstring.
//   - tyre_management_index: z-score vs same-compound/season peers, signed,
//     0 = average. Positive = degrades tyres FASTER than peers (more
//     aggressive, worse preservation). Negative = degrades slower (better
//     preservation, more conservative).
//   - lap_time_consistency:  z-score, per-circuit std of race-session lap
//     times vs same-circuit peers. Lower = more consistent. Same season-pooled
//     bug and fix as sector_time_variance.
//   - stint_length_tendency: laps (mean stint length, season average, all
//     session types) — not z-scored, no population-relative comparison.
// normalize below maps each onto a 0-1 display range purely for the radar
// chart's shape (a z-score of ~-2..+2 and a lap count of ~5-30 aren't visually
// comparable on one shared linear axis otherwise). Raw values are still used
// verbatim in the modal text and insight generation below.
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

const INDICATOR_DOT_CLASS: Record<IndicatorColor, string> = {
  green: "bg-emerald-400",
  yellow: "bg-yellow-300",
  red: "bg-red-400",
  grey: "bg-gray-400",
}

const Z_SCORE_INDICATOR_THRESHOLD = 0.5

// Same threshold rule for all 3 z-score metrics — negative/low = green
// (better than peers), positive/high = red (worse than peers). This holds for
// tyre_management_index too: a negative z there means slower degradation
// (better preservation), same "lower is better" direction as the other two.
function zScoreIndicator(value: number, goodLabel: string, badLabel: string): Indicator {
  if (value < -Z_SCORE_INDICATOR_THRESHOLD) return { color: "green", label: goodLabel }
  if (value > Z_SCORE_INDICATOR_THRESHOLD) return { color: "red", label: badLabel }
  return { color: "yellow", label: "Near average" }
}

// stint_length_tendency isn't population-relative, so it gets a neutral grey
// dot describing which bucket it falls in rather than a good/bad judgement.
function stintLengthIndicator(value: number): Indicator {
  if (value < 12) return { color: "grey", label: "Short stints" }
  if (value > 20) return { color: "grey", label: "Long stints" }
  return { color: "grey", label: "Medium stints" }
}

// Visual range bar under each modal row, showing where the driver's raw
// value sits within the metric's typical span. Two shapes: the 3 z-score
// metrics share one -2..+2 config with the same green/yellow/red zone
// boundaries as zScoreIndicator above (kept as separate constants rather than
// derived from Z_SCORE_INDICATOR_THRESHOLD, since the bar's zones are a fixed
// visual span independent of that threshold's runtime logic); stint length
// gets its own 5..30 lap config with a single neutral zone.
interface RangeZone {
  colorClass: string
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
    { colorClass: "bg-emerald-400/50", from: -2, to: -0.5 },
    { colorClass: "bg-yellow-300/50", from: -0.5, to: 0.5 },
    { colorClass: "bg-red-400/50", from: 0.5, to: 2 },
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
  zones: [{ colorClass: "bg-gray-300/50", from: 5, to: 30 }],
  labels: [
    { value: 5, text: "5" },
    { value: 30, text: "30" },
  ],
}

function rangePercent(value: number, min: number, max: number): number {
  return clamp01((value - min) / (max - min)) * 100
}

function RangeBar({ value, range }: { value: number; range: RangeBarConfig }): ReactElement {
  const markerPercent = rangePercent(value, range.min, range.max)
  return (
    <div className="mt-2.5 w-full">
      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-background">
        <div className="flex h-full w-full">
          {range.zones.map((zone) => (
            <div
              key={`${zone.from}-${zone.to}`}
              className={zone.colorClass}
              style={{
                width: `${rangePercent(zone.to, range.min, range.max) - rangePercent(zone.from, range.min, range.max)}%`,
              }}
            />
          ))}
        </div>
        <div
          className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-foreground shadow-sm"
          style={{ left: `${markerPercent}%` }}
        />
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-xs tabular-nums text-muted-foreground">
        {range.labels.map((label) => (
          <span key={label.text}>{label.text}</span>
        ))}
      </div>
    </div>
  )
}

const RADAR_AXES: {
  key: RadarMetricKey
  label: string
  modalLabel: string
  tooltip: string
  formatValue: (value: number) => string
  indicator: (value: number) => Indicator
  aboutDescription: string
  note?: string
  normalize: (value: number) => number
  range: RangeBarConfig
}[] = [
  {
    key: "sector_time_variance",
    label: "Sector Variance",
    modalLabel: "Sector Variance (z-score vs peers, per circuit)",
    tooltip:
      "Sector variance — lower z-score means more consistent sector execution than peers at the same circuit",
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
    tooltip:
      "Tyre management — higher z-score means faster tyre degradation (more aggressive), lower means better preservation",
    formatValue: formatSigned,
    indicator: (value) => zScoreIndicator(value, "Better preservation", "Faster degradation"),
    aboutDescription: "0 = average degradation. Positive = faster tyre wear than peers. Negative = better preservation.",
    normalize: (value) => clamp01((value + 2) / 4),
    range: Z_SCORE_RANGE,
  },
  {
    key: "lap_time_consistency",
    label: "Consistency",
    modalLabel: "Consistency (z-score vs peers, per circuit)",
    tooltip:
      "Lap consistency — lower z-score means more consistent lap pace than peers at the same circuit",
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
    tooltip: "Stint length tendency — higher means preference for longer stints",
    formatValue: (value) => `${value.toFixed(1)} laps`,
    indicator: stintLengthIndicator,
    aboutDescription: "Higher = prefers longer stints. Not z-scored — raw season average lap count.",
    normalize: (value) => clamp01((value - 5) / 25),
    range: STINT_LENGTH_RANGE,
  },
]

const AXIS_DESCRIPTIONS: Record<string, string> = Object.fromEntries(
  RADAR_AXES.map(({ label, tooltip }) => [label, tooltip]),
)

// One paragraph per archetype (see driver_style.py's _label_clusters — a
// fixed-priority heuristic over K-Means centroids, not a statistical
// taxonomy). The most relevant raw metric for that archetype's defining
// characteristic is included inline: tyre index for aggressive/conservative
// (the metric _label_clusters actually sorts on for those two), sector
// variance for technical, lap consistency for inconsistent — all 3 are
// z-scores now, formatted signed (e.g. +0.23 / -0.81), not seconds.
// stint_length_tendency never factors into archetype labeling, so it has no
// parenthetical here.
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

// tyre_management_index is the one metric that's genuinely zero-centered
// (a z-score), so it's the only one where an "above/below average" secondary
// detail is defensible without population data for the other three metrics.
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

// Custom tick so each axis label carries a native SVG <title> tooltip
// (no Radix/shadcn Tooltip dependency in this project yet — a browser-native
// title is zero-dependency and works identically in the web app and the
// Tauri desktop webview).
interface AxisTickProps {
  x?: number
  y?: number
  textAnchor?: "start" | "middle" | "end" | "inherit"
  payload?: { value: string }
}

function AxisTick({ x, y, textAnchor, payload }: AxisTickProps): ReactElement | null {
  if (!payload) return null
  return (
    <text x={x} y={y} textAnchor={textAnchor} className="text-xs fill-muted-foreground">
      <title>{AXIS_DESCRIPTIONS[payload.value] ?? payload.value}</title>
      {payload.value}
    </text>
  )
}

// Shared between the page chart and the modal's (smaller) left-column chart
// so the two never drift out of sync. domain={[0, 1]} on PolarRadiusAxis is
// load-bearing, not decorative: without it Recharts auto-fits the radial
// scale to this driver's own min/max normalized value, so a driver whose 4
// metrics all cluster together (e.g. near-average z-scores) gets a shape that
// looks maximally filled regardless of how consistent/inconsistent they
// actually are — the whole point of a shared 0-1 scale across drivers.
// outerRadius/margin are overridable per call site: the page chart's wide
// container needs neither, but the modal's narrower left column needs a
// smaller outerRadius (as an absolute px, not "%") plus wider side margins so
// axis labels like "Tyre Management" have room to render without clipping.
function StyleRadarChart({
  chartData,
  height,
  outerRadius = "75%",
  margin,
}: {
  chartData: ChartDataPoint[]
  height: number
  outerRadius?: string | number
  margin?: { top: number; right: number; bottom: number; left: number }
}): ReactElement {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={chartData} outerRadius={outerRadius} margin={margin}>
        <PolarGrid className="stroke-border" />
        <PolarAngleAxis dataKey="metric" tick={<AxisTick />} />
        <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
        <Tooltip
          formatter={(value, _name, item) => {
            const raw = item.payload?.rawValue
            return typeof raw === "number" ? raw.toFixed(3) : value
          }}
          {...CHART_TOOLTIP_STYLE}
        />
        <Radar
          dataKey="value"
          stroke="var(--primary)"
          fill="var(--primary)"
          fillOpacity={0.35}
          isAnimationActive={false}
        />
      </RadarChart>
    </ResponsiveContainer>
  )
}

export function StyleRadar({ driverId, sessionId, driverCode }: StyleRadarProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["driver", "analysis", driverId, sessionId],
    queryFn: () => driverApi.getDriverAnalysis(driverId as string, sessionId as string),
    enabled: Boolean(driverId && sessionId),
    retry: false,
  })

  if (!driverId || !sessionId) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        No active session to derive a style profile from.
      </div>
    )
  }

  if (isLoading) {
    return <LoadingSkeleton className="h-72 w-full" />
  }

  if (!data) {
    return (
      <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
        No style profile available for this driver.
      </div>
    )
  }

  const chartData: ChartDataPoint[] = RADAR_AXES.map(({ key, label, normalize }) => ({
    metric: label,
    value: normalize(data[key]),
    rawValue: data[key],
  }))

  const insight = buildDriverInsight(data, driverCode ?? data.archetype)

  return (
    <div
      role="img"
      aria-label={`Driving style radar for archetype ${data.archetype}: ${chartData
        .map((point) => `${point.metric} ${point.rawValue.toFixed(2)}`)
        .join(", ")}`}
    >
      <p className="mb-2 text-xs text-muted-foreground">
        Archetype: <span className="font-semibold text-foreground">{data.archetype}</span>
      </p>
      <StyleRadarChart chartData={chartData} height={288} />
      <Dialog>
        <DialogTrigger asChild>
          <button
            type="button"
            className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <Info className="h-3 w-3" />
            About this chart
          </button>
        </DialogTrigger>
        <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>About the Driving Style Chart</DialogTitle>
          </DialogHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto md:flex-row md:gap-0 md:divide-x md:divide-border md:overflow-hidden">
            {/* Left column: fixed width regardless of modal width — only the
                right column grows when the modal gets wider, so a wider modal
                buys the info column more room instead of stretching the chart. */}
            <div className="flex flex-shrink-0 flex-col items-center gap-2 md:w-[340px] md:overflow-y-auto md:pr-6">
              <StyleRadarChart
                chartData={chartData}
                height={300}
                outerRadius={85}
                margin={{ top: 16, right: 40, bottom: 16, left: 40 }}
              />
              <div className="text-center">
                {driverCode && (
                  <p className="text-sm font-semibold text-foreground">{driverCode}</p>
                )}
                <p className="text-xs text-muted-foreground">
                  Archetype: <span className="font-semibold text-foreground">{data.archetype}</span>
                </p>
              </div>
            </div>
            {/* Right column: everything else, scrolls independently on md+.
                No fixed width — flex-1 takes whatever the fixed-width left
                column doesn't use, so it's the one that grows with the modal. */}
            <div className="min-h-0 flex-1 space-y-6 text-sm md:overflow-y-auto md:pl-6">
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  What each axis means
                </h3>
                <dl className="divide-y divide-border">
                  {RADAR_AXES.map(
                    ({ key, modalLabel, formatValue, indicator, aboutDescription, note, range }) => {
                      const rawValue = data[key]
                      const ind = indicator(rawValue)
                      return (
                        <div key={key} className="py-3.5 first:pt-0 last:pb-0">
                          <dt className="text-sm font-semibold text-foreground">{modalLabel}</dt>
                          <dd className="mt-2">
                            <div className="rounded-md bg-muted/40 px-3 py-2.5">
                              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                                <span className="font-mono text-sm font-semibold tabular-nums text-foreground">
                                  {formatValue(rawValue)}
                                </span>
                                <span className="inline-flex items-center gap-1.5">
                                  <span
                                    className={cn(
                                      "h-2 w-2 flex-shrink-0 rounded-full",
                                      INDICATOR_DOT_CLASS[ind.color],
                                    )}
                                  />
                                  <span className="text-xs text-muted-foreground">{ind.label}</span>
                                </span>
                              </div>
                              <RangeBar value={rawValue} range={range} />
                            </div>
                            <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
                              {aboutDescription}
                            </p>
                            {note && (
                              <p className="mt-1 text-xs italic text-muted-foreground">{note}</p>
                            )}
                          </dd>
                        </div>
                      )
                    },
                  )}
                </dl>
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Driver insight
                </h3>
                <div className="rounded-md border border-border bg-card px-4 py-3">
                  <p className="text-sm font-medium leading-relaxed text-foreground">
                    {insight.paragraph}
                  </p>
                  {insight.tyreDetail && (
                    <p className="mt-2 font-mono text-xs tabular-nums text-muted-foreground">
                      {insight.tyreDetail}
                    </p>
                  )}
                </div>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Based on race sessions only ({data.season} season) — per-circuit comparison
                against all drivers at each circuit.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
