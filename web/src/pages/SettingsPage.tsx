import { AlertSubscriptionsForm } from "@/components/settings/AlertSubscriptionsForm"
import { ProfileForm } from "@/components/settings/ProfileForm"

export function SettingsPage() {
  return (
    <div className="mx-auto h-full max-w-2xl space-y-4 overflow-y-auto p-6">
      <h1 className="text-xl font-semibold">Settings</h1>
      <ProfileForm />
      <AlertSubscriptionsForm />
    </div>
  )
}
