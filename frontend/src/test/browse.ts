/** The shelves of Browse for the tests: invented books in the shape the server answers. */
import type { BrowseWall, PersonalRow, ShelfCard, ShelfPageOut, ShelfSection, ShelfStrip } from '../api/client'
import { card } from './discover'

export function shelfCard(token: string, over: Partial<ShelfCard> = {}): ShelfCard {
  return {
    ...card(token),
    award: '',
    topics: [],
    pages: null,
    minutes: null,
    year: 2026,
    blurb: '',
    ...over,
  }
}

const NAMES = ['krimi', 'romane', 'fantasy', 'scifi', 'klassiker', 'bio', 'geschichte', 'natur', 'gesellschaft', 'leben', 'reisen', 'kinder', 'jugend']
const DEPTS: Record<string, 'bel' | 'sach' | 'kj'> = {
  krimi: 'bel',
  romane: 'bel',
  fantasy: 'bel',
  scifi: 'bel',
  klassiker: 'bel',
  bio: 'sach',
  geschichte: 'sach',
  natur: 'sach',
  gesellschaft: 'sach',
  leben: 'sach',
  reisen: 'sach',
  kinder: 'kj',
  jugend: 'kj',
}

export function strip(shelf: string, items: ShelfCard[], over: Partial<ShelfStrip> = {}): ShelfStrip {
  return {
    shelf,
    section: 'new',
    sections: [
      { key: 'new', count: items.length },
      { key: 'popular', count: 12 },
      { key: 'classic', count: 6 },
    ],
    items,
    ...over,
  }
}

export function wall(strips: ShelfStrip[], over: Partial<BrowseWall> = {}): BrowseWall {
  return {
    state: 'ready',
    availability: [
      { source: 'apple', state: 'ready' },
      { source: 'openlibrary', state: 'ready' },
    ],
    asking: true,
    pending: false,
    sources: ['apple', 'openlibrary'],
    fetched_at: '2026-10-12T09:00:00Z',
    market: 'de',
    markets: ['de', 'us', 'uk', 'fr', 'ca'],
    hide_owned: true,
    departments: (['bel', 'sach', 'kj'] as const).map((key) => ({
      key,
      shelves: NAMES.filter((name) => DEPTS[name] === key).map((name) => ({
        key: name,
        books: 19,
        new: name === 'krimi' ? 5 : 0,
        fan: [card(`${name}-1`), card(`${name}-2`), card(`${name}-3`)],
      })),
    })),
    strips,
    ...over,
  }
}

export function shelfPage(items: ShelfCard[], over: Partial<ShelfPageOut> = {}): ShelfPageOut {
  const sections: { key: ShelfSection; count: number }[] = [
    { key: 'new', count: 5 },
    { key: 'popular', count: 13 },
    { key: 'award', count: 2 },
    { key: 'classic', count: 6 },
  ]
  return {
    state: 'ready',
    availability: [
      { source: 'apple', state: 'ready' },
      { source: 'openlibrary', state: 'ready' },
    ],
    asking: true,
    pending: false,
    sources: ['apple', 'openlibrary'],
    fetched_at: '2026-10-12T09:00:00Z',
    shelf: 'krimi',
    dept: 'bel',
    market: 'de',
    markets: ['de', 'us'],
    hide_owned: true,
    section: null,
    language: '',
    format: 'all',
    years: '',
    topic: '',
    sort: 'pop',
    total: items.length,
    owned: 0,
    offset: 0,
    limit: 60,
    sections,
    languages: [{ key: 'de', count: 12 }],
    topics: [
      { key: 'ermittler', count: 7 },
      { key: 'cosy', count: 2 },
    ],
    year_bounds: { new_from: 2025, mid_from: 2010 },
    items,
    ...over,
  }
}

export function personalRow(key: PersonalRow['key'], items: ShelfCard[], over: Partial<PersonalRow> = {}): PersonalRow {
  return { key, author: '', genre: '', reads: [], items, ...over }
}
