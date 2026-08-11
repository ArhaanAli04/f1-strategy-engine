import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/hooks/useAuth"
import { getApiErrorMessage } from "@/utils/errors"
import type { PasswordChange, UserUpdate } from "@/types"

interface PasswordChangeFormValues extends PasswordChange {
  confirm_password: string
}

export function ProfileForm() {
  const { user, updateProfile, isUpdatingProfile, changePassword, isChangingPassword } = useAuth()

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

  const passwordForm = useForm<PasswordChangeFormValues>({
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  })

  async function onSubmitProfile(values: UserUpdate) {
    try {
      await updateProfile(values)
      toast.success("Profile updated")
    } catch (error) {
      toast.error(getApiErrorMessage(error, "Failed to update profile"))
    }
  }

  async function onSubmitPassword(values: PasswordChangeFormValues) {
    try {
      await changePassword({
        current_password: values.current_password,
        new_password: values.new_password,
      })
      toast.success("Password changed")
      passwordForm.reset()
    } catch (error) {
      toast.error(getApiErrorMessage(error, "Failed to change password"))
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
        </CardHeader>
        <Form {...profileForm}>
          <form onSubmit={profileForm.handleSubmit(onSubmitProfile)}>
            <CardContent className="space-y-4">
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
                {isUpdatingProfile ? "Saving..." : "Save profile"}
              </Button>
            </CardContent>
          </form>
        </Form>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Change Password</CardTitle>
        </CardHeader>
        <Form {...passwordForm}>
          <form onSubmit={passwordForm.handleSubmit(onSubmitPassword)}>
            <CardContent className="space-y-4">
              <FormField
                control={passwordForm.control}
                name="current_password"
                rules={{ required: "Current password is required" }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Current password</FormLabel>
                    <FormControl>
                      <Input type="password" autoComplete="current-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={passwordForm.control}
                name="new_password"
                rules={{
                  required: "New password is required",
                  minLength: { value: 8, message: "Must be at least 8 characters" },
                }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>New password</FormLabel>
                    <FormControl>
                      <Input type="password" autoComplete="new-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={passwordForm.control}
                name="confirm_password"
                rules={{
                  required: "Confirm your new password",
                  validate: (value) =>
                    value === passwordForm.getValues("new_password") || "Passwords do not match",
                }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Confirm new password</FormLabel>
                    <FormControl>
                      <Input type="password" autoComplete="new-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={isChangingPassword}>
                {isChangingPassword ? "Changing..." : "Change password"}
              </Button>
            </CardContent>
          </form>
        </Form>
      </Card>
    </div>
  )
}
