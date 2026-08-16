import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// Hand-written — mirrors web/src/hooks/useLiveDriverTelemetry.ts exactly,
// including the channel-index -> field decoding (mirrors
// backend/services/telemetry_service.py's _CAR_DATA_CHANNELS).
const LIVE_DRIVER_TELEMETRY_POLL_INTERVAL_MS = 8_000

const DRS_STATUS_CODES: Record<number, DecodedCarChannels["drs"]> = {
  0: "off",
  8: "available",
  10: "enabled",
  14: "open",
}

export interface DecodedCarChannels {
  speed_kmh: number | null
  gear: number | null
  throttle_pct: number | null
  brake: boolean | null
  drs: "off" | "available" | "enabled" | "open" | "unknown" | null
}

function decodeCarChannels(data: Record<string, unknown> | undefined): DecodedCarChannels {
  const channels = data?.Channels as Record<string, unknown> | undefined
  if (!channels || typeof channels !== "object") {
    return { speed_kmh: null, gear: null, throttle_pct: null, brake: null, drs: null }
  }

  const speed = channels["2"]
  const gear = channels["3"]
  const throttle = channels["4"]
  const brake = channels["5"]
  const drs = channels["45"]

  return {
    speed_kmh: speed != null ? Number(speed) : null,
    gear: gear != null ? Number(gear) : null,
    throttle_pct: throttle != null ? Number(throttle) : null,
    brake: brake != null ? Boolean(brake) : null,
    drs: drs != null ? (DRS_STATUS_CODES[Number(drs)] ?? "unknown") : null,
  }
}

export function useLiveDriverTelemetry(sessionId: string | null, driverId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "live-driver", sessionId, driverId],
    queryFn: () => telemetryApi.getLiveLap(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId),
    refetchInterval: LIVE_DRIVER_TELEMETRY_POLL_INTERVAL_MS,
    retry: false,
    select: (response) => decodeCarChannels(response.data),
  })
}
