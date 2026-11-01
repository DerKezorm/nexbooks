/**
 * The places that ask for a book: "Alle beschaffen" on the wish list, "Fehlende beschaffen" in the gaps of a
 * series, "Beschaffen" on the page of a book of the sources and beside the hits of the catalogue in the search, and the
 * fourth area in the head. None of them is there for a person who may not ask for books.
 */
import { act } from 'react'
import { Route, Routes } from 'react-router-dom'

import type { Me } from '../api/client'
import { AppShell } from '../components/AppShell'
import { SearchBox } from '../components/SearchBox'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { work } from '../test/books'
import { catalogHit } from '../test/catalog'
import { bookPage, card, forYou, gap, list, volume } from '../test/discover'
import { Page, Server, plain } from '../test/harness'
import { counts, options, running, wish } from '../test/procure'
import { idle } from '../test/wait'
import { DiscoverPage } from './DiscoverPage'
import { ForeignPage } from './ForeignPage'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'member', avatar: null, version: '0.1.0', whats_new_seen: '0.1.0', profile: { theme: 'light', view: 'shelf' } } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const BEST = 'GET /api/v1/discover/lists/best?hide_owned=true'
const FOR = 'GET /api/v1/discover/for-you'

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/wishlist': { count: 0, items: [] },
    [BEST]: list([card('t1', { title: 'Der Leuchtturmwaerter' })]),
    'GET /api/v1/procure/counts': counts(),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/libraries': [],
    'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }] },
    ...over,
  }
  server.fails = {}
  server.install()
}

function discover() {
  return (
    <WishProvider>
      <ProcureProvider>
        <Routes>
          <Route path="discover" element={<DiscoverPage />} />
        </Routes>
      </ProcureProvider>
    </WishProvider>
  )
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const q = (selector: string) => document.querySelector<HTMLElement>(selector)

describe('the wish list', () => {
  const wished = [card('w1', { title: 'Eins', wished: true }), card('w2', { title: 'Zwei', wished: true }), card('w3', { title: 'Drei', wished: true, owned_work_id: 4 })]

  it('asks for all the books that nobody has asked for yet, in one dialog', async () => {
    serve({ 'GET /api/v1/wishlist': { count: 3, items: wished }, 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: { work_id: null, book: 'w2', title: 'Zwei', authors: [], series: '', series_pos: null, genre: '', color: '#000', cover_url: null } })] }, 'GET /api/v1/procure/book-options?book=w1': options() })
    await page.show(discover(), '/discover?list=best')
    const button = q('[data-testid="acquire-all"]')!
    // w2 is asked for already and w3 is on the shelf: one book is left.
    expect(plain(button.textContent)).toBe('Alle beschaffen 1')
    await page.click(button)
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
    expect(page.dialog!.textContent).toContain('Eins')
  })

  it('names the dialog "n Bücher beschaffen?" when there are several', async () => {
    serve({ 'GET /api/v1/wishlist': { count: 2, items: wished.slice(0, 2) }, 'GET /api/v1/procure/book-options?book=w1': options(), 'POST /api/v1/procure/wishes': { results: [] } })
    await page.show(discover(), '/discover?list=best')
    await page.click(q('[data-testid="acquire-all"]'))
    expect(page.dialog!.getAttribute('aria-label')).toBe('2 Bücher beschaffen?')
    expect(page.dialog!.querySelector('[role="radio"][aria-checked="true"]')!.textContent).toBe('E-Book')
  })

  it('has no such button for a person who may not ask for books, and keeps its old line', async () => {
    serve({ 'GET /api/v1/wishlist': { count: 2, items: wished.slice(0, 2) }, 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(discover(), '/discover?list=best')
    expect(q('[data-testid="acquire-all"]')).toBeNull()
    expect(q('.wishsec')).not.toBeNull()
  })
})

describe('the gaps of a series', () => {
  const own = work(9, { title: 'Die Krone der Weberinnen', series: { id: 3, name: 'Die Chroniken von Arvel', pos: 1 } })
  const arvel = gap({ volumes: [volume(1, 'own', { work: own }), volume(2, 'missing', { book: card('g2', { title: 'Glas und Asche', series: 'Die Chroniken von Arvel', series_pos: 2 }) }), volume(3, 'soon', { book: card('g3', { title: 'Der Thron', series: 'Die Chroniken von Arvel', series_pos: 3, upcoming: true }) })] })

  it('asks for the missing volumes of the series, those to come as well, and shows the button on each missing card', async () => {
    serve({ [FOR]: forYou({ gaps: [arvel] }), 'GET /api/v1/procure/book-options?book=g2': options() })
    await page.show(discover(), '/discover?list=foryou')
    const button = q('[data-testid="acquire-gap"]')!
    expect(plain(button.textContent)).toBe('Fehlende beschaffen')
    expect([...document.querySelectorAll('[data-procure]')].map((item) => item.getAttribute('data-procure'))).toEqual(['g2', 'g3'])
    await page.click(button)
    expect(page.dialog!.getAttribute('aria-label')).toBe('2 Bücher beschaffen?')
  })

  it('leaves the volumes out that are asked for already, and the button when none is left', async () => {
    const asked = (token: string) => wish(1, { book: { work_id: null, book: token, title: token, authors: [], series: '', series_pos: null, genre: '', color: '#000', cover_url: null } })
    serve({ [FOR]: forYou({ gaps: [arvel] }), 'GET /api/v1/procure/wishes': { wishes: [asked('g2'), asked('g3')] } })
    await page.show(discover(), '/discover?list=foryou')
    expect(q('[data-testid="acquire-gap"]')).toBeNull()
    expect(document.querySelectorAll('[data-testid="acquire-done"]')).toHaveLength(2)
  })
})

