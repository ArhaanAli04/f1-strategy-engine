import { useState } from "react"
import { Navigate, useNavigate } from "react-router-dom"
import { Bell, KeyRound, User, type LucideIcon } from "lucide-react"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { useIsAuthenticated } from "@/stores/authStore"
import { ROUTES } from "@/utils/constants"
import { AlertSubscriptionsForm } from "./AlertSubscriptionsForm"
import { PasswordSection } from "./PasswordSection"
import { ProfileSection } from "./ProfileSection"

type SettingsSection = "profile" | "password" | "notifications"

const SECTIONS: { key: SettingsSection; label: string; icon: LucideIcon }[] = [
  { key: "profile", label: "Profile", icon: User },
  { key: "password", label: "Password", icon: KeyRound },
  { key: "notifications", label: "Notifications", icon: Bell },
]

// Rendered as an overlay route (see App.tsx's background-location routing)
// on top of whatever page was open when Settings was reached. Closing
// navigates back via history rather than a hardcoded route, so it returns
// to the exact page (and scroll position) the user came from.
export function SettingsModal() {
  const isAuthenticated = useIsAuthenticated()
  const navigate = useNavigate()
  const [section, setSection] = useState<SettingsSection>("profile")

  // App.tsx's background-location fallback still renders this route even
  // when unauthenticated (it can't know auth state before this component
  // mounts) — this route isn't wrapped in AuthGuard's shell (that would
  // render a second NavBar behind the modal), so it enforces its own
  // redirect the way AuthGuard does for every other protected route.
  if (!isAuthenticated) {
    return <Navigate to={ROUTES.LOGIN} replace />
  }

  return (
    <Dialog open onOpenChange={(open) => !open && navigate(-1)}>
      <DialogContent className="flex h-[85vh] max-h-[720px] w-[92vw] max-w-5xl flex-col gap-0 overflow-hidden p-0">
        <div className="flex-shrink-0 border-b px-6 py-4">
          <DialogTitle className="text-base font-semibold">Settings</DialogTitle>
        </div>
        <div className="flex flex-1 flex-col overflow-hidden sm:flex-row">
          <nav className="flex flex-shrink-0 gap-1 overflow-x-auto border-b p-2 sm:w-56 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r sm:p-4">
            {SECTIONS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setSection(key)}
                className={cn(
                  "flex flex-shrink-0 items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors",
                  section === key
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </nav>
          <div className="flex-1 overflow-y-auto p-6">
            {section === "profile" && <ProfileSection />}
            {section === "password" && <PasswordSection />}
            {section === "notifications" && <AlertSubscriptionsForm />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
