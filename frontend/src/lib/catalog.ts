/**
 * The page of an author: which works the filters show, in which
 * sections, and what a work is in the language chosen. Pure functions, so that the rules are tested apart from the page.
 *
 * * The language is the person's own from the start (when a work came out in it), else the author's; "Alle Sprachen" shows
 *   every work in its original language. A work that never came out in the language chosen is left out and counted.
 * * "Nach Art": a section for each series of two works or more, then one for each kind, the pen names, and last "Nicht sicher
 *   zugeordnet".
 *   "Nach Jahr": a section for each decade, newest first, and "Nicht sicher zugeordnet" last.
 * * A book of the person's (the server says which, from their own libraries) is always shown, whatever language it is in.
 */
import type { AuthorCatalog, AuthorHit, CatalogEdition, CatalogKind, CatalogWork } from '../api/client'
import type { CoverWork } from '../components/BookCard'
import { patternOfToken } from './books'

export type Movable = Exclude<CatalogKind, 'unsure'>
export const KIND_ORDER: Movable[] = ['roman', 'erz', 'bio', 'sach', 'lyrik', 'kinder', 'box']
export const ALL = 'all'
/** The most authors the group of a search shows. */
export const MAX_PEOPLE = 4

export type Order = 'kind' | 'year'
export type Look = 'covers' | 'list'
export type AuthorFilter = { language: string; order: Order; look: Look; hideOwned: boolean }

export type Section =
  | { id: string; type: 'series'; name: string; works: CatalogWork[]; hidden: number }
  | { id: string; type: 'kind'; kind: Movable; works: CatalogWork[] }
  | { id: string; type: 'pen'; name: string; works: CatalogWork[] }
  | { id: string; type: 'decade'; decade: number; works: CatalogWork[] }
  | { id: string; type: 'unsure'; works: CatalogWork[] }

export const owned = (work: CatalogWork): boolean => work.work !== null

/** The work in a language: its edition there; for "all" the edition in its original language (or the first). A book of the
 * person's stands in every language its edition does not say. */
export function editionIn(work: CatalogWork, language: string): CatalogEdition | null {
  if (language === ALL) return work.editions.find((edition) => edition.language === work.orig_language) ?? work.editions[0] ?? null
  const found = work.editions.find((edition) => edition.language === language)
  if (found) return found
  if (owned(work) && work.editions.length > 0 && (work.have_languages.includes(language) || work.editions.every((edition) => !edition.language))) {
    return work.editions[0]!
  }
  return null
}

/** The languages to choose from: the person's own first, the author's, the others by how many works came out in them, and
 * "all". Only languages a work came out in. */
export function languageOptions(catalog: Pick<AuthorCatalog, 'works' | 'orig_language'>, mine: string): string[] {
  const counts = new Map<string, number>()
  for (const work of catalog.works) for (const edition of work.editions) if (edition.language) counts.set(edition.language, (counts.get(edition.language) ?? 0) + 1)
  const rest = [...counts.keys()].filter((code) => code !== mine && code !== catalog.orig_language).sort((a, b) => (counts.get(b) ?? 0) - (counts.get(a) ?? 0) || a.localeCompare(b))
  const order = [mine, catalog.orig_language, ...rest].filter((code, index, list) => code && counts.has(code) && list.indexOf(code) === index)
  return [...order, ALL]
}

export function defaultLanguage(catalog: Pick<AuthorCatalog, 'works' | 'orig_language'>, mine: string): string {
  return languageOptions(catalog, mine)[0] ?? ALL
}

const yearOf = (work: CatalogWork, language: string): number | null => {
  const edition = editionIn(work, language)
  const released = edition?.released ? Number(edition.released.slice(0, 4)) : null
  return edition?.year ?? released ?? work.year
}

const byDate = (language: string) => (a: CatalogWork, b: CatalogWork) => (yearOf(a, language) ?? 9999) - (yearOf(b, language) ?? 9999) || a.key.localeCompare(b.key)

/** The works the filters show, and what they leave out: the works that never came out in the language, and the person's own
 * when those are hidden. */
export function visible(works: CatalogWork[], filter: Pick<AuthorFilter, 'language' | 'hideOwned'>): { shown: CatalogWork[]; hiddenLanguage: number; hiddenOwned: number } {
  const inLanguage = works.filter((work) => editionIn(work, filter.language) !== null)
  const shown = filter.hideOwned ? inLanguage.filter((work) => !owned(work)) : inLanguage
  return { shown, hiddenLanguage: works.length - inLanguage.length, hiddenOwned: inLanguage.length - shown.length }
}

