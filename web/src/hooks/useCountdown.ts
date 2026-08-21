import { useEffect, useState } from "react"

export interface Countdown {
  days: number
  hours: number
  minutes: number
  seconds: number
}

// Shared by CircuitMapPanel, UpcomingRaceCard, and LandingCircuitHero — all
// three tick down to a race/session's scheduled_start the same way.
export function useCountdown(targetIso: string | null): Countdown | null {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!targetIso) return
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [targetIso])

  if (!targetIso) return null
  const diffMs = new Date(targetIso).getTime() - now
  if (diffMs <= 0) return { days: 0, hours: 0, minutes: 0, seconds: 0 }
  const totalSeconds = Math.floor(diffMs / 1000)
  return {
    days: Math.floor(totalSeconds / 86400),
    hours: Math.floor((totalSeconds % 86400) / 3600),
    minutes: Math.floor((totalSeconds % 3600) / 60),
    seconds: totalSeconds % 60,
  }
}

export function padCountdownValue(value: number): string {
  return value.toString().padStart(2, "0")
}
