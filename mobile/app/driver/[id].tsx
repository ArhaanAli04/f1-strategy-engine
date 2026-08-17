import { useLocalSearchParams } from "expo-router"
import { useState } from "react"
import { Pressable, ScrollView, Text, View } from "react-native"
import { SectorComparison } from "@/components/driver/SectorComparison"
import { StyleRadar } from "@/components/driver/StyleRadar"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { TeamLogo } from "@/components/shared/TeamLogo"
import { useDriverAnalysis } from "@/hooks/useDriverAnalysis"
import { useDriverSeasonStats } from "@/hooks/useDriverSeasonStats"
import { useDrivers } from "@/hooks/useDrivers"
import { useResolvedSession } from "@/hooks/useResolvedSession"
import { FALLBACK_TEAM_COLOR } from "@/utils/constants"

// Full port of web/src/pages/DriverPage.tsx, replacing the Day 31 identity-
// only stub (which deferred all 3 charts until victory-native +
// @shopify/react-native-skia were installed — see CLAUDE.md's Day 32
// deferred-wiring note). Segmented control instead of web's always-visible
// 3-card grid — 3 charts don't fit one mobile screen at once, so this
// paginates them behind Overview / Driving Style / Sector Times, per the
// Day 32 spec's explicit choice of a hand-rolled tab switcher over pulling
// in @react-navigation/material-top-tabs.

type SubView = "overview" | "style" | "sectors"

const SUB_VIEWS: { key: SubView; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "style", label: "Driving Style" },
  { key: "sectors", label: "Sector Times" },
]

interface StatTileProps {
  value: number | string
  label: string
}

function StatTile({ value, label }: StatTileProps) {
  return (
    <View className="items-center gap-0.5">
      <Text className="text-lg font-bold text-foreground">{value}</Text>
      <Text className="text-[10px] uppercase tracking-wide text-muted">{label}</Text>
    </View>
  )
}

function formatRaceDate(raceDate: string): string {
  return new Date(raceDate).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })
}

interface HistoricalDataBannerProps {
  raceName?: string | null
  raceDate?: string | null
}

// RN port of web/src/components/shared/HistoricalDataBanner.tsx — informational
// blue tone, not the destructive/red used elsewhere. Simplified vs. web:
// dismissal is plain component state here, not persisted to AsyncStorage —
// web remembers a dismissal per session id across visits (localStorage);
// this resets each time the screen mounts. Not requested for this checkpoint,
// and adds an async-storage round trip before first paint for a banner whose
// whole job is a same-session "heads up" notice.
function HistoricalDataBanner({ raceName, raceDate }: HistoricalDataBannerProps) {
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null

  const message =
    raceName && raceDate
      ? `No live race session active — showing data from the last completed race: ${raceName} (${formatRaceDate(raceDate)})`
      : "No live race session active — showing data from the last completed race"

  return (
    <View className="flex-row items-center justify-between gap-3 border-b border-blue-900/40 bg-blue-950/40 px-4 py-2">
      <Text className="flex-1 text-xs text-blue-200">{message}</Text>
      <Pressable onPress={() => setDismissed(true)} hitSlop={8} accessibilityLabel="Dismiss">
        <Text className="text-xs text-blue-300">Dismiss</Text>
      </Pressable>
    </View>
  )
}

