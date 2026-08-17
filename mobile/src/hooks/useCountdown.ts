import { useEffect, useState } from "react"

export interface Countdown {
  days: number
  hours: number
  minutes: number
  seconds: number
}

// Shared by UpcomingRaceCard and CircuitMapPanel — same duplication
// rationale web accepted (see web's UpcomingRaceCard.tsx comment: "kept as
// a separate small copy... per the Day 29 scope") didn't apply once a
// second mobile consumer (CircuitMapPanel, Checkpoint 6) needed the exact
// same logic — extracted here instead of copied a third time.
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

export function padCountdownUnit(value: number): string {
  return value.toString().padStart(2, "0")
}
