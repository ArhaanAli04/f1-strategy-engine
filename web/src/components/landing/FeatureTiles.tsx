import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { SampleChip } from "./SampleChip"
import { ProgressBar } from "@/components/shared/ProgressBar"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { ROUTES } from "@/utils/constants"
import { formatLapTime, formatRaceTime, getCompoundColor, getCompoundLabel } from "@/utils/formatters"

// Every value in this file is hand-authored, clearly-labeled sample output —
// /strategy/* is auth-gated (see CLAUDE.md), so a signed-out visitor cannot
// fetch a real live prediction. Each tile mirrors its real in-app
// counterpart's exact visual grammar (component reuse: ProgressBar, the
// formatters, the row-void/row-recede/pill-surface tokens) so the page
// demonstrates the product's real look rather than a generic marketing
// approximation of it — only the race positions/lap numbers/probabilities
// are synthetic.
//
// Driver codes and team color_hex values are real, sourced from the
// project's own canonical 2026 grid (backend/scripts/seed_teams.py) — not
// invented and not FALLBACK_TEAM_COLOR. HAD (Isack Hadjar) is seeded there
// as VER's Red Bull teammate, which is what makes the Undercut tile's
// "VER threatened by HAD" pairing a real intra-team scenario, not an
// arbitrary one.
const SAMPLE_TEAM_COLORS: Record<string, string> = {
  VER: "#3671C6", // Red Bull Racing
  HAD: "#3671C6", // Red Bull Racing
  NOR: "#FF8000", // McLaren
  PIA: "#FF8000", // McLaren
  HAM: "#E8002D", // Ferrari
  LEC: "#E8002D", // Ferrari
  RUS: "#27F4D2", // Mercedes
}
function SampleBadge() {
  return (
    <span className="rounded bg-pill-surface px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
      Sample
    </span>
  )
}

function TileCard({
  title,
  to,
  children,
  className,
}: {
  title: string
  to: string
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-base">
          <Link to={to} className="hover:underline">
            {title}
          </Link>
        </CardTitle>
        <SampleBadge />
      </CardHeader>
      <CardContent className="flex-1 space-y-2">{children}</CardContent>
    </Card>
  )
}

// Mirrors PitWindowCard's compact variant: chip + "L{start}-{end}" tabular-nums
// + one-line SHAP-style explanation.
export function PitWindowTile() {
  return (
    <TileCard title="Pit Window Prediction" to={ROUTES.LIVE_RACE}>
      <div className="flex items-center justify-between gap-2">
        <SampleChip code="NOR" color={SAMPLE_TEAM_COLORS.NOR} />
        <span className="font-mono text-sm font-semibold tabular-nums">L31-L34</span>
      </div>
      <p className="text-xs text-muted-foreground">
        Predicted tyre life remaining is the primary factor — 6.2 laps (+0.41 impact)
      </p>
    </TileCard>
  )
}

// Mirrors UndercutThreatPanel's ThreatRow: label + chip + ProgressBar +
// probability% + colored net gain/loss + recommended action. VER/HAD are
// real Red Bull teammates in seed_teams.py, so this reads as a genuine
// intra-team undercut threat rather than an arbitrary pairing.
export function UndercutTile() {
  return (
    <TileCard title="Undercut / Overcut Probability" to={ROUTES.LIVE_RACE}>
      <div className="space-y-1.5 rounded-md border p-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <SampleChip code="VER" color={SAMPLE_TEAM_COLORS.VER} />
            <span className="text-xs font-medium text-muted-foreground">
              Threat — car behind
            </span>
          </div>
          <SampleChip code="HAD" color={SAMPLE_TEAM_COLORS.HAD} />
        </div>
        <ProgressBar value={0.68} />
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>68% gain probability</span>
          <span className="font-semibold text-[#10B981]">+0.412s net gain</span>
        </div>
        <p className="text-xs">HAD to pit now — undercut favored</p>
      </div>
    </TileCard>
  )
}

const SAMPLE_TIMING_ROWS = [
  { position: 1, code: "VER", gap: "Leader", compound: "HARD" },
  { position: 2, code: "NOR", gap: "+1.842", compound: "MEDIUM" },
  { position: 3, code: "PIA", gap: "+4.207", compound: "MEDIUM" },
  { position: 4, code: "HAM", gap: "+7.431", compound: "HARD" },
  { position: 5, code: "LEC", gap: "+9.876", compound: "SOFT" },
]

