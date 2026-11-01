/**
 * What the pages of Discover share: a book of a source seen as something a cover can be drawn from, the words of a day,
 * the name of the sources behind a list, and the order of things in a row.
 */
import type { TFunction } from 'i18next'

import type { DiscoverCard, DiscoverListName, SourceId, WorkBrief } from '../api/client'
import type { CoverWork } from '../components/BookCard'
import { patternOfToken } from './books'

/** A book of a source as a cover is drawn from it: its colour, its pattern (fixed by its token), its picture or none. */
export function coverOf(card: Pick<DiscoverCard, 'token' | 'title' | 'authors' | 'color' | 'cover_url' | 'series' | 'series_pos' | 'kinds'> & { pattern?: number }): CoverWork {
  return {
    color: card.color,
    pattern: card.pattern ?? patternOfToken(card.token),
    cover_url: card.cover_url,
    audio_only: card.kinds.length > 0 && !card.kinds.includes('ebook'),
    title: card.title,
    authors: card.authors.map((name, index) => ({ id: index, name })),
    series: card.series ? { id: 0, name: card.series, pos: card.series_pos } : null,
  }
}

/** A day (`2026-10-12`) as a short date for the cards ("12. Okt."), or the long one for the page of a book. */
export function dayText(day: string, language: string, long = false): string {
  const parsed = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!parsed) return day
  const date = new Date(Date.UTC(Number(parsed[1]), Number(parsed[2]) - 1, Number(parsed[3]), 12))
  try {
    return new Intl.DateTimeFormat(language, long ? { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' } : { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date)
  } catch {
    return day
  }
}

/** The names of some sources as one phrase ("Apple Books Charts und Audible"). */
export function sourceNames(sources: SourceId[], t: TFunction, language: string): string {
  const names = sources.map((id) => t(`discover.sourceName.${id}`))
  try {
    return new Intl.ListFormat(language, { style: 'long', type: 'conjunction' }).format(names)
  } catch {
    return names.join(', ')
  }
}

/** Whether a list carries numbers on its covers. */
export function ranked(list: DiscoverListName): boolean {
  return list === 'best' || list === 'trending'
}

/** The markets of the head, in their order (the others the server offers follow). */
export function marketOrder(markets: string[]): string[] {
  const first = ['de', 'us', 'uk', 'fr']
  return [...first.filter((m) => markets.includes(m)), ...markets.filter((m) => !first.includes(m))]
}

/** A book of a library as a card of Discover (the volumes of a series the person has stand beside the ones they lack). */
export function cardOfWork(work: WorkBrief): DiscoverCard & { pattern: number } {
  const forms = work.editions.map((edition) => ({ kind: edition.kind, language: edition.language, variant: edition.variant }))
  return {
    token: `work-${work.id}`,
    title: work.title,
    authors: work.authors.map((person) => person.name),
    rank: null,
    kinds: [...new Set(work.editions.map((edition) => edition.kind))],
    forms,
    language: work.editions[0]?.language ?? '',
    released: '',
    upcoming: false,
    genre: work.genre,
    color: work.color,
    cover_url: work.cover_url,
    sources: [],
    owned_work_id: work.id,
    wished: false,
    series: work.series?.name ?? '',
    series_pos: work.series?.pos ?? null,
    pattern: work.pattern,
  }
}
