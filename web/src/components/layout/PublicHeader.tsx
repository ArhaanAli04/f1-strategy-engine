// LandingPage's own header — deliberately not NavBar: NavBar renders inside
// AuthGuard (assumes a logged-in user, shows alerts/settings/logout) and
// this route is reachable without auth. Same h-12 hairline-bottom shell so
// the transition from "/" into the authenticated app doesn't jump.
//
// Wordmark only, no CTA — the page has exactly two CTA locations (the hero's
// left column and the closing CTA band), both of which already swap between
// Sign In/Create Account and Go to Dashboard based on auth state; a third
// copy in the header was redundant.
export function PublicHeader() {
  return (
    <header className="flex h-12 flex-shrink-0 items-center border-b px-4 sm:px-6">
      <span className="text-sm font-semibold">F1 Strategy Engine</span>
    </header>
  )
}