describe('the page of a book of the sources', () => {
  function foreign() {
    return (
      <WishProvider>
        <ProcureProvider>
          <Routes>
            <Route path="discover/book/:token" element={<ForeignPage />} />
          </Routes>
        </ProcureProvider>
      </WishProvider>
    )
  }

  it('puts "Beschaffen" first for who may, then "Releases ansehen" and "Merken"', async () => {
    serve({ 'GET /api/v1/discover/books/g2': bookPage('g2', { title: 'Glas und Asche' }), 'GET /api/v1/procure/book-options?book=g2': options() })
    await page.show(foreign(), '/discover/book/g2')
    expect([...document.querySelectorAll('.want button')].map((item) => plain(item.textContent))).toEqual(['Beschaffen', 'Releases ansehen', 'Merken'])
    await page.click(q('[data-testid="acquire-open"]'))
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
  })

  it('shows where the book stands once it is asked for', async () => {
    serve({ 'GET /api/v1/discover/books/g2': bookPage('g2'), 'GET /api/v1/procure/running': { downloads: [running(1, { book: { work_id: null, book: 'g2', title: 'x', authors: [], series: '', series_pos: null, genre: '', color: '#000', cover_url: null } })] }, 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'loading', book: { work_id: null, book: 'g2', title: 'x', authors: [], series: '', series_pos: null, genre: '', color: '#000', cover_url: null } })] } })
    await page.show(foreign(), '/discover/book/g2')
    const link = q('[data-testid="acquire-state"]')!
    expect(plain(link.textContent)).toBe('Wird beschafft')
    expect(link.getAttribute('href')).toBe('/acquire?reiter=running')
    expect(q('[data-testid="acquire-open"]')).toBeNull()
  })

  it('keeps "Merken" first for a person who may not ask for books', async () => {
    serve({ 'GET /api/v1/discover/books/g2': bookPage('g2'), 'GET /api/v1/procure/counts': counts({ allowed: false }), 'GET /api/v1/procure/targets': { targets: [] } })
    await page.show(foreign(), '/discover/book/g2')
    expect([...document.querySelectorAll('.want button')].map((item) => plain(item.textContent))).toEqual(['Merken'])
  })
})

describe('the search', () => {
  function search() {
    return (
      <WishProvider>
        <ProcureProvider>
          <SearchBox />
          <Routes>
            <Route path="*" element={<div />} />
          </Routes>
        </ProcureProvider>
      </WishProvider>
    )
  }
  const hit = (token: string) => catalogHit(token)

  async function ask() {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await page.type(document.querySelector('#q') as HTMLInputElement, 'nord')
    await act(async () => void (await vi.advanceTimersByTimeAsync(900)))
    await idle()
  }

  it('offers "E-Book", "Hörbuch" and "Beides" beside a hit of the catalogue that is not on the shelf, and opens the dialog for it', async () => {
    serve({ 'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: [] }, 'POST /api/v1/search/catalog': { available: true, failed: false, items: [hit('tok-n')] }, 'GET /api/v1/procure/book-options?book=tok-n': options() })
    await page.show(search())
    await ask()
    expect([...document.querySelectorAll('[data-testid^="hit-acquire-"]')].map((b) => plain(b.textContent))).toEqual(['E-Book', 'Hörbuch', 'Beides'])
    const action = q('[data-testid="hit-acquire-audio"]')!
    await page.click(action)
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
    expect(page.dialog!.textContent).toContain('Nordsee Notizen')
    // The list closed, the hit was not opened.
    expect(q('[data-testid="catalog-hit"]')).toBeNull()
  })

  it('says "gesucht" for a hit that is asked for already, and offers nothing to a person who may not ask', async () => {
    serve({ 'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: [] }, 'POST /api/v1/search/catalog': { available: true, failed: false, items: [hit('tok-n')] }, 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: { work_id: null, book: 'tok-n', title: 'x', authors: [], series: '', series_pos: null, genre: '', color: '#000', cover_url: null } })] } })
    await page.show(search())
    await ask()
    expect(plain(q('[data-testid="catalog-asked"]')!.textContent)).toBe('gesucht')
    expect(q('[data-testid^="hit-acquire-"]')).toBeNull()
    page.close()
    vi.useRealTimers()
    serve({ 'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: [] }, 'POST /api/v1/search/catalog': { available: true, failed: false, items: [hit('tok-n')] }, 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(search())
    await ask()
    expect(q('[data-testid^="hit-acquire-"]')).toBeNull()
    expect(plain(q('[data-testid="catalog-hit"] .chips')!.textContent)).toContain('nicht im Regal')
  })
})

describe('the head', () => {
  function shell() {
    return (
      <WishProvider>
        <ProcureProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<div />} />
            </Route>
          </Routes>
        </ProcureProvider>
      </WishProvider>
    )
  }

  it('has a fourth area with the number of what runs and what needs somebody, for who may ask for books', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ running: 2, needs: 1 }) })
    await page.show(shell())
    const tabs = [...document.querySelectorAll('.tabs a')].map((item) => plain(item.textContent))
    expect(tabs).toEqual(['Bibliothek', 'Entdecken', 'Lesestapel', 'Beschaffen3'])
    expect(document.querySelector('[data-testid="bescount"]')!.textContent).toBe('3')
  })

  it('has no number when nothing runs, and no area for a person who may not ask for books', async () => {
    serve({ 'GET /api/v1/procure/counts': counts() })
    await page.show(shell())
    expect(document.querySelector('[data-testid="bescount"]')).toBeNull()
    expect([...document.querySelectorAll('.tabs a')]).toHaveLength(4)
    page.close()
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(shell())
    expect([...document.querySelectorAll('.tabs a')]).toHaveLength(3)
  })
})
