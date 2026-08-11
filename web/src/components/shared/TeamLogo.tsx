import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

// Served from web/public/teams/ (user-supplied PNGs) — plain static assets,
// no build-time import needed since Vite serves public/ as-is at the root.
// Keyed by TeamResponse.name exactly as backend/scripts/seed_teams.py seeds it.
const TEAM_LOGO_SLUGS: Record<string, string> = {
  McLaren: "mclaren",
  Ferrari: "ferrari",
  "Red Bull Racing": "redbull",
  Mercedes: "mercedes",
  Williams: "williams",
  Audi: "audi",
  "Aston Martin": "astonmartin",
  Alpine: "alpine",
  Haas: "haas",
  "Racing Bulls": "racingbulls",
  Cadillac: "cadillac",
}

const FALLBACK_TEAM_COLOR = "#6B7280"
const DEFAULT_LOGO_SIZE_PX = 32
const TIMING_TOWER_LOGO_SIZE_PX = 36
// These three teams' marks read too small next to the others at the
// default size — bumped up per follow-up fix. showInTimingTower overrides
// this back down regardless of team.
const LARGE_LOGO_SIZE_PX = 44
const LARGE_LOGO_TEAMS = new Set(["Red Bull Racing", "Haas", "Ferrari"])
// Cadillac's mark is dark-on-transparent — invisible against this app's
// dark background without a light backing plate behind it.
const CADILLAC_TEAM_NAME = "Cadillac"
const CADILLAC_BACKDROP_PADDING_PX = 4

interface TeamLogoProps {
  teamName: string | undefined
  teamColor?: string
  className?: string
  // Smaller size for the timing tower's row height (~20x20). Not yet wired
  // into any timing-tower component — added so it can be tested in
  // isolation first, per the Day 29 follow-up fix.
  showInTimingTower?: boolean
}

function resolveSize(teamName: string | undefined, showInTimingTower: boolean | undefined): number {
  if (showInTimingTower) return TIMING_TOWER_LOGO_SIZE_PX
  if (teamName && LARGE_LOGO_TEAMS.has(teamName)) return LARGE_LOGO_SIZE_PX
  return DEFAULT_LOGO_SIZE_PX
}

// Renders a team's logo from a local /teams/{slug}.png asset, sized per
// resolveSize(); falls back to a plain team-colored swatch — the driver
// card's previous vertical color bar, effectively — whenever the team has
// no known slug or the image itself fails to load.
export function TeamLogo({ teamName, teamColor, className, showInTimingTower }: TeamLogoProps) {
  const slug = teamName ? TEAM_LOGO_SLUGS[teamName] : undefined
  const logoUrl = slug ? `/teams/${slug}.png` : undefined
  const [failed, setFailed] = useState(false)
  const size = resolveSize(teamName, showInTimingTower)

  useEffect(() => {
    setFailed(false)
  }, [logoUrl])

  if (!logoUrl || failed) {
    return (
      <span
        role="img"
        aria-label={teamName ?? "Unknown team"}
        className={cn("inline-block flex-shrink-0 rounded", className)}
        style={{ backgroundColor: teamColor ?? FALLBACK_TEAM_COLOR, width: size, height: size }}
      />
    )
  }

  const img = (
    <img
      src={logoUrl}
      alt={`${teamName} logo`}
      onError={() => setFailed(true)}
      className="h-full w-full object-contain"
    />
  )

  if (teamName === CADILLAC_TEAM_NAME) {
    const backdropSize = size + CADILLAC_BACKDROP_PADDING_PX
    return (
      <span
        className={cn("inline-flex flex-shrink-0 items-center justify-center rounded-full bg-white", className)}
        style={{ width: backdropSize, height: backdropSize }}
      >
        <span className="inline-block" style={{ width: size, height: size }}>
          {img}
        </span>
      </span>
    )
  }

  return (
    <span className={cn("inline-block flex-shrink-0", className)} style={{ width: size, height: size }}>
      {img}
    </span>
  )
}
