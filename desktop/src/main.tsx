import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
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
    onError: (error) => {
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
