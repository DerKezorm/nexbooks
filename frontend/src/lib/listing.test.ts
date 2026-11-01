/** The list view: the order of its rows, where the person is, the circle of an author and the window of rows. */
import type { Mine, WorkRow } from '../api/client'
import { work } from '../test/books'
import { circleColor, firstDirection, initials, seriesOf, sortWorks, standOf, windowRange } from './listing'

const mine = (over: Partial<Mine> = {}): Mine => ({ ebook: null, audio: null, ebook_done: false, audio_done: false, moved_at: null, match: null, libraries: [], ...over })
const row = (id: number, over: Partial<WorkRow> = {}): WorkRow => ({ ...work(id), ...over })
const ids = (list: WorkRow[]) => list.map((w) => w.id)

const books = [
  row(1, { title: 'Zebra', authors: [{ id: 1, name: 'Ada Marwitz' }], year: 2001, pages: 300, added_at: '2026-01-01T00:00:00Z', genre: 'krimi', series: { id: 1, name: 'Deich', pos: 2 } }),
  row(2, { title: 'apfel', authors: [{ id: 2, name: 'Ben Alt' }], year: null, pages: 0, added_at: '2026-03-01T00:00:00Z', genre: 'roman', series: null }),
  row(3, { title: 'Birne 10', authors: [{ id: 3, name: 'Cy Marwitz' }], year: 1999, pages: 500, added_at: '2025-06-01T00:00:00Z', genre: 'roman', series: { id: 1, name: 'Deich', pos: 1 } }),
  row(4, { title: 'Birne 9', authors: [], year: 2010, pages: 100, added_at: '2026-02-01T00:00:00Z', genre: '', series: { id: 2, name: 'Anker', pos: null } }),
]

describe('putting the rows in order', () => {
  it('by title without regard to case, numbers as numbers', () => {
    expect(ids(sortWorks(books, 'title', 'asc', 'de'))).toEqual([2, 4, 3, 1])
    expect(ids(sortWorks(books, 'title', 'desc', 'de'))).toEqual([1, 3, 4, 2])
  })

  it('by the last name of the author; a book with no author stands last in either direction', () => {
    expect(ids(sortWorks(books, 'author', 'asc', 'de'))).toEqual([2, 1, 3, 4])
    expect(ids(sortWorks(books, 'author', 'desc', 'de'))).toEqual([3, 1, 2, 4])
  })

  it('by series and volume; a book of no series stands last in either direction', () => {
    expect(ids(sortWorks(books, 'series', 'asc', 'de'))).toEqual([4, 3, 1, 2])
    expect(ids(sortWorks(books, 'series', 'desc', 'de'))).toEqual([1, 3, 4, 2])
  })

  it('by the genre in the order of the shelf, a book with none last', () => {
    expect(ids(sortWorks(books, 'genre', 'asc', 'de'))).toEqual([2, 3, 1, 4])
  })

  it('by year, size and day added, numbers as numbers, an unknown value last', () => {
    expect(ids(sortWorks(books, 'year', 'asc', 'de'))).toEqual([3, 1, 4, 2])
    expect(ids(sortWorks(books, 'year', 'desc', 'de'))).toEqual([4, 1, 3, 2])
    expect(ids(sortWorks(books, 'size', 'desc', 'de'))).toEqual([3, 1, 4, 2])
    expect(ids(sortWorks(books, 'added', 'desc', 'de'))).toEqual([2, 4, 1, 3])
    expect(ids(sortWorks(books, 'added', 'asc', 'de'))).toEqual([3, 1, 4, 2])
  })

  it('by where the person is, the furthest first, a finished book as 100', () => {
    const list = [row(1, { mine: mine({ ebook: 0.2 }) }), row(2, { mine: mine({ audio: 0.7 }) }), row(3, { mine: mine({ ebook_done: true, ebook: 0.1 }) }), row(4)]
    expect(ids(sortWorks(list, 'mine', 'desc', 'de'))).toEqual([3, 2, 1, 4])
    expect(ids(sortWorks(list, 'mine', 'asc', 'de'))).toEqual([1, 2, 3, 4])
  })

  it('keeps the order of the shelf, or turns it round, and never changes the list it was given', () => {
    const copy = [...books]
    expect(ids(sortWorks(books, 'shelf', 'asc', 'de'))).toEqual([1, 2, 3, 4])
    expect(ids(sortWorks(books, 'shelf', 'desc', 'de'))).toEqual([4, 3, 2, 1])
    sortWorks(books, 'title', 'desc', 'de')
    expect(books).toEqual(copy)
  })

  it('keeps the order of equal rows (the sort is stable)', () => {
    const same = [row(1, { year: 2000, title: 'x' }), row(2, { year: 2000, title: 'x' }), row(3, { year: 2000, title: 'x' })]
    expect(ids(sortWorks(same, 'year', 'desc', 'de'))).toEqual([1, 2, 3])
  })
})

