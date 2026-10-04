import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { isAxiosError } from "axios"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { toast } from "sonner"
import App from "./App"
import { RaceOverlay } from "@/components/overlay/RaceOverlay"
import { ErrorBoundary } from "@/components/shared/ErrorBoundary"
import { Toaster } from "@/components/ui/sonner"
import { getApiErrorMessage } from "@/utils/errors"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
      retry: 2,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      // Same as web's main.tsx: a few queries treat a specific HTTP status
      // as a normal outcome the component renders itself, not a global error
      // toast. Opt in via `meta: { silentOn404: true }` / `silentOn503: true`.
      // - 404: useCurrentRace when no race is live or upcoming,
      //   useCircuitOutline before outlines are extracted.
      // - 503: useLiveDriverTelemetry whenever no CarData sample is cached —
      //   always during a Demo Replay, which publishes no car telemetry
      //   (Day 6b: desktop toasted "No live telemetry cached for car N"
      //   every poll).
      if (isAxiosError(error) && error.response) {
        const status = error.response.status
        if ((status === 404 && query.meta?.silentOn404) || (status === 503 && query.meta?.silentOn503)) {
          return
        }
      }
      toast.error(getApiErrorMessage(error))
    },
  }),
})

// Both windows (main, overlay) load the same index.html/entry — the Tauri
// window label (set in tauri.conf.json) decides which UI actually mounts.
const isOverlayWindow = getCurrentWindow().label === "overlay"

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        {isOverlayWindow ? <RaceOverlay /> : <App />}
        {!isOverlayWindow && <Toaster />}
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
