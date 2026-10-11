// A driver with more than two names shows the one fans know them by, keyed
// by driver code (owner's choice, 2026-10-07). Anyone else with three or more
// names shows the first.
const GIVEN_NAME_BY_CODE: Record<string, string> = {
  ANT: "Kimi",
}

// Given name then surname in capitals, as TV graphics do (mobile only, the
// Live tower and the Drivers tab's championship card): "Max VERSTAPPEN". The
// surname is the last word of the full name, which holds for the whole 2026
// grid. A one-word name shows in capitals alone; no name at all falls back to
// the code.
export function displayDriverName(fullName: string | null | undefined, code: string | null | undefined): string {
  const words = fullName?.trim().split(/\s+/).filter(Boolean) ?? []
  if (words.length === 0) return code ?? "???"
  const surname = words[words.length - 1].toUpperCase()
  if (words.length === 1) return surname
  const givenName = (code && GIVEN_NAME_BY_CODE[code]) ?? words[0]
  return `${givenName} ${surname}`
}
