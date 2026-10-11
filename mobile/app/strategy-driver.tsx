import { useLocalSearchParams } from "expo-router"
import { DriverStrategySheet } from "@/components/strategy/DriverStrategySheet"

// The Strategy tab's driver sheet as a route (2026-10-07), so it can be a
// native form sheet sized to its content: app/_layout.tsx registers it with
// presentation "formSheet" and sheetAllowedDetents "fitToContents". React
// Native's Modal only opens a page sheet at full height. Opened from
// app/(tabs)/strategy.tsx with the session and driver as route params.
export default function StrategyDriverSheetScreen() {
  const { sessionId, driverId } = useLocalSearchParams<{ sessionId?: string; driverId?: string }>()
  return <DriverStrategySheet sessionId={sessionId ?? null} driverId={driverId ?? null} />
}