// Mirrors LiveTimingTower's row grammar: position, team bar, code, gap
// (tabular-nums), compound badge — border-b rows, no zebra (matches the real
// component, which doesn't zebra this particular list).
export function TimingTowerTile() {
  return (
    <TileCard title="Live Timing Tower" to={ROUTES.LIVE_RACE}>
      <div className="flex flex-col">
        {SAMPLE_TIMING_ROWS.map((row) => (
          <div
            key={row.position}
            className="flex items-center justify-between border-b py-1.5 text-xs last:border-b-0"
          >
            <span className="w-5 text-center font-mono text-muted-foreground">{row.position}</span>
            <span
              className="h-5 w-1 flex-shrink-0 rounded-full"
              style={{ backgroundColor: SAMPLE_TEAM_COLORS[row.code] }}
            />
            <span className="w-10 font-semibold">{row.code}</span>
            <span className="w-16 text-right font-mono tabular-nums text-muted-foreground">
              {row.gap}
            </span>
            <span
              className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-[#1a1a1a] text-[10px] font-bold"
              style={{ color: getCompoundColor(row.compound) }}
            >
              {getCompoundLabel(row.compound)}
            </span>
          </div>
        ))}
      </div>
    </TileCard>
  )
}

const SAMPLE_SECTOR_ROWS = [
  { position: 1, code: "VER", classes: ["purple", "green", "yellow", "yellow"] },
  { position: 2, code: "NOR", classes: ["green", "purple", "green", "yellow"] },
  { position: 3, code: "HAM", classes: ["yellow", "yellow", "purple", "green"] },
  { position: 4, code: "LEC", classes: ["yellow", "green", "yellow", "purple"] },
  { position: 5, code: "RUS", classes: ["yellow", "yellow", "yellow", "green"] },
] as const
const SAMPLE_SECTOR_STYLES: Record<string, string> = {
  purple: "text-purple-400",
  green: "text-emerald-400",
  yellow: "text-yellow-300",
}
const SAMPLE_SECTOR_LABELS = ["LAP", "S1", "S2", "S3"]
const SAMPLE_SECTOR_VALUES = [92.481, 28.104, 31.882, 32.495]

// Mirrors SectorHeatmap's own grid: position + team bar + code, then 4
// purple/green/yellow classification pills per driver row (bg-pill-surface,
// real F1 broadcast convention).
export function SectorHeatmapTile() {
  return (
    <TileCard title="Sector Time Heatmap" to={ROUTES.LIVE_RACE}>
      <div className="flex flex-col gap-0.5">
        <div className="grid grid-cols-[3.5rem_1fr_1fr_1fr_1fr] gap-1 px-1 text-[10px] font-medium text-muted-foreground">
          <span>DRIVER</span>
          {SAMPLE_SECTOR_LABELS.map((label) => (
            <span key={label} className="text-center">
              {label}
            </span>
          ))}
        </div>
        {SAMPLE_SECTOR_ROWS.map((row, rowIndex) => (
          <div
            key={row.code}
            className={cn(
              "grid grid-cols-[3.5rem_1fr_1fr_1fr_1fr] items-center gap-1 rounded px-1 py-1",
              rowIndex % 2 === 0 ? "bg-row-void" : "bg-row-recede",
            )}
          >
            <span className="flex items-center gap-1">
              <span
                className="h-4 w-1 flex-shrink-0 rounded-full"
                style={{ backgroundColor: SAMPLE_TEAM_COLORS[row.code] }}
              />
              <span className="text-xs font-semibold">{row.code}</span>
            </span>
            {row.classes.map((sectorClass, colIndex) => (
              <span key={colIndex} className="flex justify-center">
                <span
                  className={cn(
                    "rounded-md bg-pill-surface px-1.5 py-0.5 text-center font-mono text-[10px] tabular-nums",
                    SAMPLE_SECTOR_STYLES[sectorClass],
                  )}
                >
                  {formatLapTime(SAMPLE_SECTOR_VALUES[colIndex])}
                </span>
              </span>
            ))}
          </div>
        ))}
      </div>
    </TileCard>
  )
}

const SAMPLE_STRATEGIES = [
  { name: "NOR — Pit L34 (M→H)", change: 2, finishTime: 5432.104 },
  { name: "NOR — Pit L30 (M→S)", change: -1, finishTime: 5439.771 },
  { name: "NOR — No stop", change: -3, finishTime: 5451.203 },
]

// Mirrors SimulatorPage's strategy result list: row-void/row-recede zebra,
// colored position-change delta, monospace finish time.
export function SimulatorTile() {
  return (
    <TileCard title="Monte Carlo Strategy Simulator" to={ROUTES.SIMULATE}>
      <div className="space-y-1">
        {SAMPLE_STRATEGIES.map((strategy, index) => (
          <div
            key={strategy.name}
            className={cn(
              "grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded px-2 py-1.5 text-xs",
              index % 2 === 0 ? "bg-row-void" : "bg-row-recede",
            )}
          >
            <span className="truncate">{strategy.name}</span>
            <span
              className={cn(
                "text-right font-mono font-semibold tabular-nums",
                strategy.change >= 0 ? "text-[#10B981]" : "text-[#EF4444]",
              )}
            >
              {strategy.change > 0 ? "+" : ""}
              {strategy.change}
            </span>
            <span className="text-right font-mono tabular-nums text-muted-foreground">
              {formatRaceTime(strategy.finishTime)}
            </span>
          </div>
        ))}
      </div>
    </TileCard>
  )
}
