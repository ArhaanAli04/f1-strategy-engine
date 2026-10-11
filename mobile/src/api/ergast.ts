// Direct browser call to the public Ergast-compatible Jolpica API — not
// routed through apiClient, since this is an external, unauthenticated data
// source (not our own backend) and per the Day 29 spec is fetched
// frontend-only with no backend involvement.
const ERGAST_BASE_URL = "https://api.jolpi.ca/ergast/f1"

interface ErgastResult {
  position: string
  points: string
}

interface ErgastCircuit {
  circuitName: string
}

export interface ErgastRace {
  round: string
  raceName: string
  Circuit: ErgastCircuit
  Results: ErgastResult[]
}

interface ErgastResponse {
  MRData: {
    RaceTable: {
      Races: ErgastRace[]
    }
  }
}

export async function getDriverSeasonResults(
  ergastDriverId: string,
  season: number,
): Promise<ErgastRace[]> {
  const response = await fetch(`${ERGAST_BASE_URL}/${season}/drivers/${ergastDriverId}/results/`)
  if (!response.ok) {
    throw new Error(`Ergast request failed: ${response.status}`)
  }
  const data = (await response.json()) as ErgastResponse
  return data.MRData.RaceTable.Races
}

export interface ErgastDriverStanding {
  position: string
  points: string
  wins: string
}

interface ErgastDriverStandingsResponse {
  MRData: {
    StandingsTable: {
      StandingsLists: {
        DriverStandings: ErgastDriverStanding[]
      }[]
    }
  }
}

// Per-driver-scoped endpoint (not the full grid) — returns just this
// driver's own standing directly, same "empty before the season's first
// points-paying session" shape as getConstructorStandings.
export async function getDriverStandings(
  ergastDriverId: string,
  season: number,
): Promise<ErgastDriverStanding | null> {
  const response = await fetch(`${ERGAST_BASE_URL}/${season}/drivers/${ergastDriverId}/driverStandings/`)
  if (!response.ok) {
    throw new Error(`Ergast request failed: ${response.status}`)
  }
  const data = (await response.json()) as ErgastDriverStandingsResponse
  return data.MRData.StandingsTable.StandingsLists[0]?.DriverStandings[0] ?? null
}

export interface ErgastSeasonDriverStanding {
  position: string
  points: string
  wins: string
  Driver: {
    driverId: string
    code?: string
    givenName: string
    familyName: string
  }
  Constructors: {
    constructorId: string
    name: string
  }[]
}

export interface ErgastSeasonDriverStandings {
  // The round the table is "after"; null before the season's first
  // points-paying session (StandingsLists is empty then).
  round: number | null
  standings: ErgastSeasonDriverStanding[]
}

interface ErgastSeasonDriverStandingsResponse {
  MRData: {
    StandingsTable: {
      StandingsLists: {
        round: string
        DriverStandings: ErgastSeasonDriverStanding[]
      }[]
    }
  }
}

// Mobile only (2026-10-07, the Drivers tab's championship card): the whole
// drivers' championship in one request, where getDriverStandings above takes
// one request per driver. The rest of this file is a verbatim copy of web's.
export async function getSeasonDriverStandings(season: number): Promise<ErgastSeasonDriverStandings> {
  const response = await fetch(`${ERGAST_BASE_URL}/${season}/driverStandings/`)
  if (!response.ok) {
    throw new Error(`Ergast request failed: ${response.status}`)
  }
  const data = (await response.json()) as ErgastSeasonDriverStandingsResponse
  const table = data.MRData.StandingsTable.StandingsLists[0]
  return {
    round: table ? Number(table.round) : null,
    standings: table?.DriverStandings ?? [],
  }
}

export interface ErgastConstructorStanding {
  position: string
  points: string
  Constructor: {
    constructorId: string
    name: string
  }
}

interface ErgastConstructorStandingsResponse {
  MRData: {
    StandingsTable: {
      StandingsLists: {
        ConstructorStandings: ErgastConstructorStanding[]
      }[]
    }
  }
}

export async function getConstructorStandings(
  season: number,
): Promise<ErgastConstructorStanding[]> {
  const response = await fetch(`${ERGAST_BASE_URL}/${season}/constructorStandings/`)
  if (!response.ok) {
    throw new Error(`Ergast request failed: ${response.status}`)
  }
  const data = (await response.json()) as ErgastConstructorStandingsResponse
  // Empty (not missing) before the season's first points-paying session —
  // StandingsLists itself is [] rather than containing a zero-length table.
  return data.MRData.StandingsTable.StandingsLists[0]?.ConstructorStandings ?? []
}
