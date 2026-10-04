import { useState } from "react"
import { Info, X } from "lucide-react"

const DISMISSED_KEY_PREFIX = "f1:historical-banner-dismissed:"

interface HistoricalDataBannerProps {
  // Dismissal is remembered per session id — a different fallback session
  // (or a real live one later) shows the banner again.
  sessionId: string
  raceName?: string | null
  raceDate?: string | null
}

function formatRaceDate(raceDate: string): string {
  return new Date(raceDate).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })
}

// Informational, not a warning — dark blue/muted, not the red/destructive
// tones used for real errors elsewhere in the app.
export function HistoricalDataBanner({ sessionId, raceName, raceDate }: HistoricalDataBannerProps) {
  const storageKey = `${DISMISSED_KEY_PREFIX}${sessionId}`
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(storageKey) === "1")

  if (dismissed) return null

  const message =
    raceName && raceDate
      ? `No live race session active — showing data from the last completed race: ${raceName} (${formatRaceDate(raceDate)})`
      : "No live race session active — showing data from the last completed race"

  function handleDismiss() {
    localStorage.setItem(storageKey, "1")
    setDismissed(true)
  }

  return (
    <div className="flex items-center justify-between gap-3 border-b border-blue-900/40 bg-blue-950/40 px-4 py-2 text-sm text-blue-200">
      <div className="flex items-center gap-2">
        <Info className="h-4 w-4 flex-shrink-0" />
        <span>{message}</span>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="flex-shrink-0 text-blue-300 hover:text-blue-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
