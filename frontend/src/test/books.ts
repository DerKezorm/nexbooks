/** Books for the tests of the shelf and the page of a book: invented, in the shape the server answers. */
import type { EditionBrief, EditionOut, Library, WorkBrief, WorkOut } from '../api/client'

export function ebook(id: number, over: Partial<EditionBrief> = {}): EditionBrief {
  return { id, library_id: 1, kind: 'ebook', variant: '', variant_uncertain: false, language: 'de', language_guess: false, formats: ['epub'], pages: 300, duration: 0, narrators: [], ...over }
}

export function recording(id: number, over: Partial<EditionBrief> = {}): EditionBrief {
  return { id, library_id: 1, kind: 'audio', variant: 'unabridged', variant_uncertain: false, language: 'de', language_guess: false, formats: ['m4b'], pages: 0, duration: 36_000, narrators: ['Ben Hollister'], ...over }
}

export function work(id: number, over: Partial<WorkBrief> = {}): WorkBrief {
  return {
    id, title: `Buch ${id}`, authors: [{ id: 1, name: 'Ada Feldkamp' }], series: null, genre: 'roman', genre_guess: false, color: '#7d2f2b', cover_url: null, pages: 300,
    year: 2020, pattern: id % 5, tall: 0.5, gold: false, audio_only: false, added_at: '2026-10-01T10:00:00Z', editions: [ebook(id)], ...over,
  }
}

export function edition(brief: EditionBrief, over: Partial<EditionOut> = {}): EditionOut {
  const e = brief.kind === 'ebook'
  return {
    ...brief, title: 'Buch', subtitle: '', isbn: '', asin: '', publisher: '', published: '2020', library_name: 'Familie', translators: [], authors: ['Ada Feldkamp'],
    series_name: '', series_pos: null, genre: 'roman', genre_guess: false, corrected: [], matched_by: [],
    files: (e ? brief.formats : brief.formats).map((format, index) => ({ id: brief.id * 10 + index, format, path: `buch.${format}`, size: 1_200_000, part: index + 1, duration: 0, sha256: 'a'.repeat(64), protected: false })),
    chapters: [], ...over,
  }
}

export function workOut(id: number, editions: EditionBrief[], over: Partial<WorkOut> = {}): WorkOut {
  const brief = work(id, { editions })
  return { ...brief, description: 'Eine erfundene Geschichte.', original_title: '', original_language: '', editions: editions.map((brief) => edition(brief)), duplicates: [], ...over } as WorkOut
}

export function library(over: Partial<Library> = {}): Library {
  return { id: 1, name: 'Familie', kind: 'both', my_right: 'read', state: 'idle', counts: { ebooks: 3, audiobooks: 1, files: 4, bytes: 1000, problems: 0, review: 0, entries: 0, pending: 0 }, progress: { done: 0, total: 0 }, matching: 'idle', resting: [], ...over }
}
