// Mirrors backend/schemas/common.py

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface ErrorResponse {
  detail: string
  code: string | null
}

export interface HealthResponse {
  status: string
  version: string
  database: string
  redis: string
}

export interface APIVersion {
  version: string
  build: string | null
}
