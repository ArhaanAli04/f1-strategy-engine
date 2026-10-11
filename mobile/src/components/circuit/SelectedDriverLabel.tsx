import { Text } from "react-native"
import Animated, { useAnimatedStyle, type SharedValue } from "react-native-reanimated"
import { SELECTED_DOT_RADIUS } from "@/components/circuit/AnimatedDriverDot"

// The outline viewBox every circuit uses ("0 0 1000 1000"), so map units turn
// into screen pixels the way the SVG's preserveAspectRatio "xMidYMid meet"
// does: one scale for both axes, the square centred in the map.
const VIEWBOX_SIZE = 1000

// The pill, in screen pixels. Codes are always three letters, so it has a
// fixed width and is centred on the dot by offsetting half of it.
const LABEL_WIDTH_PX = 44
const LABEL_HEIGHT_PX = 20
const LABEL_GAP_PX = 3
const LABEL_BORDER_WIDTH_PX = 1.5
const LABEL_BACKGROUND = "rgba(10, 10, 10, 0.9)"

interface SelectedDriverLabelProps {
  code: string
  color: string
  // The selected dot's centre in viewBox units, updated every frame by its
  // AnimatedDriverDot.
  x: SharedValue<number>
  y: SharedValue<number>
  // The map's measured size in pixels (same box the SVG fills).
  mapWidth: number
  mapHeight: number
}

// The selected driver's code, in a pill above their dot, following it (owner's
// choice, 2026-10-09; web and desktop draw it inside the SVG). On mobile it is
// a React Native view over the map rather than SVG text, because Reanimated
// animates a view's transform reliably while react-native-svg's <Text>
// rebuilds its x/y on render, which animated props skip. It moves on the UI
// thread from the dot's shared centre, sits above every dot, and flips below
// the dot when there is no room above it.
export function SelectedDriverLabel({ code, color, x, y, mapWidth, mapHeight }: SelectedDriverLabelProps) {
  const animatedStyle = useAnimatedStyle(() => {
    const scale = Math.min(mapWidth, mapHeight) / VIEWBOX_SIZE
    const offsetX = (mapWidth - VIEWBOX_SIZE * scale) / 2
    const offsetY = (mapHeight - VIEWBOX_SIZE * scale) / 2
    const dotX = offsetX + x.value * scale
    const dotY = offsetY + y.value * scale
    const dotRadius = SELECTED_DOT_RADIUS * scale
    const aboveTop = dotY - dotRadius - LABEL_GAP_PX - LABEL_HEIGHT_PX
    const top = aboveTop < 0 ? dotY + dotRadius + LABEL_GAP_PX : aboveTop
    return {
      transform: [{ translateX: dotX - LABEL_WIDTH_PX / 2 }, { translateY: top }],
    }
  })

  return (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: "absolute",
          left: 0,
          top: 0,
          width: LABEL_WIDTH_PX,
          height: LABEL_HEIGHT_PX,
          borderRadius: LABEL_HEIGHT_PX / 2,
          borderWidth: LABEL_BORDER_WIDTH_PX,
          borderColor: color,
          backgroundColor: LABEL_BACKGROUND,
          alignItems: "center",
          justifyContent: "center",
        },
        animatedStyle,
      ]}
    >
      <Text className="text-xs font-bold text-foreground">{code}</Text>
    </Animated.View>
  )
}