export default function DriverDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const driverId = id ?? null
  const [subView, setSubView] = useState<SubView>("overview")

  const { data: drivers, dataUpdatedAt, isLoading: driversLoading } = useDrivers()
  const { sessionId, isLive, raceName, raceDate } = useResolvedSession()

  const driver = drivers?.find((d) => d.id === driverId) ?? null
  const team = driver?.contracts[0]?.team ?? null
  const teamColor = team?.color_hex ?? FALLBACK_TEAM_COLOR

  // Shared with StyleRadar (same queryKey) so the header's archetype text and
  // the chart itself dedupe onto one request instead of firing it twice.
  const { data: analysis } = useDriverAnalysis(driverId, sessionId)

  // Always the current season regardless of which historical session
  // sessionId is scoping the other 2 tabs to — season stats are a "who's
  // leading the championship right now" readout. Mirrors web's DriverPage.
  const { data: seasonStats, isLoading: statsLoading } = useDriverSeasonStats(
    driver?.code ?? null,
    new Date().getFullYear(),
  )

  if (driversLoading) {
    return <View className="flex-1 bg-background" />
  }

  if (!driver) {
    return (
      <View className="flex-1 items-center justify-center bg-background p-6">
        <Text className="text-sm text-muted">Driver not found.</Text>
      </View>
    )
  }

  const hasLastResults = seasonStats && (seasonStats.lastWinCircuit || seasonStats.lastPodiumCircuit)

  return (
    <View className="flex-1 bg-background">
      <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      {sessionId && !isLive && <HistoricalDataBanner raceName={raceName} raceDate={raceDate} />}
      <ScrollView className="flex-1">
        <View className="h-1.5" style={{ backgroundColor: teamColor }} />
        <View className="gap-4 p-4">
          <View className="flex-row items-center gap-3">
            <TeamLogo teamName={team?.name} teamColor={teamColor} />
            <View className="flex-1">
              <Text className="text-2xl font-bold text-foreground">{driver.full_name}</Text>
              <Text className="text-sm text-muted">{team?.name ?? "No team"}</Text>
            </View>
          </View>
          {analysis && (
            <Text className="text-xs text-muted">
              Archetype: <Text className="font-semibold text-foreground">{analysis.archetype}</Text>
            </Text>
          )}

          <View className="flex-row rounded-md border border-white/10 bg-surface">
            {SUB_VIEWS.map(({ key, label }) => {
              const active = subView === key
              return (
                <Pressable
                  key={key}
                  onPress={() => setSubView(key)}
                  className={`flex-1 items-center border-b-2 py-2.5 ${active ? "border-foreground" : "border-transparent"}`}
                >
                  <Text className={`text-xs font-medium ${active ? "text-foreground" : "text-muted"}`}>
                    {label}
                  </Text>
                </Pressable>
              )
            })}
          </View>

          {subView === "overview" && (
            <View className="gap-4">
              <View className="rounded-md border border-white/10 bg-surface p-4">
                {statsLoading ? (
                  <View className="h-10 w-full" />
                ) : seasonStats ? (
                  <View className="flex-row justify-around">
                    <StatTile value={seasonStats.wins} label="Wins" />
                    <StatTile value={seasonStats.podiums} label="Podiums" />
                    <StatTile value={seasonStats.points} label="Points" />
                    <StatTile value={seasonStats.wdcPosition ?? "—"} label="WDC Pos" />
                  </View>
                ) : (
                  <Text className="text-center text-sm text-muted">No season stats available.</Text>
                )}
                {hasLastResults && (
                  <View className="mt-3 gap-1 border-t border-white/10 pt-2">
                    {seasonStats.lastWinCircuit && (
                      <Text className="text-xs text-muted">
                        Last win: <Text className="text-foreground">{seasonStats.lastWinCircuit}</Text>
                      </Text>
                    )}
                    {seasonStats.lastPodiumCircuit && (
                      <Text className="text-xs text-muted">
                        Last podium:{" "}
                        <Text className="text-foreground">{seasonStats.lastPodiumCircuit}</Text>
                      </Text>
                    )}
                  </View>
                )}
              </View>
            </View>
          )}

          {subView === "style" && (
            <View className="rounded-md border border-white/10 bg-surface p-4">
              <StyleRadar driverId={driver.id} sessionId={sessionId} driverCode={driver.code} />
            </View>
          )}

          {subView === "sectors" && (
            <View className="rounded-md border border-white/10 bg-surface p-4">
              <Text className="mb-3 text-sm font-semibold text-foreground">
                Sector Times vs. Team Average
              </Text>
              <SectorComparison sessionId={sessionId} driverId={driver.id} />
            </View>
          )}
        </View>
      </ScrollView>
    </View>
  )
}
