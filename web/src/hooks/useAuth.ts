import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import * as authApi from "@/api/auth"
import { useAuthStore, useIsAuthenticated } from "@/stores/authStore"
import type { PasswordChange, UserCreate, UserLogin, UserUpdate } from "@/types"

const ME_QUERY_KEY = ["auth", "me"] as const

export function useAuth() {
  const user = useAuthStore((state) => state.user)
  const isAuthenticated = useIsAuthenticated()
  const queryClient = useQueryClient()

  // Self-heals the case where a token exists (e.g. rehydrated from
  // localStorage) but the persisted user is missing.
  const meQuery = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: authApi.getMe,
    enabled: isAuthenticated && user === null,
  })

  useEffect(() => {
    if (meQuery.data) {
      useAuthStore.getState().setUser(meQuery.data)
    }
  }, [meQuery.data])

  const loginMutation = useMutation({
    mutationFn: async (payload: UserLogin) => {
      // LoginResponse has no user field — store tokens first (so the
      // apiClient interceptor can attach them), then fetch the profile.
      const tokens = await authApi.login(payload)
      useAuthStore.getState().setTokens({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        expiresAt: tokens.expires_at,
      })
      const me = await authApi.getMe()
      useAuthStore.getState().setUser(me)
      return me
    },
  })

  // Registration does not log the user in — POST /auth/register returns
  // UserResponse, not tokens (see backend/apis/v1/auth.py). Caller follows
  // up with login().
  const registerMutation = useMutation({
    mutationFn: (payload: UserCreate) => authApi.register(payload),
  })

  const logoutMutation = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => {
      useAuthStore.getState().clearAuth()
      queryClient.removeQueries({ queryKey: ME_QUERY_KEY })
    },
  })

  const updateProfileMutation = useMutation({
    mutationFn: (payload: UserUpdate) => authApi.updateProfile(payload),
    onSuccess: (updated) => {
      useAuthStore.getState().setUser(updated)
      queryClient.setQueryData(ME_QUERY_KEY, updated)
    },
  })

  const changePasswordMutation = useMutation({
    mutationFn: (payload: PasswordChange) => authApi.changePassword(payload),
  })

  return {
    user,
    isAuthenticated,
    login: loginMutation.mutateAsync,
    isLoggingIn: loginMutation.isPending,
    loginError: loginMutation.error,
    register: registerMutation.mutateAsync,
    isRegistering: registerMutation.isPending,
    registerError: registerMutation.error,
    logout: logoutMutation.mutateAsync,
    isLoggingOut: logoutMutation.isPending,
    updateProfile: updateProfileMutation.mutateAsync,
    isUpdatingProfile: updateProfileMutation.isPending,
    changePassword: changePasswordMutation.mutateAsync,
    isChangingPassword: changePasswordMutation.isPending,
  }
}
