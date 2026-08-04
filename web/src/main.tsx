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
    onError: (error) => toast.error(getApiErrorMessage(error)),
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
