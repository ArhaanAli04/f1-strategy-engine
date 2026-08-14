import { useState } from "react"
import { Info, X } from "lucide-react"

const DISMISSED_KEY_PREFIX = "f1:historical-banner-dismissed:"

interface HistoricalDataBannerProps {
  // Dismissal is remembered per session id — a different session (or the
  // same one going live later) shows the banner again.
  sessionId: string
}

// Informational, not a warning — dark blue/muted, not the red/destructive
// tones used for real errors elsewhere in the app. Unlike web's version,
// this has no race name/date to show: there's no backend endpoint that
// resolves race info from a bare session_id (every race/session route
// needs race_id first), and raceContextStore.sessionId is a raw UUID the
// user typed in, not something auto-resolved from a known race — see
// CLAUDE.md's Desktop Sync Protocol.
export function HistoricalDataBanner({ sessionId }: HistoricalDataBannerProps) {
  const storageKey = `${DISMISSED_KEY_PREFIX}${sessionId}`
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(storageKey) === "1")

  if (dismissed) return null

  function handleDismiss() {
    localStorage.setItem(storageKey, "1")
    setDismissed(true)
  }

  return (
    <div className="flex items-center justify-between gap-3 border-b border-blue-900/40 bg-blue-950/40 px-4 py-2 text-sm text-blue-200">
      <div className="flex items-center gap-2">
        <Info className="h-4 w-4 flex-shrink-0" />
        <span>No live race session active — showing data from the last completed race</span>
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
