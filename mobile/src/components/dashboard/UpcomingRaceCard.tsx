import { Text, View } from "react-native"
import { CircuitOutlineSvg } from "@/components/circuit/CircuitOutlineSvg"
import { useCircuitOutline } from "@/hooks/useCircuitOutline"
import { padCountdownUnit as pad, useCountdown } from "@/hooks/useCountdown"
import { useUpcomingRace } from "@/hooks/useUpcomingRace"

function CountdownTimer({ targetIso }: { targetIso: string | null }) {
  const countdown = useCountdown(targetIso)
  if (!countdown) return null
  return (
    <Text className="self-end font-mono text-xs text-muted">
      Starts in: {countdown.days}d {pad(countdown.hours)}h {pad(countdown.minutes)}m{" "}
      {pad(countdown.seconds)}s
    </Text>
  )
}

// RN port of web/src/components/dashboard/UpcomingRaceCard.tsx — static
// circuit outline (via the CircuitOutlineSvg port) + countdown, no live
// driver dots (that's the full CircuitMapPanel, ported in Checkpoint 6 onto
// the Live tab instead of Home).
export function UpcomingRaceCard() {
  const { data: upcomingRace, isLoading, isError } = useUpcomingRace()
  const { data: outline } = useCircuitOutline(upcomingRace?.circuit_id ?? null)

  if (isLoading) {
    return <View className="h-56 w-full rounded-lg bg-surface" />
  }

  if (isError || !upcomingRace) {
    return (
      <View className="h-56 w-full items-center justify-center rounded-lg border border-white/10 bg-surface">
        <Text className="text-sm text-muted">No upcoming race scheduled.</Text>
      </View>
    )
  }

  return (
    <View className="h-56 w-full overflow-hidden rounded-lg border border-white/10 bg-surface">
      <View className="absolute inset-0 items-center justify-center">
        <CircuitOutlineSvg outline={outline} />
      </View>
      <View className="flex-1 justify-between p-4">
        <View>
          <Text className="text-xs font-semibold uppercase tracking-wide text-muted">
            Upcoming Race
          </Text>
          <Text className="text-xl font-bold text-foreground">
            {upcomingRace.race_name ?? "Next Race"}
          </Text>
        </View>
        <CountdownTimer targetIso={upcomingRace.scheduled_start} />
      </View>
    </View>
  )
}
