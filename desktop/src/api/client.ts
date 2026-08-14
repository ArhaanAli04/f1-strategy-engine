import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios"
import { useAuthStore } from "@/stores/authStore"
import type { TokenResponse } from "@/types"
import { API_URL } from "@/utils/constants"

export const apiClient = axios.create({
  baseURL: `${API_URL}/api/v1`,
})

apiClient.interceptors.request.use((config) => {
  const { accessToken } = useAuthStore.getState()
  if (accessToken) {
    config.headers.set("Authorization", `Bearer ${accessToken}`)
  }
  return config
})

// Single-flight: concurrent 401s during the same expiry wait on one refresh
// call instead of each firing its own POST /auth/refresh.
let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const { refreshToken, setAccessToken, clearAuth } = useAuthStore.getState()
  if (!refreshToken) return null
  try {
    // Plain axios, not apiClient — going through apiClient's own response
    // interceptor here would recurse into this same 401 handling on failure.
    const { data } = await axios.post<TokenResponse>(`${API_URL}/api/v1/auth/refresh`, {
      refresh_token: refreshToken,
    })
    setAccessToken(data.access_token, data.expires_at)
    return data.access_token
  } catch {
    clearAuth()
    return null
  }
}

interface RetryableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableConfig | undefined
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true
      refreshPromise ??= refreshAccessToken().finally(() => {
        refreshPromise = null
      })
      const newToken = await refreshPromise
      if (newToken) {
        originalRequest.headers.set("Authorization", `Bearer ${newToken}`)
        return apiClient(originalRequest)
      }
    }
    return Promise.reject(error)
  },
)
