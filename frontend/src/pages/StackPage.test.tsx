/**
 * The reading stack: the recap of the last year that is over, a tower of lying books for each year as thick as
 * their pages say, the year of the recap that can be changed, the way to a book, and the empty and the failed page.
 */
import { Route, Routes, useLocation } from 'react-router-dom'

import type { StackEntry, StackYear } from '../api/client'
import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { StackPage } from './StackPage'

const server = new Server()
const page = new Page()

let next = 100
function entry(title: string, over: Partial<StackEntry> = {}, brief: Partial<StackEntry['work']> = {}): StackEntry {
  const id = next++
  return {
    id, work_id: id, edition_id: id, kind: 'read', finished_on: '2025-03-01', source: 'hand', created_at: '2025-03-01T10:00:00Z', pages: 280,
    seconds: 0, work: work(id, { title, ...brief }), ...over,
  }
}
function year(number: number, entries: StackEntry[], over: Partial<StackYear> = {}): StackYear {
  const read = entries.filter((e) => e.kind === 'read')
  const heard = entries.filter((e) => e.kind === 'listened')
  const thickest = [...entries].sort((a, b) => b.pages - a.pages)[0]
  return {
    year: number, books: entries.length, read: read.length, listened: heard.length, pages: read.reduce((n, e) => n + e.pages, 0), seconds: heard.reduce((n, e) => n + e.seconds, 0),
    genre: 'krimi', thickest: thickest ? { work: thickest.work, pages: thickest.pages } : null, entries, ...over,
  }
}
const stack = {
  today: '2026-10-14',
  years: [
    year(2024, [entry('Alt', { pages: 100 })], { genre: '' }),
    year(2025, [entry('Erstes', { pages: 560 }), entry('Zweites', { pages: 300 }), entry('Gehört', { kind: 'listened', seconds: 7200 * 3, pages: 220 }, { audio_only: true })]),
    year(2026, [entry('Neu', { pages: 400 })]),
  ],
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
  server.answers = { 'GET /api/v1/stack': stack }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const text = (selector: string) => plain(document.querySelector(selector)!.textContent)

describe('the recap', () => {
  it('is that of the last year that is over, with the numbers the server gave', async () => {
    await page.show(<StackPage />)
    expect(text('[data-testid="rc-books"]')).toBe('3Bücher in 2025')
    expect(text('[data-testid="rc-pages"]')).toBe('860Seiten gelesen')
    expect(text('[data-testid="rc-hours"]')).toBe('6 Stdgehört')
    expect(text('[data-testid="rc-genre"]')).toBe('Krimi & Thrilleram häufigsten')
    expect(text('[data-testid="rc-thickest"]')).toContain('Das dickste Buch des Jahres')
    expect(text('[data-testid="rc-thickest"]')).toContain('Erstes')
    expect(text('[data-testid="rc-thickest"]')).toContain('560 Seiten')
  })

  it('leaves the genre out when no book of the year has one', async () => {
    await page.show(<StackPage />)
    await page.click(document.querySelector('[data-testid="year-2024"]') as HTMLElement)
    expect(document.querySelector('[data-testid="rc-genre"]')).toBeNull()
    expect(text('[data-testid="rc-books"]')).toBe('1Bücher in 2024')
  })

  it('changes to the year that is pressed, and marks it', async () => {
    await page.show(<StackPage />)
    const button = document.querySelector('[data-testid="year-2026"]') as HTMLElement
    expect(button.getAttribute('aria-pressed')).toBe('false')
    await page.click(button)
    expect(text('[data-testid="rc-books"]')).toBe('1Bücher in 2026')
    expect((document.querySelector('[data-testid="year-2026"]') as HTMLElement).getAttribute('aria-pressed')).toBe('true')
    expect((document.querySelector('[data-testid="year-2025"]') as HTMLElement).getAttribute('aria-pressed')).toBe('false')
  })

  it('is in English with the same numbers', async () => {
    await changeLanguage('en', false)
    await page.show(<StackPage />)
    expect(text('[data-testid="rc-books"]')).toBe('3Books in 2025')
    expect(text('[data-testid="rc-hours"]')).toBe('6 hlistened')
  })
})

describe('the towers', () => {
  it('stand year by year, the oldest on the left, with the year and what it came to', async () => {
    await page.show(<StackPage />)
    const years = [...document.querySelectorAll('.year')]
    expect(years.map((y) => y.getAttribute('data-year'))).toEqual(['2024', '2025', '2026'])
    expect(plain(years[1]!.querySelector('p')!.textContent)).toBe('3 Bücher · 860 Seiten6 Std gehört')
    expect(plain(years[2]!.querySelector('h3')!.textContent)).toBe('2026bisher')
    expect(years[1]!.querySelector('h3 small')).toBeNull()
  })

  it('lay the books as thick, as long and as far out as the design does, the first at the bottom, a recording with its ring', async () => {
    await page.show(<StackPage />)
    const lying = [...document.querySelectorAll('.year[data-year="2025"] .lying')] as HTMLElement[]
    expect(lying.map((l) => l.querySelector('span')!.textContent)).toEqual(['Erstes', 'Zweites', 'Gehört'])
    expect(lying[0]!.style.getPropertyValue('--t')).toBe('20px')
    expect(lying[1]!.style.getPropertyValue('--t')).toBe('11px')
    expect(lying[2]!.style.getPropertyValue('--t')).toBe('13px')
    expect(lying[0]!.style.getPropertyValue('--w')).toBe('152px')
    expect(lying[2]!.classList.contains('audio')).toBe(true)
    expect(lying[0]!.classList.contains('audio')).toBe(false)
    expect(lying[0]!.querySelector('em')!.textContent).toBe('Feldkamp')
    // They drop one after the other.
    expect(lying.map((l) => l.style.animationDelay)).toEqual(['0ms', '28ms', '56ms'])
  })

  it('drop at once when the system asks for less motion', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
    await page.show(<StackPage />)
    expect([...document.querySelectorAll('.lying')].every((l) => (l as HTMLElement).style.animationDelay === '0ms')).toBe(true)
  })

  it('open the book over the stack when a lying book is pressed', async () => {
    let seen: { pathname: string; state: { background?: { pathname: string }; work?: { id: number } } } | null = null
    function Spy() {
      seen = useLocation() as unknown as typeof seen
      return null
    }
    await page.show(
      <>
        <Routes>
          <Route path="/stack" element={<StackPage />} />
          <Route path="*" element={<div />} />
        </Routes>
        <Spy />
      </>,
      '/stack',
    )
    const first = document.querySelector('.lying[data-work]') as HTMLElement
    await page.click(first)
    expect(seen!.pathname).toBe(`/work/${first.getAttribute('data-work')}`)
    expect(seen!.state.background!.pathname).toBe('/stack')
    expect(seen!.state.work!.id).toBe(Number(first.getAttribute('data-work')))
  })

  it('name the book, the author and whether it was read or heard for a reader of the screen', async () => {
    await page.show(<StackPage />)
    const labels = [...document.querySelectorAll('.year[data-year="2025"] .lying')].map((l) => l.getAttribute('aria-label'))
    expect(labels[0]).toBe('Erstes, Ada Feldkamp, gelesen')
    expect(labels[2]).toBe('Gehört, Ada Feldkamp, gehört')
  })
})

describe('the page without books and the page that failed', () => {
  it('says how a book comes onto the stack', async () => {
    server.answers = { 'GET /api/v1/stack': { years: [], today: '2026-10-14' } }
    server.install()
    await page.show(<StackPage />)
    expect(text('[data-testid="stack-empty"]')).toContain('sobald du Bücher gelesen oder gehört hast')
    expect(text('[data-testid="stack-hint"]')).toContain('Ein Buch zählt als beendet, wenn du bis zum Ende liest oder hörst.')
    expect(document.querySelector('#recap')).toBeNull()
    expect(document.querySelector('#stacks')).toBeNull()
  })

  it('says what went wrong', async () => {
    server.fails = { 'GET /api/v1/stack': { status: 500, code: 'internal_error' } }
    server.install()
    await page.show(<StackPage />)
    expect(document.querySelector('[role="alert"]')).toBeTruthy()
    expect(document.querySelector('.lying')).toBeNull()
  })
})
