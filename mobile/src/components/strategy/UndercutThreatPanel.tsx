import { useMemo } from "react"
import { Text, View } from "react-native"
import { DriverChip } from "@/components/shared/DriverChip"
import { useSessionGaps } from "@/hooks/useSessionGaps"
import { useUndercut } from "@/hooks/useStrategy"
import type { DriverGap, UndercutThreatResponse } from "@/types"

// Web's colours for a net gain and a net loss.
const GAIN_COLOR = "#10B981"
const LOSS_COLOR = "#EF4444"
const BAR_TRACK_COLOR = "rgba(255, 255, 255, 0.1)"
const BAR_FILL_COLOR = "#fafafa"
const BAR_HEIGHT_PX = 6

// projected_gap_seconds is NOT a gap-to-car distance — it's the predicted
// net time gain/loss from executing the undercut now vs. staying out
// (backend/services/strategy_service.py). Copied from web, which spells out
// gain/loss rather than relying on a bare +/- sign.
function formatNetTimeDelta(value: number): { label: string; color: string } {
  if (value >= 0) return { label: `+${value.toFixed(3)}s net gain`, color: GAIN_COLOR }
  return { label: `${Math.abs(value).toFixed(3)}s net loss`, color: LOSS_COLOR }
}

interface Neighbors {
  aheadDriverId: string | null
  behindDriverId: string | null
}

// Copied from web: the cars immediately ahead and behind in track position,
// matching what GET .../undercut and alert_service.evaluate_threats assume.
function resolveNeighbors(gaps: DriverGap[], driverId: string | null): Neighbors {
  if (!driverId || gaps.length === 0) return { aheadDriverId: null, behindDriverId: null }
  const sorted = [...gaps].sort((a, b) => a.position - b.position)
  const index = sorted.findIndex((gap) => gap.driver_id === driverId)
  if (index === -1) return { aheadDriverId: null, behindDriverId: null }
  return {
    aheadDriverId: index > 0 ? sorted[index - 1].driver_id : null,
    behindDriverId: index < sorted.length - 1 ? sorted[index + 1].driver_id : null,
  }
}

function ProbabilityBar({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(1, value)) * 100
  return (
    <View style={{ height: BAR_HEIGHT_PX, borderRadius: BAR_HEIGHT_PX / 2, backgroundColor: BAR_TRACK_COLOR }}>
      <View
        style={{
          width: `${percent}%`,
          height: BAR_HEIGHT_PX,
          borderRadius: BAR_HEIGHT_PX / 2,
          backgroundColor: BAR_FILL_COLOR,
        }}
      />
    </View>
  )
}

interface ThreatRowProps {
  label: string
  otherDriverId: string | null
  data: UndercutThreatResponse | undefined
  isLoading: boolean
}

function ThreatRow({ label, otherDriverId, data, isLoading }: ThreatRowProps) {
  if (!otherDriverId) {
    return (
      <View className="rounded-md border border-white/10 p-2">
        <Text className="text-xs text-muted">{label}: no car in range.</Text>
      </View>
    )
  }

  const delta = data ? formatNetTimeDelta(data.projected_gap_seconds) : null
  return (
    <View className="gap-1.5 rounded-md border border-white/10 p-2">
      <View className="flex-row items-center justify-between">
        <Text className="text-xs font-medium text-muted">{label}</Text>
        <DriverChip driverId={otherDriverId} />
      </View>
      {isLoading || !data || !delta ? (
        <View className="h-4 w-full rounded-md bg-pill" />
      ) : (
        <>
          <ProbabilityBar value={data.probability_pit_now_gains_position} />
          <View className="flex-row items-center justify-between">
            <Text className="text-xs text-muted">
              {Math.round(data.probability_pit_now_gains_position * 100)}% gain probability
            </Text>
            <Text className="text-xs font-semibold" style={{ color: delta.color }}>
              {delta.label}
            </Text>
          </View>
          <Text className="text-[10px] text-muted">Predicted net time after undercut vs staying out</Text>
          <Text className="text-xs text-foreground">{data.recommended_action}</Text>
        </>
      )}
    </View>
  )
}

interface UndercutThreatPanelProps {
  sessionId: string | null
  driverId: string | null
}

// RN port of web/src/components/strategy/UndercutThreatPanel.tsx (2026-10-07,
// the Strategy tab's driver sheet): the undercut opportunity on the car
// ahead, and the threat from the car behind pitting now. Live rows only, from
// GET .../undercut. Web's replay rows (the stored prediction for the current
// lap, so a replay runs no ML on the web machine) come with the Day 6b mobile
// catch-up, once mobile can tell a replay is running.
export function UndercutThreatPanel({ sessionId, driverId }: UndercutThreatPanelProps) {
  const { data: gapsResponse } = useSessionGaps(sessionId)
  const gaps = useMemo(() => gapsResponse?.gaps ?? [], [gapsResponse])
  const { aheadDriverId, behindDriverId } = useMemo(() => resolveNeighbors(gaps, driverId), [gaps, driverId])

  const opportunity = useUndercut(sessionId, driverId, aheadDriverId)
  const threat = useUndercut(sessionId, behindDriverId, driverId)

  if (!sessionId || !driverId) {
    return <Text className="text-sm text-muted">No race session to show.</Text>
  }

  return (
    <View className="gap-2 rounded-md border border-white/10 bg-surface p-3">
      <Text className="text-sm font-semibold text-foreground">Undercut Threats</Text>
      <ThreatRow
        label="Opportunity — car ahead"
        otherDriverId={aheadDriverId}
        data={opportunity.data}
        isLoading={opportunity.isLoading}
      />
      <ThreatRow
        label="Threat — car behind"
        otherDriverId={behindDriverId}
        data={threat.data}
        isLoading={threat.isLoading}
      />
    </View>
  )
}
