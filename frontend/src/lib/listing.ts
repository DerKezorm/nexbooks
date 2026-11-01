/**
 * The list view of the library: how the rows are put in order, and what a cell says. Pure functions of what the
 * server says (`WorkRow`), so that a test can hold them against the rules of the list.
 */
import type { ListSort, WorkBrief, WorkRow } from '../api/client'
import { GENRES, genreKey } from './shelf'
import { lastName } from './spine'

/** The columns that can be put in order by, in the order of the header. */
export const SORTABLE: ListSort[] = ['title', 'author', 'series', 'genre', 'year', 'size', 'mine', 'added']

/** The direction a column starts in when it is chosen: what is newest, furthest or most is on top. */
export function firstDirection(sort: ListSort): 'asc' | 'desc' {
  return sort === 'added' || sort === 'mine' || sort === 'year' || sort === 'size' ? 'desc' : 'asc'
}

/** How far the person is in a book, 0 to 100: the furthest of the e-book and the recording; a finished one counts as 100. */
export function standOf(work: WorkRow): number {
  const mine = work.mine
  if (!mine) return 0
  if (mine.ebook_done || mine.audio_done) return 100
  return Math.round(Math.max(mine.ebook ?? 0, mine.audio ?? 0) * 100)
}

type Key = string | number | null

function keyOf(work: WorkRow, sort: ListSort): Key[] {
  switch (sort) {
    case 'title':
      return [work.title]
    case 'author':
      return [work.authors[0] ? lastName(work.authors[0].name) : null, work.authors[0]?.name ?? null, work.series?.name ?? null, work.series?.pos ?? null, work.title]
    case 'series':
      return [work.series?.name ?? null, work.series?.pos ?? null, work.title]
    case 'genre':
      return [GENRES.indexOf(genreKey(work.genre)), work.title]
    case 'year':
      return [work.year, work.title]
    case 'size':
      return [work.pages || null, work.title]
    case 'language':
      return [work.editions[0]?.language ?? null, work.title]
    case 'mine':
      return [standOf(work) || null, work.title]
    case 'added':
      return [Date.parse(work.added_at) || null, work.title]
    default:
      return []
  }
}

/** The rows in the order of a column. A row with nothing in the column stands last in either direction (an empty series is
 * no series at the top of a list that goes the other way). The order of the shelf (`shelf`) is the order they came in. */
export function sortWorks(works: WorkRow[], sort: ListSort, dir: 'asc' | 'desc', language: string): WorkRow[] {
  if (sort === 'shelf') return dir === 'asc' ? works : [...works].reverse()
  const collator = new Intl.Collator(language, { numeric: true, sensitivity: 'base' })
  const sign = dir === 'asc' ? 1 : -1
  const keyed = works.map((work, index) => ({ work, index, key: keyOf(work, sort) }))
  keyed.sort((a, b) => {
    for (let i = 0; i < Math.max(a.key.length, b.key.length); i++) {
      const x = a.key[i] ?? null
      const y = b.key[i] ?? null
      if (x === y) continue
      if (x === null) return 1
      if (y === null) return -1
      const compared = typeof x === 'number' && typeof y === 'number' ? x - y : collator.compare(String(x), String(y))
      if (compared !== 0) return compared * sign
    }
    return a.index - b.index
  })
  return keyed.map((item) => item.work)
}

/** The first two letters of a name, for the circle of an author without a photo: the first and the last word. */
export function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean)
  const first = Array.from(words[0] ?? '')[0] ?? ''
  const last = words.length > 1 ? (Array.from(words[words.length - 1] ?? '')[0] ?? '') : ''
  return (first + last).toLocaleUpperCase()
}

/** The colour of the circle of an author without a photo: that of the genre most of their books are (the first colour of the
 * genre's palette on the server, so that a circle matches their spines). */
export const GENRE_COLOR: Record<string, string> = {
  roman: '#5e2027',
  krimi: '#3d4752',
  fantasy: '#4f2b47',
  scifi: '#215258',
  klassiker: '#2f4b3b',
  sachbuch: '#8b4b2b',
  kinder: '#a3762c',
  lyrik: '#7c8c6b',
  none: '#6f665a',
}

export function circleColor(genre: string): string {
  return GENRE_COLOR[genre] ?? GENRE_COLOR.none!
}

/** The rows a window shows: the range around what is on the screen, and the heights of what is cut off above and below. */
export function windowRange(count: number, rowHeight: number, top: number, viewport: number, over = 8): { from: number; to: number; before: number; after: number } {
  const first = Math.max(0, Math.floor(top / rowHeight) - over)
  const last = Math.min(count, Math.ceil((top + viewport) / rowHeight) + over)
  const from = Math.min(first, count)
  const to = Math.max(from, last)
  return { from, to, before: from * rowHeight, after: Math.max(0, count - to) * rowHeight }
}

/** The series of an author's books with at least two volumes here, each with its volumes in order. */
export function seriesOf(works: WorkBrief[]): { id: number; name: string; works: WorkBrief[] }[] {
  const found = new Map<number, { id: number; name: string; works: WorkBrief[] }>()
  for (const work of works) {
    if (!work.series) continue
    const entry = found.get(work.series.id) ?? { id: work.series.id, name: work.series.name, works: [] }
    entry.works.push(work)
    found.set(work.series.id, entry)
  }
  return [...found.values()]
    .filter((entry) => entry.works.length >= 2)
    .map((entry) => ({ ...entry, works: [...entry.works].sort((a, b) => (a.series?.pos ?? Infinity) - (b.series?.pos ?? Infinity) || a.title.localeCompare(b.title)) }))
}

