import Ionicons from "@expo/vector-icons/Ionicons"
import { useMemo, useState } from "react"
import { ActivityIndicator, Pressable, Text, View } from "react-native"
import {
  useCuratedSessions,
  useReplayAvailable,
  useReplayStatus,
  useStartReplay,
  useStopReplay,
} from "@/hooks/useDemoReplay"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { getApiErrorMessage } from "@/utils/errors"
import * as haptics from "@/utils/haptics"
import type { CuratedSession } from "@/types"

interface ReplaySelectorPanelProps {
  // The session the Live tab shows: the running replay's while one runs
  // (useRaceSession), used to read the current lap from its gaps.
  sessionId: string | null
  // useRaceSession().isLive: a genuine live race, not a Demo Replay.
  isLive: boolean
}

// Disabled buttons dim through a plain style: NativeWind's disabled: variant
// is used nowhere in mobile, so it may not apply on the device.
const DISABLED_OPACITY = 0.4
// pb-3 as a plain style, for the same reason (no other mobile file uses it).
const PANEL_BOTTOM_PADDING = { paddingBottom: 12 }

interface Notice {
  text: string
  isError: boolean
}

// RN port of web/src/components/demo/ReplaySelectorPanel.tsx (Day 6b-mobile
// CP3, 2026-10-09), above the circuit map as on web's race page. Phone
// differences:
// - collapsed to one "Watch a Replay" row until tapped, so the three race
//   cards don't push the map down the screen; while a replay runs it is one
//   row with the lap and Stop;
// - web's toasts become a one-line notice in the panel, plus haptics.
// No navigation on start, unlike web: every race screen follows the running
// replay by itself (useRaceSession), once the status query refetches.
export function ReplaySelectorPanel({ sessionId, isLive }: ReplaySelectorPanelProps) {
  const { data: available } = useReplayAvailable()
  const { data: status } = useReplayStatus()
  const { data: curated } = useCuratedSessions()
  const startReplay = useStartReplay()
  const stopReplay = useStopReplay()
  const [expanded, setExpanded] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  const running = status?.running ?? false
  // As on web: the race's current lap is the furthest-along car's lap in the
  // gaps the replay publishes (same query as the tower, so no extra request).
  const { data: gaps } = useSessionGaps(running ? sessionId : null)
  const currentLap = useMemo(() => {
    if (!gaps || gaps.gaps.length === 0) return null
    return Math.max(...gaps.gaps.map((entry) => entry.lap_number))
  }, [gaps])

  // Hidden during a real live race, or whenever the backend reports the
  // feature unavailable (also a server-detected live race).
  if (isLive || available?.available !== true) return null

  const handleStart = (session: CuratedSession) => {
    haptics.confirm()
    setNotice(null)
    startReplay.mutate(session.session_id, {
      onSuccess: (result) => {
        haptics.success()
        setExpanded(false)
        setNotice({ text: `Replaying ${result.race_name}`, isError: false })
      },
      onError: (error) => {
        haptics.error()
        setNotice({ text: getApiErrorMessage(error, "Failed to start replay"), isError: true })
      },
    })
  }

  const handleStop = () => {
    haptics.confirm()
    setNotice(null)
    stopReplay.mutate(undefined, {
      onSuccess: () => {
        haptics.success()
        setNotice({ text: "Replay stopped", isError: false })
      },
      onError: (error) => {
        haptics.error()
        setNotice({ text: getApiErrorMessage(error, "Failed to stop replay"), isError: true })
      },
    })
  }

  const noticeText = notice ? (
    <Text className={`text-xs ${notice.isError ? "text-destructive" : "text-muted"}`}>{notice.text}</Text>
  ) : null

  if (running) {
    return (
      <View className="mx-3 mt-3 gap-1 rounded-md border border-white/10 bg-surface p-3">
        <View className="flex-row items-center justify-between gap-2">
          <View className="flex-1">
            <Text className="text-xs text-muted">Replaying</Text>
            <Text numberOfLines={1} className="text-sm font-semibold text-foreground">
              {status?.race_name ?? "Demo Replay"}
              {typeof status?.end_lap === "number" ? ` — Lap ${currentLap ?? "…"}/${status.end_lap}` : ""}
            </Text>
          </View>
          <Pressable
            onPress={handleStop}
            disabled={stopReplay.isPending}
            accessibilityRole="button"
            accessibilityLabel="Stop the replay"
            className="items-center rounded-md bg-destructive px-3 py-2"
            style={{ opacity: stopReplay.isPending ? DISABLED_OPACITY : 1 }}
          >
            {stopReplay.isPending ? (
              <ActivityIndicator size="small" color="#fafafa" />
            ) : (
              <Text className="text-sm font-semibold text-foreground">Stop</Text>
            )}
          </Pressable>
        </View>
        {noticeText}
      </View>
    )
  }

  return (
    <View className="mx-3 mt-3 rounded-md border border-white/10 bg-surface">
      <Pressable
        onPress={() => {
          haptics.selectionTick()
          setExpanded((value) => !value)
        }}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        className="flex-row items-center justify-between p-3"
      >
        <View className="flex-1">
          <Text className="text-sm font-semibold text-foreground">Watch a Replay</Text>
          {!expanded && (
            <Text className="text-xs text-muted">Play part of a past race as if it were live</Text>
          )}
        </View>
        <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={16} color="#9ca3af" />
      </Pressable>
      {expanded && (
        <View className="gap-2 px-3" style={PANEL_BOTTOM_PADDING}>
          {(curated?.sessions ?? []).map((session) => (
            <View key={session.session_id} className="gap-1 rounded-md border border-white/10 p-3">
              <View className="flex-row items-center justify-between gap-2">
                <View className="flex-1">
                  <Text className="text-sm font-semibold text-foreground">{session.race_name}</Text>
                  <Text className="text-xs text-muted">
                    Laps {session.start_lap}–{session.end_lap} · ~{session.estimated_duration_minutes} min
                  </Text>
                </View>
                <Pressable
                  onPress={() => handleStart(session)}
                  disabled={startReplay.isPending}
                  accessibilityRole="button"
                  accessibilityLabel={`Start the ${session.race_name} replay`}
                  className="rounded-md bg-foreground px-3 py-2"
                  style={{ opacity: startReplay.isPending ? DISABLED_OPACITY : 1 }}
                >
                  <Text className="text-sm font-semibold text-background">Start</Text>
                </Pressable>
              </View>
              <Text className="text-xs text-muted">{session.description}</Text>
            </View>
          ))}
        </View>
      )}
      {notice && <View className="px-3" style={PANEL_BOTTOM_PADDING}>{noticeText}</View>}
    </View>
  )
}
