import { Ionicons } from "@expo/vector-icons"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { router } from "expo-router"
import { useEffect, useMemo, useRef, useState } from "react"
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, SectionList, Text, View } from "react-native"
import ReanimatedSwipeable, { type SwipeableMethods } from "react-native-gesture-handler/ReanimatedSwipeable"
import Animated, {
  Extrapolation,
  FadeIn,
  FadeInUp,
  FadeOut,
  FadeOutUp,
  LinearTransition,
  type SharedValue,
  ZoomIn,
  ZoomOut,
  interpolate,
  interpolateColor,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from "react-native-reanimated"
import * as alertsApi from "@/api/alerts"
import { DriverChip } from "@/components/shared/DriverChip"
import { OfflineBanner } from "@/components/shared/OfflineBanner"
import { raceBySessionQueryOptions } from "@/hooks/useRaceBySession"
import { useAlertStore } from "@/stores/alertStore"
import { useSessionStore } from "@/stores/sessionStore"
import { AlertType, type AlertResponse } from "@/types"
import { ROUTES } from "@/utils/constants"
import * as haptics from "@/utils/haptics"

const ALERTS_QUERY_KEY = ["alerts", "list"] as const

// destructive (#ef4444) at 20% opacity.
const FAILURE_BANNER_BACKGROUND = "rgba(239, 68, 68, 0.2)"

interface AlertTypeStyle {
  title: string
  icon: keyof typeof Ionicons.glyphMap
  color: string
}

// One title, icon and colour per alert type, so types can be told apart at a
// glance. Colours are set as styles: a NativeWind class no other file uses
// may not be generated (the Live tower's GAP/TYRE spacing, 2026-10-07).
const ALERT_TYPE_STYLES: Record<string, AlertTypeStyle> = {
  [AlertType.UNDERCUT_THREAT]: { title: "Undercut threat", icon: "trending-down-outline", color: "#f97316" },
  [AlertType.PIT_WINDOW_OPEN]: { title: "Pit window open", icon: "timer-outline", color: "#eab308" },
  [AlertType.SAFETY_CAR_PROBABILITY]: { title: "Safety car likely", icon: "warning-outline", color: "#f59e0b" },
  [AlertType.FASTEST_LAP_THREAT]: { title: "Fastest lap threat", icon: "flash-outline", color: "#a855f7" },
  [AlertType.COMPETITOR_PITTED]: { title: "Competitor pitted", icon: "people-outline", color: "#38bdf8" },
}
const FALLBACK_TYPE_STYLE: AlertTypeStyle = { title: "Alert", icon: "flag-outline", color: "#9ca3af" }

const TYPE_BADGE_SIZE_PX = 32
const UNREAD_EDGE_WIDTH_PX = 3

// Hex colour at 15% opacity, for the badge behind each type's icon.
function badgeBackground(hex: string): string {
  const value = Number.parseInt(hex.slice(1), 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, 0.15)`
}

// "Just now", "12 min ago", "3 h ago", then "Yesterday 14:05" and "6 Oct 14:05".
// Recomputed whenever the list re-renders (a refresh or a pull), not on a timer.
function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso)
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60_000)
  if (minutes < 1) return "Just now"
  if (minutes < 60) return `${minutes} min ago`
  const time = then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  if (minutes < 24 * 60 && then.getDate() === now.getDate()) return `${Math.floor(minutes / 60)} h ago`
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (then.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`
  return `${then.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${time}`
}

// Motion. Animated views take plain styles, not NativeWind classes: a class
// no other file uses may not be generated (the Live tower's GAP/TYRE spacing,
// 2026-10-07), and Reanimated's Animated.View is not a NativeWind component.
const SWIPE_ACTION_WIDTH_PX = 88
// A release past 60% of the action's width marks the alert read.
const SWIPE_THRESHOLD_PX = SWIPE_ACTION_WIDTH_PX * 0.6
const SWIPE_FRICTION = 1.5
const PRESSED_SCALE = 0.98
const LONG_PRESS_DELAY_MS = 350
const READ_OPACITY = 0.6
const READ_FADE_MS = 250
const ROW_SHIFT = LinearTransition.duration(200)
const UNREAD_ROW_BACKGROUND = "#141414"
const READ_ROW_BACKGROUND = "#0a0a0a"
const ROW_BORDER_COLOR = "rgba(255, 255, 255, 0.1)"

interface MarkReadActionProps {
  progress: SharedValue<number>
}

// The panel behind a row swiped left. Its icon and label grow and fade in
// with the drag, so it shows how close the release is to marking read.
function MarkReadAction({ progress }: MarkReadActionProps) {
  const contentStyle = useAnimatedStyle(() => ({
    opacity: interpolate(progress.value, [0, 0.6, 1], [0, 0.5, 1], Extrapolation.CLAMP),
    transform: [{ scale: interpolate(progress.value, [0, 1], [0.6, 1], Extrapolation.CLAMP) }],
  }))
  return (
    <View
      style={{ width: SWIPE_ACTION_WIDTH_PX, backgroundColor: "#fafafa" }}
      className="items-center justify-center"
    >
      <Animated.View style={[{ alignItems: "center" }, contentStyle]}>
        <Ionicons name="checkmark-outline" size={20} color="#0a0a0a" />
        <Text className="text-[10px] font-semibold text-background">Read</Text>
      </Animated.View>
    </View>
  )
}

interface SelectionCheckboxProps {
  checked: boolean
  disabled: boolean
}

// Grows in when selection mode starts; the tick pops in with a small spring
// (keyed on checked, so each change plays its own entering animation).
function SelectionCheckbox({ checked, disabled }: SelectionCheckboxProps) {
  return (
    <Animated.View entering={ZoomIn.duration(180)} exiting={ZoomOut.duration(140)}>
      {disabled ? (
        <Ionicons name="square-outline" size={20} color="#3f3f46" />
      ) : (
        <Animated.View
          key={checked ? "checked" : "unchecked"}
          entering={checked ? ZoomIn.springify().damping(12) : FadeIn.duration(120)}
        >
          <Ionicons
            name={checked ? "checkbox" : "square-outline"}
            size={20}
            color={checked ? "#fafafa" : "#9ca3af"}
          />
        </Animated.View>
      )}
    </Animated.View>
  )
}

interface AlertRowProps {
  alert: AlertResponse
  onMarkRead: (alertId: string) => void
  onOpen: (alert: AlertResponse) => void
  selectionMode: boolean
  selected: boolean
  onLongPress: (alertId: string) => void
  onToggle: (alertId: string) => void
}

// A card per alert: a coloured badge for its type, the type's title, the
// driver and how long ago, then the message. Unread alerts have a coloured
// left edge and a bold title; read ones are dimmed, and the change fades
// rather than switching. A tap opens the Live tab on the alert's driver
// (onOpen). Swiping left marks an unread alert read (web's "dismiss"
// semantics: it stays in the list): the row springs back closed, then fades
// to read. A long press on an unread alert shrinks the row slightly, then
// starts selection mode; in it a tap ticks the alert, the content slides over
// for the checkbox, and swiping is off so the two don't clash. Read alerts
// can't be selected (marking them read again does nothing), so their checkbox
// is greyed out. The row always sits in the swipeable (disabled when it can't
// swipe), so becoming read doesn't swap the component and snap the row.
function AlertRow({ alert, onMarkRead, onOpen, selectionMode, selected, onLongPress, onToggle }: AlertRowProps) {
  const typeStyle = ALERT_TYPE_STYLES[alert.alert_type] ?? FALLBACK_TYPE_STYLE
  const isUnread = alert.read_at === null
  const swipeRef = useRef<SwipeableMethods>(null)
  const readProgress = useSharedValue(isUnread ? 0 : 1)
  const pressScale = useSharedValue(1)

  useEffect(() => {
    readProgress.value = withTiming(isUnread ? 0 : 1, { duration: READ_FADE_MS })
  }, [isUnread, readProgress])

  useEffect(() => {
    if (selectionMode) swipeRef.current?.close()
  }, [selectionMode])

  const rowStyle = useAnimatedStyle(() => ({
    opacity: interpolate(readProgress.value, [0, 1], [1, READ_OPACITY]),
    backgroundColor: interpolateColor(readProgress.value, [0, 1], [UNREAD_ROW_BACKGROUND, READ_ROW_BACKGROUND]),
    transform: [{ scale: pressScale.value }],
  }))
  const edgeStyle = useAnimatedStyle(() => ({ opacity: 1 - readProgress.value }))

  function handlePress() {
    if (selectionMode) {
      if (isUnread) onToggle(alert.id)
      return
    }
    onOpen(alert)
  }

  // Always passed to the Pressable, even when it does nothing. React Native
  // only skips the tap on release if onLongPress is still set at that moment;
  // removing it once selection mode started made the release tap the alert
  // again, unticking it and ending selection mode straight away.
  function handleLongPress() {
    if (isUnread && !selectionMode) onLongPress(alert.id)
  }

  function handlePressIn() {
    if (isUnread && !selectionMode) pressScale.value = withTiming(PRESSED_SCALE, { duration: LONG_PRESS_DELAY_MS })
  }

  function handlePressOut() {
    pressScale.value = withSpring(1, { damping: 15, stiffness: 220 })
  }

  return (
    <ReanimatedSwipeable
      ref={swipeRef}
      enabled={isUnread && !selectionMode}
      friction={SWIPE_FRICTION}
      rightThreshold={SWIPE_THRESHOLD_PX}
      overshootRight={false}
      renderRightActions={(progress) => <MarkReadAction progress={progress} />}
      onSwipeableWillOpen={() => {
        haptics.threshold()
      }}
      onSwipeableOpen={() => {
        swipeRef.current?.close()
        onMarkRead(alert.id)
      }}
    >
      <Pressable
        onLongPress={handleLongPress}
        delayLongPress={LONG_PRESS_DELAY_MS}
        onPress={handlePress}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        disabled={selectionMode && !isUnread}
        accessibilityRole={selectionMode ? "checkbox" : "button"}
        accessibilityState={selectionMode ? { checked: selected, disabled: !isUnread } : undefined}
        accessibilityHint={selectionMode ? undefined : "Opens the Live tab on this driver"}
      >
        <Animated.View
          style={[
            {
              flexDirection: "row",
              alignItems: "flex-start",
              gap: 12,
              padding: 12,
              borderBottomWidth: 1,
              borderBottomColor: ROW_BORDER_COLOR,
            },
            rowStyle,
          ]}
        >
          <Animated.View
            style={[
              {
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: UNREAD_EDGE_WIDTH_PX,
                backgroundColor: typeStyle.color,
              },
              edgeStyle,
            ]}
          />
          {selectionMode && <SelectionCheckbox checked={selected} disabled={!isUnread} />}
          <Animated.View
            layout={ROW_SHIFT}
            style={{
              width: TYPE_BADGE_SIZE_PX,
              height: TYPE_BADGE_SIZE_PX,
              borderRadius: TYPE_BADGE_SIZE_PX / 2,
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: badgeBackground(typeStyle.color),
            }}
          >
            <Ionicons name={typeStyle.icon} size={16} color={typeStyle.color} />
          </Animated.View>
          <Animated.View layout={ROW_SHIFT} style={{ flex: 1, gap: 4 }}>
            <View className="flex-row items-center justify-between gap-2">
              <Text
                numberOfLines={1}
                className={`flex-1 text-sm text-foreground ${isUnread ? "font-bold" : "font-semibold"}`}
              >
                {typeStyle.title}
              </Text>
              <Text className="text-xs text-muted">{formatRelativeTime(alert.triggered_at)}</Text>
            </View>
            {alert.driver_id && (
              <View className="flex-row">
                <DriverChip driverId={alert.driver_id} />
              </View>
            )}
            <Text className="text-sm text-foreground">{alert.message}</Text>
          </Animated.View>
          {!selectionMode && alert.driver_id && (
            <Animated.View entering={FadeIn.duration(150)} exiting={FadeOut.duration(100)} style={{ alignSelf: "center" }}>
              <Ionicons name="chevron-forward" size={16} color="#9ca3af" />
            </Animated.View>
          )}
        </Animated.View>
      </Pressable>
    </ReanimatedSwipeable>
  )
}

interface SelectionBarProps {
  selectedCount: number
  allSelected: boolean
  busy: boolean
  onCancel: () => void
  onToggleAll: () => void
  onMarkRead: () => void
}

function SelectionBar({ selectedCount, allSelected, busy, onCancel, onToggleAll, onMarkRead }: SelectionBarProps) {
  return (
    // Fills the overlay that slides it over the chips; the overlay draws the
    // background and bottom border.
    <View className="flex-1 flex-row items-center gap-3 px-3">
      <Pressable onPress={onCancel} hitSlop={10} accessibilityRole="button" accessibilityLabel="Cancel selection">
        <Ionicons name="close" size={20} color="#fafafa" />
      </Pressable>
      <Text className="flex-1 text-sm font-semibold text-foreground">{selectedCount} selected</Text>
      <Pressable onPress={onToggleAll} hitSlop={8} accessibilityRole="button" disabled={busy}>
        <Text className="text-xs font-medium text-foreground">
          {allSelected ? "Deselect all" : "Select all unread"}
        </Text>
      </Pressable>
      <Pressable
        onPress={onMarkRead}
        disabled={busy || selectedCount === 0}
        accessibilityRole="button"
        className={`flex-row items-center gap-1 rounded-md bg-foreground px-3 py-1.5 ${busy || selectedCount === 0 ? "opacity-50" : ""}`}
      >
        {busy ? (
          <ActivityIndicator size="small" color="#0a0a0a" />
        ) : (
          <Ionicons name="checkmark-done-outline" size={14} color="#0a0a0a" />
        )}
        <Text className="text-xs font-semibold text-background">Mark as read</Text>
      </Pressable>
    </View>
  )
}

// "all", "unread", or one alert type.
type AlertFilter = string

interface FilterChip {
  key: AlertFilter
  label: string
}

interface FilterChipsProps {
  chips: FilterChip[]
  active: AlertFilter
  onChange: (filter: AlertFilter) => void
}

function FilterChips({ chips, active, onChange }: FilterChipsProps) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      // flexGrow 0 keeps the chip row its own height; as a style, because the
      // flex-grow-0 class is used nowhere else and NativeWind may not
      // generate it (see FAILURE_BANNER_BACKGROUND).
      style={{ flexGrow: 0 }}
      className="border-b border-white/10"
      contentContainerClassName="gap-2 px-3 py-2"
    >
      {chips.map(({ key, label }) => {
        const selected = key === active
        return (
          <Pressable
            key={key}
            onPress={() => onChange(key)}
            accessibilityRole="button"
            accessibilityState={{ selected }}
            className={`rounded-full border px-3 py-1.5 ${selected ? "border-foreground bg-foreground" : "border-white/10 bg-surface"}`}
          >
            <Text className={`text-xs font-medium ${selected ? "text-background" : "text-foreground"}`}>
              {label}
            </Text>
          </Pressable>
        )
      })}
    </ScrollView>
  )
}

