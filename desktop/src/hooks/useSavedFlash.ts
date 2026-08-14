import { useEffect, useRef, useState } from "react"

const SAVED_FLASH_MS = 2000

// Briefly flips a "just saved" flag after a successful mutation, for an
// inline button-label confirmation alongside (not replacing) the existing
// toast. Shared by ProfileForm (profile + password) and
// AlertSubscriptionsForm — three save buttons, one behavior.
export function useSavedFlash() {
  const [justSaved, setJustSaved] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  function flash() {
    setJustSaved(true)
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => setJustSaved(false), SAVED_FLASH_MS)
  }

  return [justSaved, flash] as const
}