export function sections(works: CatalogWork[], filter: AuthorFilter): Section[] {
  const { shown } = visible(works, filter)
  const sorted = [...shown].sort(byDate(filter.language))
  const unsure = sorted.filter((work) => work.kind === 'unsure')
  const pen = sorted.filter((work) => work.pen && work.kind !== 'unsure')
  const main = sorted.filter((work) => !work.pen && work.kind !== 'unsure')
  const out: Section[] = []
  if (filter.order === 'kind') {
    // A series is a section from two of its works on (counted over the whole catalogue, whatever the filters show);
    // a work alone in its series stands under its kind.
    const everyOne = (name: string) => works.filter((work) => work.series === name && !work.pen && work.kind !== 'unsure').length
    const inSeries = (work: CatalogWork) => !!work.series && everyOne(work.series) >= 2
    const series = new Map<string, CatalogWork[]>()
    for (const work of main) if (inSeries(work)) series.set(work.series, [...(series.get(work.series) ?? []), work])
    for (const [name, list] of series) {
      out.push({ id: `s:${name}`, type: 'series', name, works: [...list].sort((a, b) => (a.series_pos ?? 9999) - (b.series_pos ?? 9999)), hidden: everyOne(name) - list.length })
    }
    for (const kind of KIND_ORDER) {
      const list = main.filter((work) => !inSeries(work) && work.kind === kind)
      if (list.length) out.push({ id: `k:${kind}`, type: 'kind', kind, works: list })
    }
    const pens = new Map<string, CatalogWork[]>()
    for (const work of pen) pens.set(work.pen, [...(pens.get(work.pen) ?? []), work])
    for (const [name, list] of pens) out.push({ id: `p:${name}`, type: 'pen', name, works: list })
  } else {
    const decades = new Map<number, CatalogWork[]>()
    for (const work of [...main, ...pen]) {
      const year = yearOf(work, filter.language)
      const decade = year ? Math.floor(year / 10) * 10 : 0
      decades.set(decade, [...(decades.get(decade) ?? []), work])
    }
    for (const decade of [...decades.keys()].sort((a, b) => b - a)) {
      out.push({ id: `d:${decade}`, type: 'decade', decade, works: [...decades.get(decade)!].sort(byDate(filter.language)).reverse() })
    }
  }
  if (unsure.length) out.push({ id: 'unsure', type: 'unsure', works: unsure })
  return out
}

/** The works of a section "Alle fehlenden beschaffen" takes: sure ones the person does not have, nobody asked for and that
 * came out (or come out) in the language. `asked` says whether a work is wished or on its way already. A work a book on the
 * shelf may be (`maybe_have`) is left out: a person looks first. */
export function missing(works: CatalogWork[], language: string, asked: (work: CatalogWork) => boolean): CatalogWork[] {
  return works.filter((work) => work.kind !== 'unsure' && work.token !== null && !owned(work) && !work.maybe_have && !asked(work) && editionIn(work, language) !== null)
}

/** The works announced, with a day (or a year) after today in some language. */
export function soon(works: CatalogWork[]): CatalogWork[] {
  return works.filter((work) => work.kind !== 'unsure' && work.editions.some((edition) => edition.upcoming))
}

export type Mark = 'have' | 'want' | 'avail' | 'none'

/** What a mark of a kind says of a work: on the person's shelf, wished, known to exist in the language, or not known. */
export function markOf(work: CatalogWork, edition: CatalogEdition | null, kind: 'ebook' | 'audio', wished: boolean): Mark {
  if (kind === 'ebook' ? work.have_ebook : work.have_audio) return 'have'
  if (wished) return 'want'
  if (edition && edition[kind]) return 'avail'
  return 'none'
}

/** The original title of a work, when the edition shown has another one. */
export function originalTitle(work: CatalogWork, edition: CatalogEdition | null): string {
  const original = work.editions.find((item) => item.language === work.orig_language)
  return original && edition && original.title !== edition.title ? original.title : ''
}

/** The years the works came out in, for "1974 bis 2026". */
export function yearsOf(catalog: Pick<AuthorCatalog, 'first_year' | 'last_year'>, thisYear: number): [number, number] | null {
  if (!catalog.first_year) return null
  return [catalog.first_year, Math.min(thisYear, catalog.last_year ?? catalog.first_year)]
}

/** Where the page of an author is: the person of the caller's libraries, else the catalogue by its token. */
export function authorHref(hit: Pick<AuthorHit, 'person_id' | 'token'>): string | null {
  if (hit.person_id !== null) return `/person/${hit.person_id}`
  return hit.token ? `/author/${encodeURIComponent(hit.token)}` : null
}

/** The cover of a work: the person's own book's, else one drawn from the work (or the picture of its source). */
export function coverOfWork(work: CatalogWork, edition: CatalogEdition | null, author: string): CoverWork {
  if (work.work) return work.work
  return {
    color: work.color || '#5b4a3a',
    pattern: patternOfToken(work.token ?? work.key),
    cover_url: work.cover_url,
    audio_only: !!edition && edition.audio && !edition.ebook,
    title: edition?.title ?? work.editions[0]?.title ?? '',
    authors: [{ id: 0, name: work.pen || author }],
    series: work.series ? { id: 0, name: work.series, pos: work.series_pos } : null,
  }
}

/** The authors of the person's libraries first, then those of the catalogues that are not among them. */
export function peopleOf(mine: AuthorHit[], theirs: AuthorHit[]): AuthorHit[] {
  const out = [...mine]
  for (const hit of theirs) {
    if (!out.some((item) => (hit.person_id !== null && item.person_id === hit.person_id) || item.name.toLocaleLowerCase() === hit.name.toLocaleLowerCase())) out.push(hit)
  }
  return out.slice(0, MAX_PEOPLE)
}

/** How often the page asks again for a catalogue that is being built: every few seconds at first, then less often (an
 * author far back in the queue of the background), until it is there. */
const POLL_MS = 4000
const POLL_MAX_MS = 30000

/** The wait before the next question while a catalogue is being built: 4 s for the first minute, then growing to 30 s. */
export function pollDelay(polls: number): number {
  return polls < 15 ? POLL_MS : Math.min(POLL_MAX_MS, POLL_MS * 1.25 ** (polls - 14))
}
