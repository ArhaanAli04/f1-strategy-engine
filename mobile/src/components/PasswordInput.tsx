import { Ionicons } from "@expo/vector-icons"
import { useState } from "react"
import { Pressable, TextInput, View, type TextInputProps } from "react-native"

type PasswordInputProps = Omit<TextInputProps, "secureTextEntry">

// RN equivalent of web/src/components/ui/password-input.tsx — plain
// TextInput + a local show/hide toggle (no library), same as web.
export function PasswordInput({ className, ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false)

  return (
    <View className="relative justify-center">
      <TextInput
        secureTextEntry={!visible}
        placeholderTextColor="#555"
        className={`rounded-md border border-white/10 bg-surface px-3 py-2.5 pr-10 text-base text-foreground ${className ?? ""}`}
        {...props}
      />
      <Pressable
        onPress={() => setVisible((v) => !v)}
        hitSlop={8}
        className="absolute right-3"
        accessibilityLabel={visible ? "Hide password" : "Show password"}
      >
        <Ionicons name={visible ? "eye-off-outline" : "eye-outline"} size={18} color="#999999" />
      </Pressable>
    </View>
  )
}
