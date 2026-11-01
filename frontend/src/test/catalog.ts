/** Invented catalogues of authors for the tests of the author page. */
import type { AuthorCatalog, AuthorWatch, CatalogEdition, CatalogHit, CatalogWork, PersonOut } from '../api/client'

/** A watch of the author "Edmund Halloran" as the server answers it: the book to come is wished in German. */
export function watchOf(over: Partial<AuthorWatch> = {}): AuthorWatch {
  const keeper = { key: 'keeper', title: 'Der Hüter der verlorenen Zimmer', language: 'de', date: '2027-03-03', state: 'wished' as const, kinds: ['ebook' as const, 'audio' as const] }
  return {
    token: 'cat-token', name: 'Edmund Halloran', person_id: null, photo_url: null, genre: 'krimi', kind: 'both', language: '', languages: ['de', 'en'], target: 'de', language_off: false, more: false, sources_few: false, paused: false, state: 'watching', can_acquire: true,
    created_at: '2026-10-12T09:00:00Z', checked_at: '2026-10-12T06:10:00Z', wished: 2, listed: 0, announced: [keeper], next: keeper, not_yet: [], made: null, ...over,
  }
}

export function edition(language: string, title: string, over: Partial<CatalogEdition> = {}): CatalogEdition {
  return { language, title, year: 2001, released: '', upcoming: false, ebook: true, audio: false, ...over }
}

export function catalogWork(key: string, editions: CatalogEdition[], over: Partial<CatalogWork> = {}): CatalogWork {
  return {
    key, kind: 'roman', why: '', moved: false, pen: '', series: '', series_pos: null, year: editions[0]?.year ?? null,
    orig_language: editions[0]?.language ?? 'en', editions, sources: ['openlibrary', 'dnb'], token: `tok-${key}`, work: null,
    have_ebook: false, have_audio: false, have_languages: [], wished: false, color: '#334455', cover_url: null, pages: 300, minutes: null, ...over,
  }
}

export function catalog(works: CatalogWork[], over: Partial<AuthorCatalog> = {}): AuthorCatalog {
  return {
    token: 'cat-token', name: 'Edmund Halloran', person_id: null, aliases: [], born: '', died: '', orig_language: 'en', languages: ['de', 'en'],
    state: 'ready', refreshing: false, built_at: '2026-10-12T06:10:00Z', sources: [{ source: 'openlibrary', works: works.length }, { source: 'dnb', works: works.length }], away: [],
    sure: works.filter((w) => w.kind !== 'unsure').length, unsure: works.filter((w) => w.kind === 'unsure').length, level: 'high', works_total: works.length,
    on_shelf: works.filter((w) => w.work).length, soon: works.filter((w) => w.editions.some((e) => e.upcoming)).length, first_year: 1974, last_year: 2026,
    can_move: false, genre: 'krimi', works, ...over,
  }
}

/** The catalogue of an author of the libraries before any source was asked: only the books of the person. */
export function catalogOf(person: PersonOut): AuthorCatalog {
  const works = person.wrote.map((brief) =>
    catalogWork(`own-${brief.id}`, [edition('de', brief.title, { year: brief.year })], {
      token: null, work: brief, have_ebook: true, have_languages: ['de'], sources: ['library'], series: brief.series?.name ?? '', series_pos: brief.series?.pos ?? null, orig_language: 'de',
    }),
  )
  return catalog(works, { name: person.name, person_id: person.id, state: 'building', orig_language: 'de', level: 'low' })
}

/** An author with translations, a series, stories, a pen name, a work to come and one that is unsure. */
export function halloran(over: Partial<AuthorCatalog> = {}): AuthorCatalog {
  return catalog(
    [
      catalogWork('salt', [edition('en', 'The Salt Daughters', { year: 1974 }), edition('de', 'Die Salztöchter', { year: 1976, audio: true })], { year: 1974 }),
      catalogWork('soot', [edition('en', 'Soot', { year: 1983 }), edition('de', 'Ruß', { year: 1985 })], { year: 1983 }),
      catalogWork('roses', [edition('en', 'Soot and Roses', { year: 2024 }), edition('de', 'Ruß und Rosen', { year: 2026 })], { kind: 'erz', year: 2024 }),
      catalogWork('ash', [edition('en', 'The Ash Road', { year: 1982 }), edition('de', 'Die Aschenstraße', { year: 1984 })], { series: 'Wrenfield', series_pos: 1, year: 1982 }),
      catalogWork('doors', [edition('en', 'Three Doors', { year: 1987 }), edition('de', 'Drei Türen', { year: 1989 })], { series: 'Wrenfield', series_pos: 2, year: 1987 }),
      catalogWork('tide', [edition('en', 'Tidewater', { year: 2019 })], { year: 2019 }),
      catalogWork('rain', [edition('en', 'Rain Fury', { year: 1977 }), edition('de', 'Regenwut', { year: 1980 })], { pen: 'Richard Vane', year: 1977 }),
      catalogWork(
        'keeper',
        [edition('en', 'The Keeper of Lost Rooms', { year: 2026, released: '2026-11-15', upcoming: true, audio: true }), edition('de', 'Der Hüter der verlorenen Zimmer', { year: 2027, released: '2027-03-03', upcoming: true })],
        { year: 2026 },
      ),
      catalogWork('comic', [edition('en', 'Wrenfield: The Graphic Novel', { year: 2010 }), edition('de', 'Wrenfield. Die Graphic Novel', { year: 2011 })], { kind: 'unsure', why: 'comic', year: 2010 }),
    ],
    { aliases: ['Richard Vane'], ...over },
  )
}

/** A hit of the search of the catalogues (one book with its forms and editions). */
export function catalogHit(token: string | null, over: Partial<CatalogHit> = {}): CatalogHit {
  return {
    key: `key-${token ?? 'none'}`, token, owned_work_id: null, title: 'Nordsee Notizen', language: 'de', allowed: true, languages: ['de', 'en'],
    authors: ['Mara Lindqvist'], year: 2021, series: '', series_pos: null, label: 'work', group: 'match', ebook: true, audio: false,
    procurable: true, why_not: '', cover_url: null, sources: ['openlibrary', 'hardcover'], editions: [], ...over,
  }
}
