import Svg, { Circle, Path, Text as SvgText } from "react-native-svg"
import { COMPOUND_COLORS } from "@/utils/constants"
import { getCompoundColor, getCompoundLabel } from "@/utils/formatters"

const ICON_SIZE = 24
const ICON_BG = "#1a1a1a"

interface TyreIconProps {
  compound: string | null
}

// RN port of the TyreIcon function inline in
// web/src/components/telemetry/LiveTimingTower.tsx — same two-arc F1-style
// tyre glyph, same geometry, extracted into its own file since mobile reuses
// it on both the Live tab and Driver Detail.
export function TyreIcon({ compound }: TyreIconProps) {
  const color = compound ? getCompoundColor(compound) : COMPOUND_COLORS.UNKNOWN
  const label = compound ? getCompoundLabel(compound) : "?"

  return (
    <Svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24">
      <Circle cx={12} cy={12} r={11} fill={ICON_BG} />
      {/* Right arc: theta 12°→168° (measured clockwise from 12 o'clock) */}
      <Path
        d="M 13.87 3.2 A 9 9 0 0 1 13.87 20.8"
        fill="none"
        stroke={color}
        strokeWidth={1.6}
        strokeLinecap="round"
      />
      {/* Left arc: theta 192°→348°, mirrors the right arc */}
      <Path
        d="M 10.13 20.8 A 9 9 0 0 1 10.13 3.2"
        fill="none"
        stroke={color}
        strokeWidth={1.6}
        strokeLinecap="round"
      />
      <SvgText
        x={12}
        y={12}
        dy={3.5}
        textAnchor="middle"
        fontSize={10}
        fontWeight="bold"
        fill={color}
      >
        {label}
      </SvgText>
    </Svg>
  )
}
