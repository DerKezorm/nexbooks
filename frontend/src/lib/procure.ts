/** Small helpers of the fetching pages: the cover of a wished book, the words for a time to wait, a time
 * left, and a language by its name. */
import type { DiscoverCard, ProcureBook } from '../api/client'
import type { CoverWork } from '../components/BookCard'
import type { AcquireBook } from '../components/procure/AcquireDialog'
import { patternOfToken } from './books'

export function coverOfBook(book: ProcureBook): CoverWork {
  return {
    color: book.color || '#8e3b46',
    pattern: patternOfToken(book.book ?? `work-${book.work_id ?? 0}`),
    cover_url: book.cover_url,
    audio_only: false,
    title: book.title,
    authors: book.authors.map((name, index) => ({ id: index, name })),
    series: book.series ? { id: 0, name: book.series, pos: book.series_pos } : null,
  }
}

/** "3 Std", "12 Min", "1 Std 20 Min": the time left of a download, in whole minutes from a minute on. */
export function leftText(seconds: number, hours: string, minutes: string, secondsWord: string): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} ${secondsWord}`
  if (seconds < 5400) return `${Math.round(seconds / 60)} ${minutes}`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m === 60 ? `${h + 1} ${hours}` : `${h} ${hours} ${m} ${minutes}`
}

/** The hours until a moment (rounded up, at least 1): "Nächster Versuch in 3 Std". */
export function hoursUntil(moment: string | null, now: number = Date.now()): number {
  if (!moment) return 1
  const left = new Date(moment).getTime() - now
  return Number.isFinite(left) ? Math.max(1, Math.ceil(left / 3_600_000)) : 1
}

/** A book of the sources as the dialog "Beschaffen" asks for it. */
export function acquireBookOfCard(card: Pick<DiscoverCard, 'token' | 'title' | 'authors' | 'series' | 'series_pos' | 'color' | 'cover_url'>): AcquireBook {
  return { token: card.token, title: card.title, authors: card.authors, series: card.series, series_pos: card.series_pos, color: card.color, cover_url: card.cover_url }
}

