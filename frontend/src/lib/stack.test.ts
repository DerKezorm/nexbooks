/** The numbers of the reading stack are the design's. */
import type { StackEntry, StackYear } from '../api/client'
import { work } from '../test/books'
import { hours, lyingOffset, lyingThickness, lyingWidth, recapYear } from './stack'

const entry = (id: number, pages: number, over: Partial<StackEntry['work']> = {}): Pick<StackEntry, 'id' | 'pages' | 'work'> => ({ id, pages, work: work(id, over) })

describe('the thickness of a lying book', () => {
  it('is the pages over 28, at least 8 and at most 22 pixels', () => {
    expect(lyingThickness(entry(1, 280))).toBe(10)
    expect(lyingThickness(entry(1, 100))).toBe(8)
    expect(lyingThickness(entry(1, 1000))).toBe(22)
    expect(lyingThickness(entry(1, 28 * 14))).toBe(14)
  })

  it('is 13 pixels for a recording alone', () => {
    expect(lyingThickness(entry(1, 900, { audio_only: true }))).toBe(13)
  })
})

describe('the length of a lying book', () => {
  it('is 124 pixels plus the height of its spine times 56', () => {
    expect(lyingWidth(entry(1, 100, { tall: 0 }))).toBe(124)
    expect(lyingWidth(entry(1, 100, { tall: 1 }))).toBe(180)
    expect(lyingWidth(entry(1, 100, { tall: 0.5 }))).toBe(152)
  })
})

describe('the offset of a lying book', () => {
  it('stays within 11 pixels either way and is the same every time', () => {
    for (let id = 1; id < 500; id++) {
      const off = lyingOffset({ id })
      expect(Math.abs(off)).toBeLessThanOrEqual(11)
      expect(lyingOffset({ id })).toBe(off)
    }
    expect(new Set(Array.from({ length: 60 }, (_, id) => lyingOffset({ id: id + 1 }))).size).toBeGreaterThan(10)
  })
})

describe('the year of the recap', () => {
  const years = [2023, 2024, 2025, 2026].map((year) => ({ year })) as StackYear[]
  it('is the last year that is over', () => {
    expect(recapYear(years, '2026-10-14')).toBe(2025)
    expect(recapYear(years, '2027-01-02')).toBe(2026)
  })
  it('is the newest year when none is over yet, and none without books', () => {
    expect(recapYear([{ year: 2026 }] as StackYear[], '2026-10-14')).toBe(2026)
    expect(recapYear([], '2026-10-14')).toBeNull()
  })
  it('skips a year that has no books', () => {
    expect(recapYear([{ year: 2022 }, { year: 2026 }] as StackYear[], '2026-10-14')).toBe(2022)
  })
})

describe('the hours', () => {
  it('are rounded', () => {
    expect(hours(0)).toBe(0)
    expect(hours(5400)).toBe(2)
    expect(hours(5399)).toBe(1)
  })
})
