import { Image, View, type ImageSourcePropType } from "react-native"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

// RN port of web/src/components/shared/TeamLogo.tsx — same slug map, same
// size overrides, same Cadillac white-backdrop special case. Metro requires
// static `require()` calls (no dynamic string interpolation like web's
// `/teams/${slug}.png` URL), so the slug map below maps straight to the
// bundled asset instead of a slug string.
const TEAM_LOGOS: Record<string, ImageSourcePropType> = {
  McLaren: require("../../../assets/teams/mclaren.png"),
  Ferrari: require("../../../assets/teams/ferrari.png"),
  "Red Bull Racing": require("../../../assets/teams/redbull.png"),
  Mercedes: require("../../../assets/teams/mercedes.png"),
  Williams: require("../../../assets/teams/williams.png"),
  Audi: require("../../../assets/teams/audi.png"),
  "Aston Martin": require("../../../assets/teams/astonmartin.png"),
  Alpine: require("../../../assets/teams/alpine.png"),
  Haas: require("../../../assets/teams/haas.png"),
  "Racing Bulls": require("../../../assets/teams/racingbulls.png"),
  Cadillac: require("../../../assets/teams/cadillac.png"),
}

const DEFAULT_LOGO_SIZE_PX = 32
// Same three teams whose marks read too small at the default size on web.
const LARGE_LOGO_SIZE_PX = 44
const LARGE_LOGO_TEAMS = new Set(["Red Bull Racing", "Haas", "Ferrari"])
// Cadillac's mark is dark-on-transparent — invisible against this app's dark
// background without a light backing plate behind it, same as web.
const CADILLAC_TEAM_NAME = "Cadillac"
const CADILLAC_BACKDROP_PADDING_PX = 4

interface TeamLogoProps {
  teamName?: string
  teamColor?: string
  size?: number
}

function resolveSize(teamName: string | undefined, size: number | undefined): number {
  if (size !== undefined) return size
  if (teamName && LARGE_LOGO_TEAMS.has(teamName)) return LARGE_LOGO_SIZE_PX
  return DEFAULT_LOGO_SIZE_PX
}

export function TeamLogo({ teamName, teamColor, size }: TeamLogoProps) {
  const source = teamName ? TEAM_LOGOS[teamName] : undefined
  const resolvedSize = resolveSize(teamName, size)

  if (!source) {
    return (
      <View
        style={{
          backgroundColor: teamColor ?? FALLBACK_TEAM_COLOR,
          width: resolvedSize,
          height: resolvedSize,
          borderRadius: 6,
        }}
      />
    )
  }

  const img = (
    <Image
      source={source}
      style={{ width: resolvedSize, height: resolvedSize }}
      resizeMode="contain"
    />
  )

  if (teamName === CADILLAC_TEAM_NAME) {
    const backdropSize = resolvedSize + CADILLAC_BACKDROP_PADDING_PX
    return (
      <View
        style={{
          width: backdropSize,
          height: backdropSize,
          borderRadius: backdropSize / 2,
          backgroundColor: "#ffffff",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {img}
      </View>
    )
  }

  return img
}
