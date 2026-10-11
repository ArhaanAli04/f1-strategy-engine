// Team logo sizes for list rows (mobile only: the Live tower and the Drivers
// tab's championship card). TeamLogo's default 32/44 px would make rows of
// different heights, so rows pass their own sizes, each logo centred in one
// fixed-size box: larger for the marks that read small at the default size
// (Red Bull, Haas and Alpine enlarged further after the first device check,
// 2026-10-06).
const ROW_LOGO_SIZE_PX = 24
const ROW_LOGO_SIZES_PX: Record<string, number> = {
  "Red Bull Racing": 36,
  Haas: 36,
  Alpine: 36,
  Ferrari: 30,
}

export function rowLogoSize(teamName: string | undefined): number {
  return (teamName && ROW_LOGO_SIZES_PX[teamName]) || ROW_LOGO_SIZE_PX
}
