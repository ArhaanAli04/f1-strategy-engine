import { cn } from "@/lib/utils"

interface ProgressBarProps {
  // 0..1
  value: number
  className?: string
  barClassName?: string
}

// Plain Tailwind div bar — no @radix-ui/react-progress dependency needed
// for a single-track, non-interactive meter.
export function ProgressBar({ value, className, barClassName }: ProgressBarProps) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn("h-full rounded-full bg-primary transition-all", barClassName)}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
