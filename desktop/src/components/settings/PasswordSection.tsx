import { useForm } from "react-hook-form"
import { Check, KeyRound } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { PasswordInput } from "@/components/ui/password-input"
import { useAuth } from "@/hooks/useAuth"
import { useSavedFlash } from "@/hooks/useSavedFlash"
import { getApiErrorMessage } from "@/utils/errors"
import type { PasswordChange } from "@/types"

interface PasswordChangeFormValues extends PasswordChange {
  confirm_password: string
}

// Split out of the former combined ProfileForm.tsx — see ProfileSection.tsx
// for why there's no Card wrapper here.
export function PasswordSection() {
  const { changePassword, isChangingPassword } = useAuth()

  const passwordForm = useForm<PasswordChangeFormValues>({
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  })

  const [justSaved, flashSaved] = useSavedFlash()

  async function onSubmitPassword(values: PasswordChangeFormValues) {
    try {
      await changePassword({
        current_password: values.current_password,
        new_password: values.new_password,
      })
      toast.success("Password changed")
      flashSaved()
      passwordForm.reset()
    } catch (error) {
      toast.error(getApiErrorMessage(error, "Failed to change password"))
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">Change Password</h2>
      </div>
      <Form {...passwordForm}>
        <form onSubmit={passwordForm.handleSubmit(onSubmitPassword)} className="space-y-4">
          <FormField
            control={passwordForm.control}
            name="current_password"
            rules={{ required: "Current password is required" }}
            render={({ field }) => (
              <FormItem>
                <FormLabel>Current password</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="current-password" {...field} />
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
                  <PasswordInput autoComplete="new-password" {...field} />
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
                  <PasswordInput autoComplete="new-password" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" disabled={isChangingPassword}>
            {isChangingPassword ? (
              "Changing..."
            ) : justSaved ? (
              <span className="flex items-center gap-1.5">
                <Check className="h-4 w-4" />
                Saved
              </span>
            ) : (
              "Change password"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}
