import { Link } from "react-router-dom"
import { ROUTES } from "@/utils/constants"

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-2">
      <h1 className="text-xl font-semibold">404 — Page not found</h1>
      <Link to={ROUTES.DASHBOARD} className="text-primary underline underline-offset-4">
        Back to dashboard
      </Link>
    </div>
  )
}
