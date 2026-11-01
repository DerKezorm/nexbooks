/** Wishes, downloads and problems for the tests of the fetching pages: invented, in the shape the server answers. */
import type { BlockLine, BookOptions, ForeignJobOut, HistoryLine, ProblemOut, ProcureBook, ProcureCounts, RunningOut, WishOut } from '../api/client'

export function book(over: Partial<ProcureBook> = {}): ProcureBook {
  return { work_id: null, book: 'tokabc', title: 'Glas und Asche', authors: ['Iris Quist'], series: 'Die Chroniken von Arvel', series_pos: 2, genre: 'fantasy', color: '#4f2b47', cover_url: null, ...over }
}

export function wish(id: number, over: Partial<WishOut> = {}): WishOut {
  return {
    id, kind: 'ebook', language: 'de', state: 'searching', detail: null, library: { id: 1, name: 'Bücher' }, by: 'Mira', mine: true, created_at: new Date(Date.now() - 3 * 60_000).toISOString(),
    searched_at: null, next_try_at: null, release_date: '', waiting_for_a_place: false, handing_over_again: false, book: book({ book: `tok${id}`, title: `Wunschbuch ${id}` }), ...over,
  }
}

export function running(id: number, over: Partial<RunningOut> = {}): RunningOut {
  return {
    id, book: book({ book: `run${id}`, title: `Ladebuch ${id}` }), kind: 'audio', language: 'de', release: 'Iris.Quist.-.Glas.und.Asche.GERMAN.M4B-EXAMPLE', protocol: 'usenet', client: 'SABnzbd', indexer: 'Bücherei', format: 'm4b_aac',
    size: 412_000_000, phase: 'downloading', progress: 42, remaining_seconds: 180, speed: 9_800_000, stalled: false, seed_ratio: null, goal_ratio: null, seed_seconds: null, goal_seconds: null, library: { id: 2, name: 'Hörbücher' },
    by: 'Mira', mine: true, forced: [], created_at: new Date(Date.now() - 20 * 60_000).toISOString(), ...over,
  }
}

export function problem(id: number, over: Partial<ProblemOut> = {}): ProblemOut {
  return { id, book: book({ book: `pro${id}`, title: `Problembuch ${id}` }), kind: 'ebook', release: 'Iris.Quist.-.Glas.und.Asche.GERMAN.EPUB-EXAMPLE', code: 'path_not_found', values: {}, client: 'SABnzbd', library: { id: 1, name: 'Bücher' }, at: new Date(Date.now() - 70 * 60_000).toISOString(), can_map: false, protocol: 'usenet', ...over }
}

export function foreign(id: number, over: Partial<ForeignJobOut> = {}): ForeignJobOut {
  return { id, name: 'Unbekannter.Autor.Unbekanntes.Buch.2019.GERMAN.EPUB-EXAMPLE', client: 'SABnzbd', state: 'downloading', first_seen_at: new Date(Date.now() - 9 * 3_600_000).toISOString(), ...over }
}

export function line(id: number, over: Partial<HistoryLine> = {}): HistoryLine {
  return { id, book_title: `Altes Buch ${id}`, book_author: 'Ada Feldkamp', work_id: null, book: null, kind: 'ebook', release: 'Ada.Feldkamp.-.Altes.Buch.GERMAN.EPUB-EXAMPLE', indexer: 'Bücherei', result: 'done', note: null, created_at: new Date(Date.now() - 4 * 60_000).toISOString(), ...over }
}

export function counts(over: Partial<ProcureCounts> = {}): ProcureCounts {
  return { allowed: true, seeking: 0, running: 0, needs: 0, blocklist: 0, automatic: true, indexers: 2, clients_down: [], ...over }
}

export function options(over: Partial<BookOptions> = {}): BookOptions {
  const kind = { state: 'free' as const, wish_id: null, have: null, goal: 'epub', libraries: [{ id: 1, name: 'Bücher', writable: true, write_problem: '' }, { id: 2, name: 'Familie', writable: true, write_problem: '' }], default_library: 1, takes: ['epub', 'azw3', 'mobi', 'pdf'], abridged: false }
  return {
    allowed: true, kinds: [{ ...kind, kind: 'ebook' }, { ...kind, kind: 'audio', goal: 'flac', takes: ['flac', 'm4b_aac', 'mp3_320', 'mp3_192'] }], original_language: 'en', languages: ['de', 'en'],
    release_date: '', automatic: true, indexers: 2, ...over,
  }
}

export function blockLine(id: number, over: Partial<BlockLine> = {}): BlockLine {
  return { id, title: 'Iris.Quist.-.Glas.und.Asche.ENGLISH.EPUB-EXAMPLE', kind: 'ebook', protocol: 'usenet', indexer: 'Bücherei', reason: 'abridged', by: 'Tom', created_at: new Date(Date.now() - 3 * 3_600_000).toISOString(), work_id: null, book: 'tokabc', book_title: 'Glas und Asche', book_author: 'Iris Quist', ...over }
}
