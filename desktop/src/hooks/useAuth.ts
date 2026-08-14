import { useMutation } from "@tanstack/react-query"
import * as authApi from "@/api/auth"
import { useAuthStore, useIsAuthenticated } from "@/stores/authStore"
import type { PasswordChange, UserCreate, UserLogin, UserUpdate } from "@/types"

// Desktop-local. login/register/logout are needed by LoginPage/RegisterPage/
// Sidebar.tsx; updateProfile/changePassword by components/settings/*.
export function useAuth() {
  const user = useAuthStore((state) => state.user)
  const isAuthenticated = useIsAuthenticated()

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
    },
  })

  const updateProfileMutation = useMutation({
    mutationFn: (payload: UserUpdate) => authApi.updateProfile(payload),
    onSuccess: (updated) => {
      useAuthStore.getState().setUser(updated)
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
    register: registerMutation.mutateAsync,
    isRegistering: registerMutation.isPending,
    logout: logoutMutation.mutateAsync,
    isLoggingOut: logoutMutation.isPending,
    updateProfile: updateProfileMutation.mutateAsync,
    isUpdatingProfile: updateProfileMutation.isPending,
    changePassword: changePasswordMutation.mutateAsync,
    isChangingPassword: changePasswordMutation.isPending,
  }
}
