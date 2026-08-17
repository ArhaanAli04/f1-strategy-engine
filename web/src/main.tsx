import { isAxiosError } from "axios"
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { toast } from "sonner"
import App from "./App.tsx"
import { ErrorBoundary } from "@/components/shared/ErrorBoundary"
import { Toaster } from "@/components/ui/sonner"
import { getApiErrorMessage } from "@/utils/errors"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Per-query staleTime (e.g. useStrategyOverview's 30s) overrides this
      // default — 0 keeps genuinely live data (telemetry, gaps) fresh.
      staleTime: 0,
      retry: 2,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      // A handful of queries treat a specific HTTP status as a normal,
      // expected outcome the component renders directly — not a global
      // error toast. Opt in via `meta: { silentOn404: true }` /
      // `silentOn503: true` on the query itself.
      // - 404: useCircuitOutline before extract_circuit_outlines.py has
      //   run, useUpcomingRace once a season concludes.
      // - 503: useLiveDriverTelemetry's 8s poll whenever no live CarData
      //   sample is cached yet (driver not on track / feed momentarily
      //   stale) — TelemetryNotAvailableError, expected every poll a live
      //   ingestor isn't actively running.
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
        <Toaster />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
