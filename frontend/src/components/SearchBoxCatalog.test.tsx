/**
 * The hits of the catalogues in the quick search of the head: a grid the field controls (the arrows up and down go from hit to
 * hit, left and right reach "E-Book", "Hörbuch" and "Beides", Enter presses what they stand on), only a book offers them, a
 * hit's page is made when it is opened, an older answer never replaces a newer one, a search left behind while the sources
 * are still asked is stopped on the server, and a search the server slowed down says so.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { catalogHit } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { counts, options } from '../test/procure'
import { idle } from '../test/wait'
import { SearchBox } from './SearchBox'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'member', avatar: null, version: '0.1.0', whats_new_seen: '0.1.0', profile: { theme: 'light', view: 'shelf' } } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()
const KEY = 'a'.repeat(20)
const BOOK = catalogHit(null, { key: KEY })
const PLAY = catalogHit(null, { key: 'b'.repeat(20), title: 'Nordsee Notizen', label: 'drama', group: 'related', procurable: false, why_not: 'not_a_book' })
const SERIES = catalogHit(null, { key: 'c'.repeat(20), title: 'Nordlicht', series: 'Nordlicht', procurable: false, why_not: 'volume_unknown' })

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/procure/counts': counts(),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/libraries': [],
    'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }] },
    'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: [] },
    'GET /api/v1/search?q=nordsee&limit=8': { query: 'nordsee', items: [] },
    'POST /api/v1/search/catalog': { available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [BOOK, PLAY, SERIES], people: [] },
    [`POST /api/v1/search/catalog/ticket-0001/books`]: { token: 'tok-made' },
    'GET /api/v1/procure/book-options?book=tok-made': options(),
    'DELETE /api/v1/search/catalog/ticket-0001': 204,
    ...over,
  }
  server.fails = {}
  server.install()
}

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname}</div>
}

function app() {
  return (
    <WishProvider>
      <ProcureProvider>
        <SearchBox />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </ProcureProvider>
    </WishProvider>
  )
}

const q = (selector: string) => document.querySelector(selector) as HTMLElement | null
const all = (selector: string) => [...document.querySelectorAll(selector)] as HTMLElement[]
const field = () => q('#q') as HTMLInputElement

async function press(key: string, times = 1) {
  await act(async () => {
    for (let time = 0; time < times; time++) field().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  })
  await idle()
}

/** Types without waiting for the fake server to be idle (an answer is held back). */
async function setText(value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field()), 'value')!.set!
  await act(async () => {
    setter.call(field(), value)
    field().dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function typeAndWait(text: string, ms = 900) {
  await page.type(field(), text)
  await act(async () => void (await vi.advanceTimersByTimeAsync(ms)))
  await idle()
}

/** The server takes its time over asking again by the ticket (the sources are still asked): it answers only when the page
 * gives up. Resolves once the page is waiting for it. */
async function holdAskingAgain(type: string) {
  const fake = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
  let held = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/v1/search/catalog/ticket-0001' && (init?.method ?? 'GET') === 'GET') {
        held += 1
        return new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('gave up', 'AbortError'))))
      }
      return fake(url, init)
    }),
  )
  await act(async () => field().focus())
  await setText(type)
  await act(async () => void (await vi.advanceTimersByTimeAsync(750)))
  await vi.waitFor(() => expect(held).toBeGreaterThan(0))
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the hits of the catalogues in the quick search', () => {
  it('are rows of a grid with cells, never buttons inside an option', async () => {
    serve()
    await page.show(app())
    await typeAndWait('nord')
    const grid = q('[role="grid"]')!
    expect(field().getAttribute('aria-controls')).toBe(grid.id)
    expect(all('[data-testid="catalog-hit"]').every((row) => row.getAttribute('role') === 'row')).toBe(true)
    expect(grid.querySelectorAll('[role="option"], [role="button"], button')).toHaveLength(0)
    expect(all('[data-testid="catalog-hit"]')[0]!.querySelectorAll('[role="gridcell"]')).toHaveLength(4)
  })

  it('reach "E-Book", "Hörbuch" and "Beides" with the arrows, and Enter opens the dialog with the one chosen', async () => {
    serve()
    await page.show(app())
    await typeAndWait('nord')
    // The arrows left and right move in the text until the arrows went into the list.
    const main = field().getAttribute('aria-activedescendant')
    await press('ArrowRight')
    expect(field().getAttribute('aria-activedescendant')).toBe(main)
    await press('ArrowDown')
    await press('ArrowUp')
    await press('ArrowRight', 2)
    const cell = document.getElementById(field().getAttribute('aria-activedescendant')!)!
    expect(cell.dataset.testid).toBe('hit-acquire-audio')
    expect(cell.classList.contains('act')).toBe(true)
    await press('ArrowRight', 5)
    expect(document.getElementById(field().getAttribute('aria-activedescendant')!)!.dataset.testid).toBe('hit-acquire-both')
    await press('ArrowLeft')
    await press('Enter')
    // The page of the hit is made first, then the dialog opens with "Hörbuch" chosen.
    expect(server.called('POST', '/api/v1/search/catalog/ticket-0001/books')!.body).toEqual({ key: KEY })
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
    expect(page.dialog!.querySelector('[data-what="audio"]')!.getAttribute('aria-checked')).toBe('true')
  })

  it('offer nothing to acquire at what is no book, and say so at a book named like its series', async () => {
    serve()
    await page.show(app())
    await typeAndWait('nord')
    const [book, play, series] = all('[data-testid="catalog-hit"]')
    expect(book!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(3)
    expect(play!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(0)
    expect(plain(play!.querySelector('[data-testid="hit-label"]')!.textContent)).toBe('Hörspiel')
    expect(series!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(0)
    expect(plain(series!.querySelector('[data-testid="hit-why-not"]')!.textContent)).toBe('Band unbekannt')
    // The arrows do not go to buttons that are not there.
    await press('ArrowDown')
    await press('ArrowRight')
    expect(field().getAttribute('aria-activedescendant')).toBe(play!.querySelector('[role="gridcell"]')!.id)
  })

  it('make the page of a hit when it is opened, not before', async () => {
    serve()
    await page.show(app())
    await typeAndWait('nord')
    expect(server.calls.some((call) => call.url.endsWith('/books'))).toBe(false)
    await page.click(all('[data-testid="catalog-hit"]')[0]!)
    await idle()
    expect(server.called('POST', '/api/v1/search/catalog/ticket-0001/books')!.body).toEqual({ key: KEY })
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/tok-made')
  })

  it('never take an older answer for a newer one', async () => {
    serve()
    let release: (value: Response) => void = () => undefined
    const fake = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const body = init?.body ? (JSON.parse(String(init.body)) as { q?: string }) : {}
        if (url === '/api/v1/search/catalog' && body.q === 'nord') return new Promise<Response>((resolve) => (release = resolve))
        if (url === '/api/v1/search/catalog' && body.q === 'nordsee') {
          return new Response(JSON.stringify({ available: true, failed: false, pending: false, ticket: 'ticket-0002', items: [catalogHit('tok-sea', { title: 'Nordsee neu' })] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        }
        return fake(url, init)
      }),
    )
    await page.show(app())
    // (The first answer is held back: the page is never idle until it comes.)
    await setText('nord')
    await act(async () => void (await vi.advanceTimersByTimeAsync(900)))
    await setText('nordsee')
    await act(async () => void (await vi.advanceTimersByTimeAsync(900)))
    await vi.waitFor(() => expect(all('[data-testid="catalog-hit"] b').map((b) => plain(b.textContent))).toEqual(['Nordsee neu']))
    release(new Response(JSON.stringify({ available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [BOOK] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await idle()
    expect(all('[data-testid="catalog-hit"] b').map((b) => plain(b.textContent))).toEqual(['Nordsee neu'])
  })

  it('stop a search on the server that the person typed on from while the sources were still asked', async () => {
    serve({
      'POST /api/v1/search/catalog': { available: true, failed: false, pending: true, ticket: 'ticket-0001', items: [BOOK], people: [] },
      'GET /api/v1/search/catalog/ticket-0001': { available: true, failed: false, pending: true, ticket: 'ticket-0001', items: [BOOK], people: [] },
    })
    await page.show(app())
    await typeAndWait('nord', 750)
    await page.type(field(), 'nordsee')
    await idle()
    expect(server.called('DELETE', '/api/v1/search/catalog/ticket-0001')).toBeDefined()
  })

  it('give up a search on the server when Escape or a press outside closes the list, and ask anew when it opens again', async () => {
    const still = { available: true, failed: false, pending: true, ticket: 'ticket-0001', items: [BOOK], people: [] }
    serve({ 'POST /api/v1/search/catalog': still })
    await page.show(app())
    await holdAskingAgain('nord')
    expect(server.called('DELETE', '/api/v1/search/catalog/ticket-0001')).toBeUndefined()
    await press('Escape')
    await idle()
    expect(server.calls.filter((call) => call.method === 'DELETE' && call.url === '/api/v1/search/catalog/ticket-0001')).toHaveLength(1)
    const asked = server.calls.filter((call) => call.method === 'POST' && call.url === '/api/v1/search/catalog').length
    await act(async () => {
      field().blur()
      field().focus()
      await vi.advanceTimersByTimeAsync(900)
    })
    await vi.waitFor(() => expect(server.calls.filter((call) => call.method === 'POST' && call.url === '/api/v1/search/catalog').length).toBe(asked + 1))
    // A press outside the field (a tab of the head) closes the list and gives the search up as well.
    await act(async () => document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })))
    await idle()
    expect(server.calls.filter((call) => call.method === 'DELETE' && call.url === '/api/v1/search/catalog/ticket-0001')).toHaveLength(2)
  })

  it('give up a search on the server when the page is reloaded or closed', async () => {
    const still = { available: true, failed: false, pending: true, ticket: 'ticket-0001', items: [BOOK], people: [] }
    serve({ 'POST /api/v1/search/catalog': still })
    await page.show(app())
    await holdAskingAgain('nord')
    expect(server.called('DELETE', '/api/v1/search/catalog/ticket-0001')).toBeUndefined()
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await vi.waitFor(() => expect(server.called('DELETE', '/api/v1/search/catalog/ticket-0001')).toBeDefined())
  })

  it('say which source is not answering, never "nothing found" while one rests, and announce the number of hits', async () => {
    const until = new Date(Date.now() + 90_000).toISOString()
    serve({ 'POST /api/v1/search/catalog': { available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [], people: [], resting: [{ source: 'openlibrary', until }] } })
    await page.show(app())
    await typeAndWait('nord')
    const line = plain(q('[data-testid="catalog-resting"]')!.textContent)
    expect(line).toMatch(/^Open Library antwortet gerade nicht, deshalb gibt es noch keine Treffer\. Wieder ab \d\d:\d\d\.$/)
    expect(q('[data-testid="results"]')!.textContent).not.toContain('Nichts gefunden.')
    expect(plain(q('[data-testid="catalog-count"]')!.textContent)).toBe('0 Treffer im Katalog')
  })

  it('say that the hits are incomplete when a source did not answer', async () => {
    serve({ 'POST /api/v1/search/catalog': { available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [BOOK, PLAY], people: [], resting: [{ source: 'openlibrary', until: null }, { source: 'dnb', until: null }] } })
    await page.show(app())
    await typeAndWait('nord')
    expect(plain(q('[data-testid="catalog-resting"]')!.textContent)).toBe('Open Library und Deutsche Nationalbibliothek antworten gerade nicht, die Treffer sind unvollständig.')
    expect(plain(q('[data-testid="catalog-count"]')!.textContent)).toBe('2 Treffer im Katalog')
  })

  it('say that the person searched too much at once when the server slows the search down', async () => {
    serve()
    server.fails = { 'POST /api/v1/search/catalog': { status: 429, code: 'search_too_often' } }
    await page.show(app())
    await typeAndWait('nord')
    expect(plain(q('[data-testid="catalog-busy"]')!.textContent)).toBe('Zu viele Suchen auf einmal. Warte eine Minute.')
    expect(q('[data-testid="results"]')!.textContent).not.toContain('Der Katalog antwortet gerade nicht.')
  })
})
