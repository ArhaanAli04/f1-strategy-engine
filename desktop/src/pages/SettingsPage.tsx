import { useState } from "react"
import { Bell, KeyRound, User, type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { AlertSubscriptionsForm } from "@/components/settings/AlertSubscriptionsForm"
import { PasswordSection } from "@/components/settings/PasswordSection"
import { ProfileSection } from "@/components/settings/ProfileSection"

type SettingsSection = "profile" | "password" | "notifications"

const SECTIONS: { key: SettingsSection; label: string; icon: LucideIcon }[] = [
  { key: "profile", label: "Profile", icon: User },
  { key: "password", label: "Password", icon: KeyRound },
  { key: "notifications", label: "Notifications", icon: Bell },
]

// Adapted from web's SettingsModal.tsx: web renders this as a Dialog
// overlay reached via a background-location route; desktop has no router
// and no overlay-route concept, so it's just another sidebar page — same
// section nav, same three section components, no Dialog/navigate(-1).
export function SettingsPage() {
  const [section, setSection] = useState<SettingsSection>("profile")

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex-shrink-0 border-b border-border px-6 py-4">
        <h1 className="text-base font-semibold">Settings</h1>
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
    </div>
  )
}
