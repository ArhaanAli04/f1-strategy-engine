// Static equivalent of components/shared/DriverChip.tsx — same shape (team-
// color dot + code, rounded-full/border/px-2 py-0.5), but takes its color
// and label as props instead of resolving a real driverId via useDrivers().
// Used only inside landing/ sample tiles, which show synthetic data and must
// never imply a real, resolvable driver.
interface SampleChipProps {
  code: string
  color: string
}

export function SampleChip({ code, color }: SampleChipProps) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold">
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {code}
    </span>
  )
}
