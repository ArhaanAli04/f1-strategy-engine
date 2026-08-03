import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { UserResponse } from "@/types"

interface AuthTokens {
  accessToken: string
  refreshToken: string
  expiresAt: string
}

interface AuthState {
  user: UserResponse | null
  accessToken: string | null
  refreshToken: string | null
  expiresAt: string | null
  // LoginResponse carries no user field (see backend/apis/v1/auth.py) — login
  // is tokens-then-getMe, so tokens and user are set in two separate steps.
  setTokens: (tokens: AuthTokens) => void
  setAccessToken: (accessToken: string, expiresAt: string) => void
  setUser: (user: UserResponse | null) => void
  clearAuth: () => void
}

// Backend issues bearer tokens in the JSON response body (not Set-Cookie),
// so localStorage (via persist) is the only place to hold them client-side.
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      expiresAt: null,
      setTokens: (tokens) =>
        set({
          accessToken: tokens.accessToken,
          refreshToken: tokens.refreshToken,
          expiresAt: tokens.expiresAt,
        }),
      setAccessToken: (accessToken, expiresAt) => set({ accessToken, expiresAt }),
      setUser: (user) => set({ user }),
      clearAuth: () =>
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          expiresAt: null,
        }),
    }),
    { name: "f1-auth" },
  ),
)

// Derived, not stored — avoids a boolean field drifting out of sync with
// accessToken across the setTokens/setAccessToken/clearAuth actions above.
export function useIsAuthenticated(): boolean {
  return useAuthStore((state) => state.accessToken !== null)
}
