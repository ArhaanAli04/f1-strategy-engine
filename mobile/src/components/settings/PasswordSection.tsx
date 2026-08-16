import { useState } from "react"
import { Alert, Pressable, Text, View } from "react-native"
import { PasswordInput } from "@/components/PasswordInput"
import { useAuth } from "@/hooks/useAuth"
import { getApiErrorMessage } from "@/utils/errors"

interface FormErrors {
  current?: string
  next?: string
  confirm?: string
}

// RN equivalent of web/src/components/settings/PasswordSection.tsx — plain
// useState form, same reasoning as ProfileSection.tsx.
export function PasswordSection() {
  const { changePassword, isChangingPassword } = useAuth()
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [errors, setErrors] = useState<FormErrors>({})

  async function onSave() {
    const nextErrors: FormErrors = {}
    if (!current) nextErrors.current = "Current password is required"
    if (!next) nextErrors.next = "New password is required"
    else if (next.length < 8) nextErrors.next = "Must be at least 8 characters"
    if (!confirm) nextErrors.confirm = "Confirm your new password"
    else if (confirm !== next) nextErrors.confirm = "Passwords do not match"
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return
    try {
      await changePassword({ current_password: current, new_password: next })
      Alert.alert("Saved", "Password changed.")
      setCurrent("")
      setNext("")
      setConfirm("")
    } catch (error) {
      Alert.alert("Error", getApiErrorMessage(error, "Failed to change password"))
    }
  }

  return (
    <View className="gap-4">
      <Text className="text-base font-semibold text-foreground">Change password</Text>
      <View className="gap-1.5">
        <Text className="text-sm font-medium text-foreground">Current password</Text>
        <PasswordInput value={current} onChangeText={setCurrent} autoComplete="current-password" />
        {errors.current && <Text className="text-sm text-destructive">{errors.current}</Text>}
      </View>
      <View className="gap-1.5">
        <Text className="text-sm font-medium text-foreground">New password</Text>
        <PasswordInput value={next} onChangeText={setNext} autoComplete="new-password" />
        {errors.next && <Text className="text-sm text-destructive">{errors.next}</Text>}
      </View>
      <View className="gap-1.5">
        <Text className="text-sm font-medium text-foreground">Confirm new password</Text>
        <PasswordInput value={confirm} onChangeText={setConfirm} autoComplete="new-password" />
        {errors.confirm && <Text className="text-sm text-destructive">{errors.confirm}</Text>}
      </View>
      <Pressable
        onPress={onSave}
        disabled={isChangingPassword}
        className="items-center rounded-md bg-foreground py-3 disabled:opacity-50"
      >
        <Text className="text-base font-semibold text-background">
          {isChangingPassword ? "Changing..." : "Change password"}
        </Text>
      </Pressable>
    </View>
  )
}
