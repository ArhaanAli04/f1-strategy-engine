import { Link, router } from "expo-router"
import { useState } from "react"
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native"
import { PasswordInput } from "@/components/PasswordInput"
import { useAuth } from "@/hooks/useAuth"
import { getApiErrorMessage } from "@/utils/errors"

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface FormErrors {
  fullName?: string
  email?: string
  password?: string
  confirmPassword?: string
}

export default function RegisterScreen() {
  const { register, isRegistering } = useAuth()
  const [fullName, setFullName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [errors, setErrors] = useState<FormErrors>({})
  const [serverError, setServerError] = useState<string | null>(null)

  function validate(): boolean {
    const next: FormErrors = {}
    if (!fullName) next.fullName = "Full name is required"
    if (!email) next.email = "Email is required"
    else if (!EMAIL_PATTERN.test(email)) next.email = "Enter a valid email address"
    if (!password) next.password = "Password is required"
    else if (password.length < 8) next.password = "Password must be at least 8 characters"
    if (!confirmPassword) next.confirmPassword = "Please confirm your password"
    else if (confirmPassword !== password) next.confirmPassword = "Passwords do not match"
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function onSubmit() {
    setServerError(null)
    if (!validate()) return
    try {
      // POST /auth/register returns UserResponse, not tokens — no
      // auto-login (see backend/apis/v1/auth.py). User logs in next.
      await register({ full_name: fullName, email, password })
      Alert.alert("Account created", "Please log in.")
      router.replace("/(auth)/login")
    } catch (error) {
      setServerError(getApiErrorMessage(error, "Registration failed"))
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={{ flex: 1 }}
      className="bg-background"
    >
      <ScrollView
        contentContainerClassName="flex-1 items-center justify-center px-6"
        keyboardShouldPersistTaps="handled"
      >
        <View className="w-full max-w-sm gap-4">
          <View className="mb-2 gap-1">
            <Text className="text-2xl font-bold text-foreground">Create an account</Text>
            <Text className="text-sm text-muted">Sign up for F1 Strategy Engine.</Text>
          </View>

          <View className="gap-1.5">
            <Text className="text-sm font-medium text-foreground">Full name</Text>
            <TextInput
              value={fullName}
              onChangeText={setFullName}
              autoComplete="name"
              placeholder="Max Verstappen"
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
              placeholder="you@example.com"
              placeholderTextColor="#555"
              className="rounded-md border border-white/10 bg-surface px-3 py-2.5 text-base text-foreground"
            />
            {errors.email && <Text className="text-sm text-destructive">{errors.email}</Text>}
          </View>

          <View className="gap-1.5">
            <Text className="text-sm font-medium text-foreground">Password</Text>
            <PasswordInput value={password} onChangeText={setPassword} autoComplete="new-password" />
            {errors.password && <Text className="text-sm text-destructive">{errors.password}</Text>}
          </View>

          <View className="gap-1.5">
            <Text className="text-sm font-medium text-foreground">Confirm password</Text>
            <PasswordInput
              value={confirmPassword}
              onChangeText={setConfirmPassword}
              autoComplete="new-password"
            />
            {errors.confirmPassword && (
              <Text className="text-sm text-destructive">{errors.confirmPassword}</Text>
            )}
          </View>

          {serverError && (
            <Text role="alert" className="text-sm font-medium text-destructive">
              {serverError}
            </Text>
          )}

          <Pressable
            onPress={onSubmit}
            disabled={isRegistering}
            className="mt-2 items-center rounded-md bg-foreground py-3 disabled:opacity-50"
          >
            <Text className="text-base font-semibold text-background">
              {isRegistering ? "Creating account..." : "Create account"}
            </Text>
          </Pressable>

          <View className="flex-row justify-center gap-1">
            <Text className="text-sm text-muted">Already have an account?</Text>
            <Link href="/(auth)/login" className="text-sm font-medium text-foreground underline">
              Log in
            </Link>
          </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
