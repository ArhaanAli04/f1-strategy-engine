import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { Check, User } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/hooks/useAuth"
import { useSavedFlash } from "@/hooks/useSavedFlash"
import { getApiErrorMessage } from "@/utils/errors"
import type { UserUpdate } from "@/types"

// Split out of the former combined ProfileForm.tsx so SettingsModal's
// sidebar can show exactly one section at a time — same validation/mutation
// logic as before, unchanged. No Card wrapper: this renders directly inside
// the Dialog's content pane, which is already the one elevated surface (a
// Card here would be a card-in-a-modal, against DESIGN.md's no-nested-
// containers rule).
export function ProfileSection() {
  const { user, updateProfile, isUpdatingProfile } = useAuth()

  const profileForm = useForm<UserUpdate>({
    defaultValues: { full_name: user?.full_name ?? "", email: user?.email ?? "" },
  })

  // user can still be null at first mount (self-healing GET /auth/me fetch
  // in useAuth, e.g. a page reload that rehydrated tokens but not the
  // persisted user) — defaultValues alone wouldn't pick up that later value.
  useEffect(() => {
    if (user) profileForm.reset({ full_name: user.full_name, email: user.email })
    // profileForm is a stable object identity from useForm — safe to omit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])

  const [justSaved, flashSaved] = useSavedFlash()

  async function onSubmitProfile(values: UserUpdate) {
    try {
      await updateProfile(values)
      toast.success("Profile updated")
      flashSaved()
    } catch (error) {
      toast.error(getApiErrorMessage(error, "Failed to update profile"))
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <User className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">Profile</h2>
      </div>
      <Form {...profileForm}>
        <form onSubmit={profileForm.handleSubmit(onSubmitProfile)} className="space-y-4">
          <FormField
            control={profileForm.control}
            name="full_name"
            rules={{ required: "Name is required" }}
            render={({ field }) => (
              <FormItem>
                <FormLabel>Full name</FormLabel>
                <FormControl>
                  <Input autoComplete="name" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={profileForm.control}
            name="email"
            rules={{
              required: "Email is required",
              pattern: {
                value: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
                message: "Enter a valid email address",
              },
            }}
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input type="email" autoComplete="email" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" disabled={isUpdatingProfile}>
            {isUpdatingProfile ? (
              "Saving..."
            ) : justSaved ? (
              <span className="flex items-center gap-1.5">
                <Check className="h-4 w-4" />
                Saved
              </span>
            ) : (
              "Save profile"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}
