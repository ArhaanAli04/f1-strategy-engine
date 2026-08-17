import { Navigate, Outlet, useLocation } from "react-router-dom"
import { NavBar } from "@/components/layout/NavBar"
import { useIsAuthenticated } from "@/stores/authStore"
import { ROUTES } from "@/utils/constants"

// Route-layout guard (React Router v6's idiomatic pattern for gating a group
// of nested routes) rather than a literal prop-wrapping HOC — used as a
// parent <Route element={<AuthGuard />}> wrapping protected child routes.
export function AuthGuard() {
  const isAuthenticated = useIsAuthenticated()
  const location = useLocation()

  if (!isAuthenticated) {
    return <Navigate to={ROUTES.LOGIN} state={{ from: location }} replace />
  }

  // flex-col + NavBar's fixed height means child routes fill the *remaining*
  // height (h-full), not a fresh 100vh (h-screen) — see RacePage.tsx, the
  // one existing page that assumed full-viewport height.
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <NavBar />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  )
}
