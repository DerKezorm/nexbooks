/**
 * The search of the catalogues in Discover: one hit for each book, the book asked for first, the other volumes of its series
 * under their own heading, what is set apart (a box, a play, a study guide) after them and named, a book in none of the
 * languages fetched in last with the languages it exists in. "E-Book", "Hörbuch" and "Beides" open the dialog to acquire it;
 * "Ausgaben zeigen" shows its single editions. While the sources are still being asked the page asks again by the ticket.
 * The quick search of the head shows the first of the same hits and leads here.
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import type { CatalogHit, Me } from '../api/client'
import { SearchBox } from '../components/SearchBox'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { catalogHit } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { counts, options } from '../test/procure'
import { idle } from '../test/wait'
import { DiscoverSearchPage } from './DiscoverSearch'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'member', avatar: null, version: '0.1.0', whats_new_seen: '0.1.0', profile: { theme: 'light', view: 'shelf' } } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const SALT = catalogHit('tok-salt', {
  title: 'Das Salztor', authors: ['Ada Sorrel'], year: 2014, series: 'Die Laternen-Saga', series_pos: 2, audio: true,
  editions: [
    { source: 'dnb', form: 'ebook', language: 'de', title: 'Das Salztor', subtitle: 'Roman', year: 2015, isbn: '9783000070042', asin: '', publisher: 'Beispielverlag', narrators: [], minutes: null, variant: '', pages: 470 },
    { source: 'audible', form: 'audio', language: 'de', title: 'Das Salztor', subtitle: '', year: 2019, isbn: '', asin: 'B070000002', publisher: '', narrators: ['Mara Quell'], minutes: 900, variant: 'unabridged', pages: null },
  ],
})
const ROAD = catalogHit('tok-road', { title: 'Die Laternenstraße', authors: ['Ada Sorrel'], series: 'Die Laternen-Saga', series_pos: 1, group: 'series' })
const PLAY = catalogHit('tok-play', { title: 'Das Salztor', authors: ['Ada Sorrel'], label: 'drama', group: 'related', ebook: false, audio: true })
const PHARE = catalogHit('tok-phare', { title: 'La Maison du Phare', authors: ['Odile Ferrand'], allowed: false, language: '', languages: ['fr'], group: 'elsewhere' })
const ANSWER = { available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [SALT, ROAD, PLAY, PHARE], people: [] }

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/procure/counts': counts(),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/libraries': [],
    'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }] },
    'POST /api/v1/search/catalog': ANSWER,
    ...over,
  }
  server.fails = {}
  server.install()
}

function Where() {
  const location = useLocation()
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>
}

function app() {
  return (
    <WishProvider>
      <ProcureProvider>
        <SearchBox />
        <Routes>
          <Route path="discover/search" element={<DiscoverSearchPage />} />
          <Route path="*" element={<Where />} />
        </Routes>
        <Where />
      </ProcureProvider>
    </WishProvider>
  )
}

const q = (selector: string) => document.querySelector(selector) as HTMLElement | null
const all = (selector: string) => [...document.querySelectorAll(selector)] as HTMLElement[]

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the page of the search', () => {
  it('shows one hit for each book in the order of a library, each group under its heading', async () => {
    serve()
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(server.called('POST', '/api/v1/search/catalog')!.body).toEqual({ q: 'salztor' })
    const groups = all('[data-testid^="search-group-"]').map((section) => section.dataset.testid)
    expect(groups).toEqual(['search-group-match', 'search-group-series', 'search-group-related', 'search-group-elsewhere'])
    expect(all('.hit-head').map((h) => plain(h.textContent))).toEqual([
      'Weitere Bände von Die Laternen-Saga',
      'Boxen, Hörspiele, Lektürehilfen, Comics und Teilausgaben',
      'Nicht in den Sprachen, in denen beschafft wird',
    ])
    const first = all('[data-testid="search-hit"]')[0]!
    expect(plain(first.querySelector('.hit-title b')!.textContent)).toBe('Das Salztor')
    expect(plain(first.querySelector('.hit-body > small')!.textContent)).toBe('Ada Sorrel · 2014 · Band 2 · Die Laternen-Saga')
    const play = all('[data-testid="search-hit"]')[2]!
    expect(plain(play.querySelector('[data-testid="hit-label"]')!.textContent)).toBe('Hörspiel')
    const phare = all('[data-testid="search-hit"]')[3]!
    expect(plain(phare.querySelector('[data-testid="hit-elsewhere"]')!.textContent)).toBe('gibt es auf Französisch')
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })

  it('opens the dialog to acquire with the form chosen, one press for each', async () => {
    serve({ 'GET /api/v1/procure/book-options?book=tok-salt': options() })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const first = all('[data-testid="search-hit"]')[0]!
    expect([...first.querySelectorAll('[data-testid^="hit-acquire-"]')].map((b) => plain(b.textContent))).toEqual(['E-Book', 'Hörbuch', 'Beides'])
    await page.click(first.querySelector('[data-testid="hit-acquire-both"]') as HTMLElement)
    await idle()
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
    expect(page.dialog!.textContent).toContain('Das Salztor')
    expect(page.dialog!.querySelector('[data-what="both"]')!.getAttribute('aria-checked')).toBe('true')
    await page.click(page.dialog!.querySelector('[data-close]') as HTMLElement)
    await page.click(all('[data-testid="search-hit"]')[0]!.querySelector('[data-testid="hit-acquire-audio"]') as HTMLElement)
    await idle()
    expect(page.dialog!.querySelector('[data-what="audio"]')!.getAttribute('aria-checked')).toBe('true')
  })

  it('acquires a volume of a series as that volume: the dialog names the series and the volume', async () => {
    serve({ 'GET /api/v1/procure/book-options?book=tok-road': options() })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const road = all('[data-testid="search-hit"]').find((hit) => plain(hit.querySelector('.hit-title b')!.textContent) === 'Die Laternenstraße')!
    await page.click(road.querySelector('[data-testid="hit-acquire-ebook"]') as HTMLElement)
    await idle()
    expect(plain(page.dialog!.textContent)).toContain('Die Laternen-Saga, Band 1')
  })

  it('keeps two hits of the same title apart, each its own row', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const twin = catalogHit('tok-twin', { key: 'k'.repeat(20), title: 'Das Salztor', authors: ['Theo Brand'], group: 'more' })
    const other = catalogHit('tok-other', { key: 'o'.repeat(20), title: 'Das Salztor', authors: ['Ida Hollm'], group: 'more' })
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [SALT, twin, other] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(all('[data-testid="search-hit"]').map((hit) => plain(hit.querySelector('.hit-body > small')!.textContent).split(' · ')[0])).toEqual(['Ada Sorrel', 'Theo Brand', 'Ida Hollm'])
    expect(errors.mock.calls.some((call) => String(call[0]).includes('same key'))).toBe(false)
    errors.mockRestore()
  })

  it('shows the single editions only when asked', async () => {
    serve()
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const first = all('[data-testid="search-hit"]')[0]!
    expect(first.querySelectorAll('[data-testid="hit-edition"]').length).toBe(0)
    const toggle = first.querySelector('[data-testid="hit-editions"]') as HTMLElement
    expect(plain(toggle.textContent)).toBe('Ausgaben zeigen (2)')
    await page.click(toggle)
    const editions = [...first.querySelectorAll('[data-testid="hit-edition"] small')].map((e) => plain(e.textContent))
    expect(editions).toEqual([
      'Deutsch · Buch · 2015 · Beispielverlag · 470 Seiten · ISBN 9783000070042 · Deutsche Nationalbibliothek',
      'Deutsch · Hörbuch · 2019 · gelesen von Mara Quell · 15 Std. 0 Min. · Audible',
    ])
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })

  it('leads a book on the shelf to the book on the shelf, and any other to its page', async () => {
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [{ ...SALT, owned_work_id: 7 }, ROAD] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const [first, second] = all('[data-testid="search-hit"]')
    expect(first!.querySelectorAll('[data-testid^="hit-acquire-"]').length).toBe(0)
    expect(plain(first!.querySelector('.chips')!.textContent)).toBe('Im Regal')
    await page.click(second!.querySelector('.hit-title') as HTMLElement)
    expect(plain(all('[data-testid="where"]').at(-1)!.textContent)).toBe('/discover/book/tok-road')
  })

  it('asks again by the ticket while the sources are still being asked', async () => {
    serve({
      'POST /api/v1/search/catalog': { ...ANSWER, pending: true, items: [ROAD] },
      'GET /api/v1/search/catalog/ticket-0001': ANSWER,
    })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    await idle()
    expect(server.called('GET', '/api/v1/search/catalog/ticket-0001')).toBeTruthy()
    expect(all('[data-testid="search-hit"]').length).toBe(4)
    expect(q('[data-testid="search-pending"]')).toBeNull()
  })

  it('says it when no source is on, and asks nothing without words', async () => {
    serve({ 'POST /api/v1/search/catalog': { available: false, failed: false, pending: false, ticket: null, items: [] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(document.body.textContent).toContain('Es ist keine Quelle eingeschaltet.')
    page.close()
    serve()
    await page.show(app(), '/discover/search')
    await idle()
    expect(server.called('POST', '/api/v1/search/catalog')).toBeUndefined()
    expect(document.body.textContent).toContain('Gib einen Titel, eine Reihe mit Band oder einen Namen ein.')
  })

  it('says which source is not answering, and never "nothing found" while one rests', async () => {
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [], resting: [{ source: 'dnb', until: new Date(Date.now() + 60_000).toISOString() }] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(plain(q('[data-testid="search-resting"]')!.textContent)).toMatch(/^Deutsche Nationalbibliothek antwortet gerade nicht, deshalb gibt es noch keine Treffer\. Wieder ab \d\d:\d\d\.$/)
    expect(document.body.textContent).not.toContain('Nichts gefunden.')
    page.close()
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, resting: [{ source: 'openlibrary', until: null }] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(plain(q('[data-testid="search-resting"]')!.textContent)).toBe('Open Library antwortet gerade nicht, die Treffer sind unvollständig.')
    expect(all('[data-testid="search-hit"]')).toHaveLength(4)
  })

  it('searches what is typed into its field', async () => {
    serve()
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const field = q('[data-testid="discover-search"] input') as HTMLInputElement
    await page.type(field, 'laternenstraße')
    await act(async () => void q('[data-testid="discover-search"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    await idle()
    expect(server.calls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([{ q: 'salztor' }, { q: 'laternenstraße' }])
  })
})

describe('what a search offers and what it costs', () => {
  it('keeps the order of a library whatever order the hits come in', async () => {
    const more = catalogHit('tok-more', { title: 'Salz und Licht', authors: ['Theo Brand'], group: 'more' })
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [PHARE, PLAY, more, ROAD, SALT] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(all('[data-testid^="search-group-"]').map((section) => section.dataset.testid)).toEqual([
      'search-group-match', 'search-group-series', 'search-group-more', 'search-group-related', 'search-group-elsewhere',
    ])
  })

  it('offers to acquire only a book, and says it of a book named like its series', async () => {
    const box = catalogHit('tok-box', { title: 'Die Laternen-Saga 1 bis 3', label: 'box', group: 'related', procurable: false, why_not: 'not_a_book' })
    const whole = catalogHit('tok-whole', { title: 'Die Laternen-Saga', group: 'more', procurable: false, why_not: 'volume_unknown' })
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [SALT, whole, box] } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    const [first, second, third] = all('[data-testid="search-hit"]')
    expect(first!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(3)
    expect(second!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(0)
    expect(plain(second!.querySelector('[data-testid="hit-why-not"]')!.textContent)).toBe('Band unbekannt')
    expect(third!.querySelectorAll('[data-testid^="hit-acquire-"]')).toHaveLength(0)
    expect(plain(third!.querySelector('[data-testid="hit-label"]')!.textContent)).toBe('Box oder Sammelband')
  })

  it('makes the page of a hit when the person opens it', async () => {
    const fresh = catalogHit(null, { key: 'd'.repeat(20), title: 'Das Salztor' })
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, items: [fresh] }, 'POST /api/v1/search/catalog/ticket-0001/books': { token: 'tok-made' } })
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(server.calls.some((call) => call.url.endsWith('/books'))).toBe(false)
    await page.click(q('[data-testid="search-hit"] .hit-title'))
    await idle()
    expect(server.called('POST', '/api/v1/search/catalog/ticket-0001/books')!.body).toEqual({ key: 'd'.repeat(20) })
    expect(plain(all('[data-testid="where"]').at(-1)!.textContent)).toBe('/discover/book/tok-made')
  })

  it('stops the search on the server when the person leaves while the sources are still asked', async () => {
    serve({ 'POST /api/v1/search/catalog': { ...ANSWER, pending: true, items: [ROAD] }, 'DELETE /api/v1/search/catalog/ticket-0001': 204 })
    const answers = server.answers
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${url}`)
        if (url === '/api/v1/search/catalog/ticket-0001' && (init?.method ?? 'GET') === 'GET') return await new Promise<Response>(() => undefined)
        if (init?.method === 'DELETE') return new Response(null, { status: 204 })
        return new Response(JSON.stringify(answers[`${init?.method ?? 'GET'} ${url}`] ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }),
    )
    // (Shown without waiting for the fake server to be idle: the question by the ticket is held open.)
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const box = document.createElement('div')
    document.body.appendChild(box)
    const root = createRoot(box)
    await act(async () => root.render(<MemoryRouter initialEntries={['/discover/search?q=salztor']}>{app()}</MemoryRouter>))
    await vi.waitFor(() => expect(calls).toContain('GET /api/v1/search/catalog/ticket-0001'))
    expect(calls).not.toContain('DELETE /api/v1/search/catalog/ticket-0001')
    act(() => root.unmount())
    box.remove()
    expect(calls).toContain('DELETE /api/v1/search/catalog/ticket-0001')
    // (Nothing for the hook after the test to close this once.)
    const close = page.close.bind(page)
    page.close = () => {
      page.close = close
    }
  })

  it('says that the person searched too much at once when the server slows the search down', async () => {
    serve()
    server.fails = { 'POST /api/v1/search/catalog': { status: 429, code: 'search_too_often' } }
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(plain(q('[role="alert"]')!.textContent)).toBe('Zu viele Suchen auf einmal. Warte eine Minute.')
  })
})

describe('the quick search of the head', () => {
  it('shows the first hits of the same answer and leads to all of them in Discover', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const many: CatalogHit[] = Array.from({ length: 8 }, (_unused, index) => catalogHit(`tok-${index}`, { title: `Buch ${index}` }))
    serve({ 'GET /api/v1/search?q=buch&limit=8': { query: 'buch', items: [] }, 'POST /api/v1/search/catalog': { ...ANSWER, items: many } })
    await page.show(app())
    await page.type(document.querySelector('#q') as HTMLInputElement, 'buch')
    await act(async () => void (await vi.advanceTimersByTimeAsync(900)))
    await idle()
    expect(all('[data-testid="catalog-hit"]').map((hit) => plain(hit.querySelector('b')!.textContent))).toEqual(['Buch 0', 'Buch 1', 'Buch 2', 'Buch 3', 'Buch 4', 'Buch 5'])
    const more = q('[data-testid="catalog-all"]')!
    expect(plain(more.textContent)).toBe('Alle 8 Treffer in Entdecken zeigen')
    await page.click(more)
    expect(plain(all('[data-testid="where"]').at(-1)!.textContent)).toBe('/discover/search?q=buch')
  })

  it('asks again by the ticket while the answer is pending, and says that more are coming', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let release: (value: Response) => void = () => undefined
    serve({ 'GET /api/v1/search?q=salztor&limit=8': { query: 'salztor', items: [] }, 'POST /api/v1/search/catalog': { ...ANSWER, pending: true, items: [ROAD] } })
    const answers = server.answers
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === '/api/v1/search/catalog/ticket-0001') return await new Promise<Response>((resolve) => (release = resolve))
        return new Response(JSON.stringify(answers[`${init?.method ?? 'GET'} ${url}`] ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }),
    )
    await page.show(app())
    await page.type(document.querySelector('#q') as HTMLInputElement, 'salztor')
    await act(async () => void (await vi.advanceTimersByTimeAsync(900)))
    // (The question by the ticket is held open: the fake API is not idle until it is let go.)
    for (let time = 0; time < 10 && !q('[data-testid="catalog-all"]'); time += 1) {
      await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 20))))
    }
    expect(plain(q('[data-testid="catalog-all"]')!.textContent)).toBe('Weitere Treffer kommen gleich …')
    release(new Response(JSON.stringify(ANSWER), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await idle()
    expect(all('[data-testid="catalog-hit"]').map((hit) => plain(hit.querySelector('b')!.textContent))).toEqual(['Das Salztor', 'Die Laternenstraße', 'Das Salztor', 'La Maison du Phare'])
  })
})

describe('in English', () => {
  it('speaks English and never pauses with a dash', async () => {
    await changeLanguage('en', false)
    serve()
    await page.show(app(), '/discover/search?q=salztor')
    await idle()
    expect(all('.hit-head').map((h) => plain(h.textContent))).toEqual([
      'More books of Die Laternen-Saga',
      'Box sets, audio dramas, study guides, comics and parts',
      'Not in the languages books are fetched in',
    ])
    expect([...all('[data-testid="search-hit"]')[0]!.querySelectorAll('[data-testid^="hit-acquire-"]')].map((b) => plain(b.textContent))).toEqual(['E-book', 'Audiobook', 'Both'])
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})
