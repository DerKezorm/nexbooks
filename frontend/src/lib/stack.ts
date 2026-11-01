/**
 * The reading stack as the design makes it: a book lies as thick as its pages say
 * (a recording as thick as a recording alone is: 13 px), as long as its (fixed) height says, and a little out of line.
 * Pure functions of what the server says, so that a test can hold them against the numbers of the design.
 */
import type { StackEntry, StackYear } from '../api/client'

/** The thickness of a lying book: the pages over 28, between 8 and 22 px; a recording is 13 px. */
export function lyingThickness(entry: Pick<StackEntry, 'work' | 'pages'>): number {
  if (entry.work.audio_only) return 13
  return Math.round(Math.max(8, Math.min(22, entry.pages / 28)))
}

/** The length of a lying book: 124 px plus the (fixed) height of its spine times 56. */
export function lyingWidth(entry: Pick<StackEntry, 'work'>): number {
  return Math.round(124 + entry.work.tall * 56)
}

/** How far a lying book is pushed out of line, -11 to 11 px, fixed by the entry so that a tower stands the same every time
 * (the design draws them at random; a tower that changes at every visit would be no tower of ours). */
export function lyingOffset(entry: Pick<StackEntry, 'id'>): number {
  return ((entry.id * 7919) % 23) - 11
}

/** The year the recap starts with: the last year that is over and has books, or the newest year there is (the design shows
 * 2025 in 2026). `today` is the server's day for the person (`YYYY-MM-DD`). */
export function recapYear(years: Pick<StackYear, 'year'>[], today: string): number | null {
  if (years.length === 0) return null
  const now = Number(today.slice(0, 4))
  const over = years.filter((year) => year.year < now)
  return (over.length > 0 ? over[over.length - 1] : years[years.length - 1])!.year
}

/** The hours a number of seconds comes to, rounded as the design does. */
export function hours(seconds: number): number {
  return Math.round(seconds / 3600)
}
