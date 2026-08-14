import { useQuery } from "@tanstack/react-query"
import * as telemetryApi from "@/api/telemetry"

// GET /telemetry/{session_id}/{driver_id}/live reads straight from the
// CarData Redis cache (f1:{season}:{round}:car:{driver_num}:latest, written
// every ~8s with an 8s TTL by ingest_live_session.py) — polling faster than
// that TTL would just repeat the same cached sample.
const LIVE_DRIVER_TELEMETRY_POLL_INTERVAL_MS = 8_000

// Channel-index -> field mapping mirrors backend/services/telemetry_service.py's
// _CAR_DATA_CHANNELS exactly. get_live_lap (unlike get_live_car_channels,
// used only internally for the WS broadcast) returns the raw undecoded
// {"Channels": {"2": ..., ...}} structure — decoded here client-side rather
// than server-side, since this is scoped as a frontend-only change.
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

// Real-time CarData polling — unlike useLiveTelemetry (WS, updates only on
// lap completion), this reflects the live 8s-refreshed telemetry cache
// directly, which is what a "live" speed/gear/throttle/brake/DRS display
// actually needs.
export function useLiveDriverTelemetry(sessionId: string | null, driverId: string | null) {
  return useQuery({
    queryKey: ["telemetry", "live-driver", sessionId, driverId],
    queryFn: () => telemetryApi.getLiveLap(sessionId as string, driverId as string),
    enabled: Boolean(sessionId && driverId),
    refetchInterval: LIVE_DRIVER_TELEMETRY_POLL_INTERVAL_MS,
    retry: false,
    // 503 (no live sample cached yet) is expected whenever the driver isn't
    // on track or the feed is momentarily stale — not a global error toast.
    meta: { silentOn503: true },
    select: (response) => decodeCarChannels(response.data),
  })
}
