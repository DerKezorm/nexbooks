import { describe, expect, it } from 'vitest'

import { followTextStart, type BookLike } from './start'

function book(count: number, mark: { type: string[]; href: string } | null, at: Record<string, number>): BookLike {
  return {
    sections: Array.from({ length: count }, () => ({})),
    landmarks: mark ? [{ type: ['cover'], href: 'Text/titlepage.xhtml' }, mark] : [],
    resolveHref: (href: string) => (href in at ? { index: at[href] } : null),
  }
}

describe('where a book opens the first time', () => {
  it('follows the start of the text a book names near its beginning', () => {
    expect(followTextStart(book(64, { type: ['bodymatter'], href: 'Text/chapter1.xhtml' }, { 'Text/chapter1.xhtml': 3 }))).toBe(true)
    expect(followTextStart(book(64, { type: ['text'], href: 'Text/start.html' }, { 'Text/start.html': 15 }))).toBe(true)
  })

  it('opens at the first page when the named start lies last, after the imprint', () => {
    expect(followTextStart(book(64, { type: ['text'], href: 'Text/cover.html' }, { 'Text/cover.html': 63 }))).toBe(false)
    expect(followTextStart(book(64, { type: ['bodymatter'], href: 'Text/late.html' }, { 'Text/late.html': 16 }))).toBe(false)
  })

  it('opens at the first page when the named start leads nowhere', () => {
    expect(followTextStart(book(10, { type: ['text'], href: 'Text/missing.html' }, {}))).toBe(false)
    const broken: BookLike = { ...book(10, { type: ['text'], href: 'x' }, {}), resolveHref: () => { throw new Error('bad href') } }
    expect(followTextStart(broken)).toBe(false)
  })

  it('lets the reader take its first section when the book names no start', () => {
    expect(followTextStart(book(10, null, {}))).toBe(true)
    expect(followTextStart({ sections: [{}], landmarks: null })).toBe(true)
  })

  it('takes a short book as it is: one section is its own first quarter', () => {
    expect(followTextStart(book(2, { type: ['text'], href: 'a' }, { a: 0 }))).toBe(true)
    expect(followTextStart(book(2, { type: ['text'], href: 'b' }, { b: 1 }))).toBe(false)
  })
})
