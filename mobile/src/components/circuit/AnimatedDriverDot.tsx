import { useEffect } from "react"
import Animated, { Easing, useAnimatedProps, useReducedMotion, useSharedValue, withTiming } from "react-native-reanimated"
import { Circle } from "react-native-svg"

const AnimatedCircle = Animated.createAnimatedComponent(Circle)

const DOT_RADIUS = 12
const SELECTED_DOT_RADIUS = 18
const DOT_STROKE_WIDTH = 1.5
const SELECTED_DOT_STROKE_WIDTH = 3
// Matches web's --duration-dot-glide (1.8s) — slightly under
// useDriverPositions's 2s poll interval so a dot finishes easing into
// place before the next update arrives.
const DOT_GLIDE_DURATION_MS = 1800

interface AnimatedDriverDotProps {
  cx: number
  cy: number
  color: string
  isSelected: boolean
}

// RN equivalent of CircuitMapPanel.tsx's inline <circle> with a CSS
// `transform` transition — react-native-svg's Circle has no CSS transitions
// to lean on, so position glide is done explicitly via Reanimated's
// useAnimatedProps driving cx/cy as shared values. One dot = one instance =
// its own pair of shared values, since hooks can't run inside the parent's
// .map() loop (Rules of Hooks) — CircuitMapPanel renders one of these per
// live position instead of a bare <Circle>.
export function AnimatedDriverDot({ cx, cy, color, isSelected }: AnimatedDriverDotProps) {
  const animatedCx = useSharedValue(cx)
  const animatedCy = useSharedValue(cy)
  const prefersReducedMotion = useReducedMotion()

  useEffect(() => {
    if (prefersReducedMotion) {
      animatedCx.value = cx
      animatedCy.value = cy
      return
    }
    const config = { duration: DOT_GLIDE_DURATION_MS, easing: Easing.inOut(Easing.cubic) }
    animatedCx.value = withTiming(cx, config)
    animatedCy.value = withTiming(cy, config)
  }, [cx, cy, prefersReducedMotion, animatedCx, animatedCy])

  const animatedProps = useAnimatedProps(() => ({
    cx: animatedCx.value,
    cy: animatedCy.value,
  }))

  return (
    <AnimatedCircle
      animatedProps={animatedProps}
      r={isSelected ? SELECTED_DOT_RADIUS : DOT_RADIUS}
      fill={color}
      stroke="#ffffff"
      strokeWidth={isSelected ? SELECTED_DOT_STROKE_WIDTH : DOT_STROKE_WIDTH}
    />
  )
}