describe('the first direction of a column', () => {
  it('is the natural one: what is newest, most or furthest on top', () => {
    expect(firstDirection('added')).toBe('desc')
    expect(firstDirection('mine')).toBe('desc')
    expect(firstDirection('title')).toBe('asc')
    expect(firstDirection('author')).toBe('asc')
  })
})

describe('where the person is', () => {
  it('is 0 without a place, the furthest of the two otherwise, 100 once one is finished', () => {
    expect(standOf(row(1))).toBe(0)
    expect(standOf(row(1, { mine: mine({ ebook: 0.34, audio: 0.5 }) }))).toBe(50)
    expect(standOf(row(1, { mine: mine({ audio_done: true }) }))).toBe(100)
  })
})

describe('the circle of an author', () => {
  it('has the first letters of the first and the last word of the name', () => {
    expect(initials('Ada Feldkamp')).toBe('AF')
    expect(initials('Mary Ann Smith')).toBe('MS')
    expect(initials('Colette')).toBe('C')
    expect(initials('édith piaf')).toBe('ÉP')
    expect(initials('  ')).toBe('')
  })

  it('has the colour of the genre, a plain one for none', () => {
    expect(circleColor('krimi')).toBe('#3d4752')
    expect(circleColor('nonsense')).toBe(circleColor('none'))
  })
})

describe('the window of rows', () => {
  it('draws the rows near the screen and holds the place of the rest', () => {
    const w = windowRange(2000, 60, 6000, 900)
    expect(w.from).toBe(100 - 8)
    expect(w.to).toBe(115 + 8)
    expect(w.before).toBe(w.from * 60)
    expect(w.after).toBe((2000 - w.to) * 60)
    expect(w.before + (w.to - w.from) * 60 + w.after).toBe(2000 * 60)
  })

  it('starts at the first row and ends at the last, and copes with none and with a position past the end', () => {
    expect(windowRange(2000, 60, 0, 900).from).toBe(0)
    const end = windowRange(30, 60, 99999, 900)
    expect(end.to).toBe(30)
    expect(end.from).toBeLessThanOrEqual(30)
    expect(windowRange(0, 60, 0, 900)).toEqual({ from: 0, to: 0, before: 0, after: 0 })
  })
})

describe('the series of an author', () => {
  it('are those with at least two books here, in the order of the volumes', () => {
    const list = [
      work(1, { title: 'B', series: { id: 7, name: 'Deich', pos: 2 } }),
      work(2, { title: 'A', series: { id: 7, name: 'Deich', pos: 1 } }),
      work(3, { title: 'C', series: { id: 8, name: 'Allein', pos: 1 } }),
      work(4, { title: 'D', series: null }),
    ]
    expect(seriesOf(list).map((s) => [s.name, s.works.map((w) => w.title)])).toEqual([['Deich', ['A', 'B']]])
  })
})
