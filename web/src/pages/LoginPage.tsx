import { useState } from "react"
import { useForm } from "react-hook-form"
import { Link, useLocation, useNavigate, type Location } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/hooks/useAuth"
import { ROUTES } from "@/utils/constants"
import { getApiErrorMessage } from "@/utils/errors"
import type { UserLogin } from "@/types"

export function LoginPage() {
  const { login, isLoggingIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [serverError, setServerError] = useState<string | null>(null)

  const form = useForm<UserLogin>({
    defaultValues: { email: "", password: "" },
  })

  async function onSubmit(values: UserLogin) {
    setServerError(null)
    try {
      await login(values)
      // AuthGuard redirects here with state={{ from: location }} — send the
      // user back to whatever route they originally tried to reach.
      const from = (location.state as { from?: Location } | null)?.from?.pathname
      navigate(from ?? ROUTES.DASHBOARD, { replace: true })
    } catch (error) {
      setServerError(getApiErrorMessage(error, "Login failed"))
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Log in</CardTitle>
          <CardDescription>Sign in to your F1 Strategy Engine account.</CardDescription>
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
                      <Input
                        type="email"
                        autoComplete="email"
                        placeholder="you@example.com"
                        {...field}
                      />
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
                      <Input type="password" autoComplete="current-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {serverError && <p className="text-sm font-medium text-destructive">{serverError}</p>}
            </CardContent>
            <CardFooter className="flex flex-col gap-4">
              <Button type="submit" className="w-full" disabled={isLoggingIn}>
                {isLoggingIn ? "Logging in..." : "Log in"}
              </Button>
              <p className="text-sm text-muted-foreground">
                Don&apos;t have an account?{" "}
                <Link to={ROUTES.REGISTER} className="text-primary underline underline-offset-4">
                  Register
                </Link>
              </p>
            </CardFooter>
          </form>
        </Form>
      </Card>
    </div>
  )
}
