import { useEffect, useState } from "react"
import { Alert, Pressable, Text, TextInput, View } from "react-native"
import { useAuth } from "@/hooks/useAuth"
import { getApiErrorMessage } from "@/utils/errors"

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface FormErrors {
  fullName?: string
  email?: string
}

// RN equivalent of web/src/components/settings/ProfileSection.tsx — plain
// useState form instead of react-hook-form (not installed on mobile; this
// form is small enough that a resolver library isn't worth adding).
export function ProfileSection() {
  const { user, updateProfile, isUpdatingProfile } = useAuth()
  const [fullName, setFullName] = useState(user?.full_name ?? "")
  const [email, setEmail] = useState(user?.email ?? "")
  const [errors, setErrors] = useState<FormErrors>({})

  // user can still be null at first mount (self-healing GET /auth/me fetch
  // in useAuth) — mirrors web's same effect.
  useEffect(() => {
    if (user) {
      setFullName(user.full_name)
      setEmail(user.email)
    }
  }, [user])

  async function onSave() {
    const next: FormErrors = {}
    if (!fullName) next.fullName = "Name is required"
    if (!email) next.email = "Email is required"
    else if (!EMAIL_PATTERN.test(email)) next.email = "Enter a valid email address"
    setErrors(next)
    if (Object.keys(next).length > 0) return
    try {
      await updateProfile({ full_name: fullName, email })
      Alert.alert("Saved", "Profile updated.")
    } catch (error) {
      Alert.alert("Error", getApiErrorMessage(error, "Failed to update profile"))
    }
  }

  return (
    <View className="gap-4">
      <Text className="text-base font-semibold text-foreground">Profile</Text>
      <View className="gap-1.5">
        <Text className="text-sm font-medium text-foreground">Full name</Text>
        <TextInput
          value={fullName}
          onChangeText={setFullName}
          autoComplete="name"
          placeholderTextColor="#555"
          className="rounded-md border border-white/10 bg-surface px-3 py-2.5 text-base text-foreground"
        />
        {errors.fullName && <Text className="text-sm text-destructive">{errors.fullName}</Text>}
      </View>
      <View className="gap-1.5">
        <Text className="text-sm font-medium text-foreground">Email</Text>
        <TextInput
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          placeholderTextColor="#555"
          className="rounded-md border border-white/10 bg-surface px-3 py-2.5 text-base text-foreground"
        />
        {errors.email && <Text className="text-sm text-destructive">{errors.email}</Text>}
      </View>
      <Pressable
        onPress={onSave}
        disabled={isUpdatingProfile}
        className="items-center rounded-md bg-foreground py-3 disabled:opacity-50"
      >
        <Text className="text-base font-semibold text-background">
          {isUpdatingProfile ? "Saving..." : "Save profile"}
        </Text>
      </Pressable>
    </View>
  )
}
