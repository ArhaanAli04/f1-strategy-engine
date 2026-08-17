// Maps this app's driver code (backend/scripts/seed_teams.py's 2026 roster)
// to the stable Ergast/Jolpica driverId used by GET
// /ergast/f1/{season}/drivers/{driverId}/results/. Ergast driverIds don't
// change once assigned, so this is safe to hardcode rather than resolve
// dynamically. A driver with no 2025 Ergast history (e.g. a genuine 2026
// rookie) simply won't appear in that season's results — useDriverSeasonStats
// handles that as "no stats available", not an error.
export const ERGAST_DRIVER_IDS: Record<string, string> = {
  NOR: "norris",
  PIA: "piastri",
  HAM: "hamilton",
  LEC: "leclerc",
  VER: "max_verstappen",
  HAD: "hadjar",
  RUS: "russell",
  ANT: "antonelli",
  ALB: "albon",
  SAI: "sainz",
  HUL: "hulkenberg",
  BOR: "bortoleto",
  ALO: "alonso",
  STR: "stroll",
  GAS: "gasly",
  COL: "colapinto",
  OCO: "ocon",
  BEA: "bearman",
  LAW: "lawson",
  LIN: "lindblad",
  PER: "perez",
  BOT: "bottas",
}

export function getErgastDriverId(driverCode: string): string | null {
  return ERGAST_DRIVER_IDS[driverCode.toUpperCase()] ?? null
}
