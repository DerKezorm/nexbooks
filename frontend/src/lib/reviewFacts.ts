/**
 * What the review page shows of a file beside a proposal: the rows of the comparison sorted into the groups a person reads
 * (the file contradicts, is close, agrees, says nothing, or the source does not know it), and the places a short listen
 * starts from. Nothing here talks to the server or draws anything.
 */
import type { CompareRow, CompareState } from '../api/client'

/** The groups in the order they are shown: what speaks against a proposal first. */
export const GROUPS: CompareState[] = ['differs', 'similar', 'agrees', 'file_silent', 'source_silent']

export function groupRows(rows: CompareRow[]): Record<CompareState, CompareRow[]> {
  const out: Record<CompareState, CompareRow[]> = { differs: [], similar: [], agrees: [], file_silent: [], source_silent: [] }
  for (const row of rows) out[row.state]?.push(row)
  return out
}

/** How many fields the file contradicts: the number a person looks at first. */
export function contradictions(rows: CompareRow[]): number {
  return rows.filter((row) => row.state === 'differs').length
}

/** How much of the end a short listen plays: a recording cut off stops in the middle of a sentence. */
const END_SECONDS = 20

/** Where a short listen jumps to, in seconds from the beginning: the start, a third of the way in, the middle, the end. */
export function jumpPoints(total: number): { key: 'start' | 'third' | 'middle' | 'end'; at: number }[] {
  const whole = Number.isFinite(total) && total > 0 ? total : 0
  return [
    { key: 'start', at: 0 },
    { key: 'third', at: Math.floor(whole / 3) },
    { key: 'middle', at: Math.floor(whole / 2) },
    { key: 'end', at: Math.max(0, Math.floor(whole) - END_SECONDS) },
  ]
}

/** A folder inside the library as written for people: the top of the library has no folder. */
export function folderText(folder: string, top: string): string {
  return folder === '' ? top : folder
}
