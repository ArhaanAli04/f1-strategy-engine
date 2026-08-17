import { useState } from "react"
import { useForm } from "react-hook-form"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { PasswordInput } from "@/components/ui/password-input"
import { useAuth } from "@/hooks/useAuth"
import { getApiErrorMessage } from "@/utils/errors"
import type { UserLogin } from "@/types"

interface LoginPageProps {
  onNavigateToRegister: () => void
}

export function LoginPage({ onNavigateToRegister }: LoginPageProps) {
  const { login, isLoggingIn } = useAuth()
  const [serverError, setServerError] = useState<string | null>(null)

  const form = useForm<UserLogin>({
    defaultValues: { email: "", password: "" },
  })

  async function onSubmit(values: UserLogin) {
    setServerError(null)
    try {
      await login(values)
    } catch (error) {
      setServerError(getApiErrorMessage(error, "Login failed"))
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background px-4">
      <h1 className="sr-only">Log in</h1>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>F1 Strategy Engine</CardTitle>
          <CardDescription>Sign in to your account.</CardDescription>
        </CardHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <CardContent className="space-y-4">
              <FormField
                control={form.control}
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
                      <Input type="email" autoComplete="email" placeholder="you@example.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                rules={{ required: "Password is required" }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <PasswordInput autoComplete="current-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {serverError && (
                <p role="alert" className="text-sm font-medium text-destructive">
                  {serverError}
                </p>
              )}
            </CardContent>
            <CardFooter className="flex flex-col gap-4">
              <Button type="submit" className="w-full" disabled={isLoggingIn}>
                {isLoggingIn ? "Logging in..." : "Log in"}
              </Button>
              <p className="text-sm text-muted-foreground">
                Don&apos;t have an account?{" "}
                <button
                  type="button"
                  onClick={onNavigateToRegister}
                  className="text-primary underline-offset-4 hover:underline"
                >
                  Register
                </button>
              </p>
            </CardFooter>
          </form>
        </Form>
      </Card>
    </div>
  )
}