interface EmptyStateProps {
  icon: keyof typeof Ionicons.glyphMap
  title: string
  body: string
  actionLabel: string
  onAction: () => void
}

function EmptyState({ icon, title, body, actionLabel, onAction }: EmptyStateProps) {
  return (
    <View className="flex-1 items-center justify-center gap-3 p-6">
      <Ionicons name={icon} size={36} color="#9ca3af" />
      <Text className="text-base font-semibold text-foreground">{title}</Text>
      <Text className="text-center text-sm text-muted">{body}</Text>
      <Pressable onPress={onAction} accessibilityRole="button" className="rounded-md bg-foreground px-3 py-2">
        <Text className="text-xs font-semibold text-background">{actionLabel}</Text>
      </Pressable>
    </View>
  )
}

interface AlertSection {
  sessionId: string
  title: string
  data: AlertResponse[]
}

function formatRaceDate(isoDate: string): string {
  return new Date(isoDate).toLocaleDateString(undefined, { day: "numeric", month: "short" })
}

// RN port of web/src/pages/AlertsPage.tsx — swipe-to-mark-read (via
// react-native-gesture-handler's ReanimatedSwipeable) instead of web's tap-to-mark-
// read button. Populates alertStore exactly like web (source of truth for
// the Alerts tab's unread badge, wired in Checkpoint 3).
// Multi-select (mobile only, 2026-10-07): long press an unread alert to tick
// alerts and mark them read together. There is no bulk endpoint, so the app
// sends one PUT /alerts/{id}/read per alert, all at once; any that fail stay
// unread and are counted in a message.
// Grouping and filters (mobile only, 2026-10-07): alerts are grouped by the
// race they came from, newest race first, under a header that sticks while
// its alerts scroll; filter chips (All, Unread, and each type present) narrow
// the list, and "Select all unread" only takes the alerts the filter shows.
export default function AlertsScreen() {
  const setAlerts = useAlertStore((state) => state.setAlerts)
  const markReadInStore = useAlertStore((state) => state.markRead)
  const alerts = useAlertStore((state) => state.alerts)
  const queryClient = useQueryClient()
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkFailedCount, setBulkFailedCount] = useState(0)
  const [filter, setFilter] = useState<AlertFilter>("all")

  const setSelectedDriver = useSessionStore((state) => state.setSelectedDriver)

  // The chip row fades out under the selection bar and back in after.
  const selectionProgress = useSharedValue(0)
  useEffect(() => {
    selectionProgress.value = withTiming(selectionMode ? 1 : 0, { duration: 200 })
  }, [selectionMode, selectionProgress])
  const chipsStyle = useAnimatedStyle(() => ({ opacity: 1 - selectionProgress.value }))

  const { data, dataUpdatedAt, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ALERTS_QUERY_KEY,
    queryFn: () => alertsApi.getAlerts(),
  })

  useEffect(() => {
    if (data) setAlerts(data)
  }, [data, setAlerts])

  const markReadMutation = useMutation({
    mutationFn: (alertId: string) => alertsApi.markAlertRead(alertId),
    onSuccess: (_updated, alertId) => {
      markReadInStore(alertId)
      queryClient.invalidateQueries({ queryKey: ALERTS_QUERY_KEY })
    },
  })

  // Distinct sessions in first-seen order. Alerts arrive newest first, so
  // this is also newest race first.
  const sessionIds = useMemo(() => {
    const seen: string[] = []
    for (const alert of alerts) if (!seen.includes(alert.session_id)) seen.push(alert.session_id)
    return seen
  }, [alerts])

  const raceQueries = useQueries({
    queries: sessionIds.map((sessionId) => raceBySessionQueryOptions(sessionId)),
  })

  const sectionTitles = useMemo(() => {
    const titles = new Map<string, string>()
    sessionIds.forEach((sessionId, index) => {
      const race = raceQueries[index]?.data
      titles.set(
        sessionId,
        race ? `${race.event_name ?? `Round ${race.round_number}`} · ${formatRaceDate(race.race_date)}` : "Race",
      )
    })
    return titles
    // raceQueries is a new array every render (useQueries); sessionIds is
    // the real change signal, raceQueries is read for its current data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionIds, raceQueries])

  // Only the types the user actually has get a chip.
  const filterChips = useMemo<FilterChip[]>(() => {
    const unreadCount = alerts.filter((alert) => alert.read_at === null).length
    const types: string[] = []
    for (const alert of alerts) if (!types.includes(alert.alert_type)) types.push(alert.alert_type)
    return [
      { key: "all", label: "All" },
      { key: "unread", label: `Unread (${unreadCount})` },
      ...types.map((type) => ({ key: type, label: (ALERT_TYPE_STYLES[type] ?? FALLBACK_TYPE_STYLE).title })),
    ]
  }, [alerts])

  // A type filter whose last alert disappeared falls back to All.
  const activeFilter = filterChips.some((chip) => chip.key === filter) ? filter : "all"

  const visibleAlerts = useMemo(() => {
    if (activeFilter === "all") return alerts
    if (activeFilter === "unread") return alerts.filter((alert) => alert.read_at === null)
    return alerts.filter((alert) => alert.alert_type === activeFilter)
  }, [alerts, activeFilter])

  const sections = useMemo<AlertSection[]>(() => {
    const bySession = new Map<string, AlertResponse[]>()
    for (const alert of visibleAlerts) {
      const list = bySession.get(alert.session_id) ?? []
      list.push(alert)
      bySession.set(alert.session_id, list)
    }
    return sessionIds
      .filter((sessionId) => bySession.has(sessionId))
      .map((sessionId) => ({
        sessionId,
        title: sectionTitles.get(sessionId) ?? "Race",
        data: bySession.get(sessionId) ?? [],
      }))
  }, [visibleAlerts, sessionIds, sectionTitles])

  const visibleUnreadIds = useMemo(
    () => visibleAlerts.filter((alert) => alert.read_at === null).map((alert) => alert.id),
    [visibleAlerts],
  )

  // An alert read elsewhere (another device, a refetch) drops out of the
  // selection rather than being sent again.
  const effectiveSelection = useMemo(() => {
    const unread = new Set(alerts.filter((alert) => alert.read_at === null).map((alert) => alert.id))
    return [...selectedIds].filter((id) => unread.has(id))
  }, [alerts, selectedIds])
  const allSelected =
    visibleUnreadIds.length > 0 && visibleUnreadIds.every((id) => selectedIds.has(id))

  // Opening an alert marks it read, as notification lists do, then shows its
  // driver on the Live tab (map highlight, Lap Times) through the selection
  // the Live tab already follows. An alert with no driver only marks read.
  function openAlert(alert: AlertResponse) {
    if (alert.read_at === null) markReadMutation.mutate(alert.id)
    if (alert.driver_id) {
      setSelectedDriver(alert.driver_id)
      router.navigate(ROUTES.LIVE)
    }
  }

  function exitSelection() {
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  function startSelection(alertId: string) {
    haptics.confirm()
    setBulkFailedCount(0)
    setSelectionMode(true)
    setSelectedIds(new Set([alertId]))
  }

  function toggleSelected(alertId: string) {
    haptics.selectionTick()
    setSelectedIds((previous) => {
      const next = new Set(previous)
      if (next.has(alertId)) next.delete(alertId)
      else next.add(alertId)
      if (next.size === 0) setSelectionMode(false)
      return next
    })
  }

  function toggleAll() {
    haptics.selectionTick()
    if (allSelected) exitSelection()
    else setSelectedIds(new Set(visibleUnreadIds))
  }

  async function markSelectedRead() {
    const ids = effectiveSelection
    if (ids.length === 0) return
    setBulkBusy(true)
    const results = await Promise.allSettled(ids.map((id) => alertsApi.markAlertRead(id)))
    let failed = 0
    results.forEach((result, index) => {
      if (result.status === "fulfilled") markReadInStore(ids[index])
      else failed += 1
    })
    if (failed > 0) haptics.warning()
    else haptics.success()
    setBulkBusy(false)
    setBulkFailedCount(failed)
    exitSelection()
    queryClient.invalidateQueries({ queryKey: ALERTS_QUERY_KEY })
  }

  function changeFilter(next: AlertFilter) {
    if (next !== activeFilter) haptics.selectionTick()
    exitSelection()
    setFilter(next)
  }

  if (isLoading) {
    return (
      <View className="flex-1 bg-background">
        <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
      </View>
    )
  }

  if (alerts.length === 0) {
    return (
      <View className="flex-1 bg-background">
        <OfflineBanner dataUpdatedAt={dataUpdatedAt} />
        <EmptyState
          icon="notifications-outline"
          title="No alerts yet"
          body="You'll get alerts like undercut threats for the drivers you follow, during a live race or a Demo Replay."
          actionLabel="Choose alerts in Settings"
          onAction={() => router.push(ROUTES.SETTINGS)}
        />
      </View>
    )
  }

  const activeChipLabel = filterChips.find((chip) => chip.key === activeFilter)?.label ?? ""
  const filteredEmptyTitle =
    activeFilter === "unread" ? "No unread alerts" : `No ${activeChipLabel.toLowerCase()} alerts`

  return (
    <View className="flex-1 bg-background">
      {/* The chips stay mounted and the selection bar slides down over them,
          so the list below never jumps when selection mode starts or ends. */}
      <View>
        <Animated.View style={chipsStyle}>
          <FilterChips chips={filterChips} active={activeFilter} onChange={changeFilter} />
        </Animated.View>
        {selectionMode && (
          <Animated.View
            entering={FadeInUp.duration(200)}
            exiting={FadeOutUp.duration(160)}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: READ_ROW_BACKGROUND,
              borderBottomWidth: 1,
              borderBottomColor: ROW_BORDER_COLOR,
            }}
          >
            <SelectionBar
              selectedCount={effectiveSelection.length}
              allSelected={allSelected}
              busy={bulkBusy}
              onCancel={exitSelection}
              onToggleAll={toggleAll}
              onMarkRead={markSelectedRead}
            />
          </Animated.View>
        )}
      </View>
      {bulkFailedCount > 0 && (
        // A style, not bg-destructive/20: a class no other file uses may not be
        // generated by NativeWind (the tower's GAP/TYRE spacing, 2026-10-07).
        <View
          style={{ backgroundColor: FAILURE_BANNER_BACKGROUND }}
          className="flex-row items-center justify-between gap-3 px-3 py-2"
        >
          <Text className="flex-1 text-xs text-foreground">
            {bulkFailedCount === 1
              ? "1 alert couldn't be marked read. It's still unread."
              : `${bulkFailedCount} alerts couldn't be marked read. They're still unread.`}
          </Text>
          <Pressable onPress={() => setBulkFailedCount(0)} hitSlop={8} accessibilityLabel="Dismiss">
            <Text className="text-xs text-muted">Dismiss</Text>
          </Pressable>
        </View>
      )}
      {/* Keyed on the filter, so a new filter fades its list in instead of
          swapping it (and starts at the top). */}
      <Animated.View key={activeFilter} entering={FadeIn.duration(180)} style={{ flex: 1 }}>
      <SectionList
        className="flex-1"
        sections={sections}
        keyExtractor={(alert) => alert.id}
        extraData={{ selectionMode, selectedIds }}
        stickySectionHeadersEnabled
        contentContainerStyle={sections.length === 0 ? { flexGrow: 1 } : undefined}
        ListHeaderComponent={<OfflineBanner dataUpdatedAt={dataUpdatedAt} />}
        ListEmptyComponent={
          <EmptyState
            icon="funnel-outline"
            title={filteredEmptyTitle}
            body="Nothing matches this filter right now."
            actionLabel="Show all alerts"
            onAction={() => changeFilter("all")}
          />
        }
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor="#fafafa" />
        }
        renderSectionHeader={({ section }) => (
          <View className="border-b border-white/10 bg-background px-3 py-2">
            <Text className="text-xs font-semibold uppercase tracking-wide text-muted">{section.title}</Text>
          </View>
        )}
        renderItem={({ item: alert }) => (
          <AlertRow
            alert={alert}
            onMarkRead={markReadMutation.mutate}
            onOpen={openAlert}
            selectionMode={selectionMode}
            selected={selectedIds.has(alert.id)}
            onLongPress={startSelection}
            onToggle={toggleSelected}
          />
        )}
      />
      </Animated.View>
    </View>
  )
}
