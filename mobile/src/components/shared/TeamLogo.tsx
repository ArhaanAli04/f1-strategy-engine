import { View } from "react-native"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

interface TeamLogoProps {
  teamColor?: string
  size?: number
}

// Simplified RN equivalent of web/src/components/shared/TeamLogo.tsx — web
// renders real per-team logo PNGs (web/public/teams/*.png) with a
// color-swatch fallback for unknown teams/failed loads. Those PNG assets
// were never ported to mobile, so this always renders the swatch — visually
// the same as web's fallback path. Add real logo assets (via
// react-native-svg or bundled PNGs) if a future day wants full parity.
export function TeamLogo({ teamColor, size = 32 }: TeamLogoProps) {
  return (
    <View
      style={{
        backgroundColor: teamColor ?? FALLBACK_TEAM_COLOR,
        width: size,
        height: size,
        borderRadius: 6,
      }}
    />
  )
}
