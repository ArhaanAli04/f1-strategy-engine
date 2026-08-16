import * as SecureStore from "expo-secure-store"
import { create } from "zustand"
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware"
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
  // True once persist has finished its async SecureStore read on launch —
  // api/client.ts's request interceptor reads accessToken synchronously from
  // this store, so the root layout must gate rendering on this flag first
  // (see app/_layout.tsx) rather than the interceptor awaiting SecureStore
  // itself on every request.
  hasHydrated: boolean
  setTokens: (tokens: AuthTokens) => void
  setAccessToken: (accessToken: string, expiresAt: string) => void
  setUser: (user: UserResponse | null) => void
  clearAuth: () => void
}

// expo-secure-store's getItemAsync/setItemAsync/deleteItemAsync are async —
// zustand's persist middleware handles that natively via this StateStorage
// adapter (rehydration on launch, writes on every set() call are
// fire-and-forget from the store's perspective).
const secureStoreAdapter: StateStorage = {
  getItem: async (name) => (await SecureStore.getItemAsync(name)) ?? null,
  setItem: async (name, value) => {
    await SecureStore.setItemAsync(name, value)
  },
  removeItem: async (name) => {
    await SecureStore.deleteItemAsync(name)
  },
}

// Only the token slice is persisted (partialize below) — SecureStore caps
// each item at ~2048 bytes on iOS, and UserResponse has no fixed size bound.
// `user` stays in-memory only and is refetched via GET /auth/me on launch,
// same as web's post-login getMe() call.
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      expiresAt: null,
      hasHydrated: false,
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
    {
      name: "f1-auth",
      storage: createJSONStorage(() => secureStoreAdapter),
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        expiresAt: state.expiresAt,
      }),
      // Runs after rehydration whether it succeeded or errored — either way
      // the gate must unblock, and erroring should fail open to "logged
      // out" rather than hang the app waiting for hydration forever.
      onRehydrateStorage: () => () => {
        useAuthStore.setState({ hasHydrated: true })
      },
    },
  ),
)

// Derived, not stored — avoids a boolean field drifting out of sync with
// accessToken across the setTokens/setAccessToken/clearAuth actions above.
export function useIsAuthenticated(): boolean {
  return useAuthStore((state) => state.accessToken !== null)
}
