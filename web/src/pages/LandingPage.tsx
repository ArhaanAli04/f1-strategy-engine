import { Link } from "react-router-dom"
import {
  PitWindowTile,
  SectorHeatmapTile,
  SimulatorTile,
  TimingTowerTile,
  UndercutTile,
} from "@/components/landing/FeatureTiles"
import { LandingCircuitHero } from "@/components/landing/LandingCircuitHero"
import { PublicHeader } from "@/components/layout/PublicHeader"
import { Button } from "@/components/ui/button"
import { useIsAuthenticated } from "@/stores/authStore"
import { ROUTES } from "@/utils/constants"

// Public "/" route — no AuthGuard. Structure: hero (the one genuinely live
// element — circuit map + countdown, see LandingCircuitHero), a feature-tile
// grid styled like the in-app Strategy Wall (each tile mirrors a real
// component's grammar with labeled sample data, see FeatureTiles.tsx), and a
// closing CTA band. See CLAUDE.md's Day 39B notes for the design brief this
// followed.
export function LandingPage() {
  const isAuthenticated = useIsAuthenticated()

  return (
    <div className="flex h-screen flex-col overflow-y-auto bg-background">
      <PublicHeader />

      <LandingCircuitHero />

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6">
        <h2 className="text-xl font-semibold">What the engine does</h2>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Every panel below is a real surface in the app, shown with sample data — sign in to see
          it running against a live session.
        </p>

        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <PitWindowTile />
          <UndercutTile />
          <SimulatorTile />
          <TimingTowerTile />
          <SectorHeatmapTile />
        </div>
      </main>

      <footer className="border-t px-4 py-10 sm:px-6">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 text-center">
          <h2 className="text-2xl font-bold">
            {isAuthenticated ? "Back to the pit wall." : "Read the race like the pit wall does."}
          </h2>
          <div className="flex gap-2">
            {isAuthenticated ? (
              <Button asChild size="lg">
                <Link to={ROUTES.DASHBOARD}>Go to Dashboard</Link>
              </Button>
            ) : (
              <>
                <Button asChild size="lg">
                  <Link to={ROUTES.REGISTER}>Create Account</Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <Link to={ROUTES.LOGIN}>Sign In</Link>
                </Button>
              </>
            )}
          </div>
        </div>
      </footer>
    </div>
  )
}
