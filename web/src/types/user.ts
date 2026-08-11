// Mirrors backend/schemas/user_schema.py

export interface UserCreate {
  email: string
  password: string
  full_name: string
}

export interface UserResponse {
  id: string
  email: string
  full_name: string
  is_active: boolean
  subscription_tier: string
  created_at: string
}

export interface UserLogin {
  email: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_at: string
}

export interface LoginResponse extends TokenResponse {
  refresh_token: string
}

export interface RefreshTokenRequest {
  refresh_token: string
}

export interface FCMTokenUpdate {
  fcm_token: string
}

export interface UserUpdate {
  full_name?: string
  email?: string
}

export interface PasswordChange {
  current_password: string
  new_password: string
}

export interface SubscriptionCreate {
  driver_ids: string[]
  team_ids: string[]
  alert_types: string[]
}

export interface SubscriptionResponse {
  id: string
  user_id: string
  driver_ids: string[]
  team_ids: string[]
  alert_types: string[]
}
