import { apiClient } from "./client"
import type {
  FCMTokenUpdate,
  LoginResponse,
  PasswordChange,
  RefreshTokenRequest,
  TokenResponse,
  UserCreate,
  UserLogin,
  UserResponse,
  UserUpdate,
} from "@/types"

export async function register(payload: UserCreate): Promise<UserResponse> {
  const { data } = await apiClient.post<UserResponse>("/auth/register", payload)
  return data
}

export async function login(payload: UserLogin): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>("/auth/login", payload)
  return data
}

// Returns TokenResponse (access_token/expires_at only) — refresh does not
// rotate the refresh_token, see backend/apis/v1/auth.py's refresh handler.
export async function refresh(payload: RefreshTokenRequest): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>("/auth/refresh", payload)
  return data
}

export async function logout(): Promise<void> {
  await apiClient.post("/auth/logout")
}

export async function getMe(): Promise<UserResponse> {
  const { data } = await apiClient.get<UserResponse>("/auth/me")
  return data
}

export async function updateFcmToken(payload: FCMTokenUpdate): Promise<UserResponse> {
  const { data } = await apiClient.put<UserResponse>("/auth/fcm-token", payload)
  return data
}

export async function updateProfile(payload: UserUpdate): Promise<UserResponse> {
  const { data } = await apiClient.put<UserResponse>("/auth/me", payload)
  return data
}

export async function changePassword(payload: PasswordChange): Promise<void> {
  await apiClient.put("/auth/password", payload)
}
