import { Component, type ErrorInfo, type ReactNode } from "react"

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled error in component tree:", error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div role="alert" className="flex min-h-screen flex-col items-center justify-center gap-2">
          <h1 className="text-xl font-semibold">Something went wrong</h1>
          <p className="text-muted-foreground text-sm">{this.state.error.message}</p>
        </div>
      )
    }
    return this.props.children
  }
}
