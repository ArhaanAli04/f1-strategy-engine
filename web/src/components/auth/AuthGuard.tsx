import { Navigate, Outlet, useLocation } from "react-router-dom"
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

  return <Outlet />
}
