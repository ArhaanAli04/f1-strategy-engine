import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { AuthGuard } from "@/components/auth/AuthGuard"
import { useIsAuthenticated } from "@/stores/authStore"

vi.mock("@/stores/authStore", () => ({ useIsAuthenticated: vi.fn() }))
// NavBar pulls in useAuth/useAlertStore, neither relevant to AuthGuard's own
// redirect-vs-render logic — stub it out rather than mock its whole dependency tree.
vi.mock("@/components/layout/NavBar", () => ({
  NavBar: () => <div>NavBar</div>,
}))

function renderGuardedRoute() {
  return render(
    <MemoryRouter initialEntries={["/protected"]}>
      <Routes>
        <Route path="/login" element={<div>Login Page</div>} />
        <Route element={<AuthGuard />}>
          <Route path="/protected" element={<div>Protected content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe("AuthGuard", () => {
  it("redirects to /login when authStore has no token", () => {
    vi.mocked(useIsAuthenticated).mockReturnValue(false)

    renderGuardedRoute()

    expect(screen.getByText("Login Page")).toBeInTheDocument()
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument()
  })

  it("renders children when a valid token is present in the store", () => {
    vi.mocked(useIsAuthenticated).mockReturnValue(true)

    renderGuardedRoute()

    expect(screen.getByText("Protected content")).toBeInTheDocument()
    expect(screen.queryByText("Login Page")).not.toBeInTheDocument()
  })
})
