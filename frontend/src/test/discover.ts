/** Books of the sources for the tests of Discover: invented, in the shape the server answers. */
import type { BookPage, DiscoverCard, DiscoverList, ForYou, SeriesGap, Volume, WorkBrief } from '../api/client'

export function card(token: string, over: Partial<DiscoverCard> = {}): DiscoverCard {
  return {
    token,
    title: `Fremdes Buch ${token}`,
    authors: ['Mara Feldkamp'],
    rank: null,
    kinds: ['ebook'],
    forms: [{ kind: 'ebook', language: 'de', variant: '' }],
    language: 'de',
    released: '2026-05-02',
    upcoming: false,
    genre: 'roman',
    color: '#7d2f2b',
    cover_url: null,
    sources: ['apple'],
    owned_work_id: null,
    wished: false,
    series: '',
    series_pos: null,
    ...over,
  }
}

export function list(items: DiscoverCard[], over: Partial<DiscoverList> = {}): DiscoverList {
  return {
    list: 'best',
    market: 'de',
    format: 'all',
    language: '',
    genre: '',
    hide_owned: true,
    state: 'ready',
    availability: [
      { source: 'apple', state: 'ready' },
      { source: 'audible', state: 'ready' },
    ],
    asking: true,
    sources: ['apple', 'audible'],
    fetched_at: '2026-10-12T09:00:00Z',
    languages: [
      { code: 'de', count: 3 },
      { code: 'en', count: 1 },
    ],
    markets: ['de', 'us', 'uk', 'fr', 'ca'],
    total: items.length,
    owned: 0,
    items,
    ...over,
  }
}

export function volume(pos: number, state: Volume['state'], over: { work?: WorkBrief; book?: DiscoverCard } = {}): Volume {
  return { pos, state, work: over.work ?? null, book: over.book ?? null }
}

export function gap(over: Partial<SeriesGap> = {}): SeriesGap {
  return { series_id: 3, name: 'Die Chroniken von Arvel', author: 'Iris Quist', have: 1, out: 3, missing: 2, soon: 1, pending: false, volumes: [], ...over }
}

export function forYou(over: Partial<ForYou> = {}): ForYou {
  return { state: 'ready', sources: ['hardcover', 'audible'], pending: false, gaps: [], authors: [], ...over }
}

export function bookPage(token: string, over: Partial<BookPage> = {}): BookPage {
  return {
    ...card(token),
    description: 'Eine erfundene Geschichte über ein Haus am Wasser.',
    year: 2026,
    forms_known: [{ kind: 'ebook', language: 'de', variant: '', minutes: null, narrators: [], pages: 320, source: 'apple' }],
    volumes: [],
    series_pending: false,
    more: [],
    pending: false,
    ...over,
  }
}
