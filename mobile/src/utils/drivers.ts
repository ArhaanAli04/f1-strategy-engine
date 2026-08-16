import type { DriverResponse } from "@/types"

// GET /drivers returns every driver ever ingested (see
// backend/apis/v1/drivers.py — no active-roster filter param), including
// retired/historical drivers with no current-season contract. Only a driver
// with a real team assignment belongs on a "current roster" — used anywhere
// a driver picker/list should reflect who's actually on the current grid,
// not the full historical roster. Single source of truth — was previously
// duplicated verbatim across DriverRosterGrid.tsx and AlertSubscriptionsForm.tsx.
export function isActiveDriver(driver: DriverResponse): boolean {
  const contract = driver.contracts[0]
  return Boolean(contract?.team_id) && Boolean(contract?.team?.name)
}
