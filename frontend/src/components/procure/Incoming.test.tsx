/**
 * The marks of fetching on books: "Unterwegs" at the top of the library (dashed spines, the spine that fills as
 * the download goes; covers with a mark), the band at the foot of a spine whose kind is asked for, the plaque on a card in
 * Discover, "Beschaffen" on a card of a row, and the line on the page of a book.
 */
import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { ProcureProvider } from '../../state/procure'
import { work } from '../../test/books'
import { card } from '../../test/discover'
import { Page, Server, plain } from '../../test/harness'
import { book, counts, options, running, wish } from '../../test/procure'
import { ForeignCard } from '../ForeignCard'
import { ShelfSections } from '../Shelf'
import { WishProvider } from '../../state/wish'
import { IncomingSection } from './Incoming'
import { StateLine } from './StateLine'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function serve(answers: Record<string, unknown> = {}) {
  server.answers = { 'GET /api/v1/procure/counts': counts(), 'GET /api/v1/procure/wishes': { wishes: [] }, 'GET /api/v1/procure/running': { downloads: [] }, 'GET /api/v1/wishlist': { count: 0, items: [] }, ...answers }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const wrap = (inner: React.ReactNode) => (
  <WishProvider>
    <ProcureProvider>{inner}</ProcureProvider>
  </WishProvider>
)
const FOREIGN = (token: string, title: string, over = {}) => book({ book: token, title, genre: 'fantasy', ...over })

describe('Unterwegs', () => {
  it('stands as dashed spines: GESUCHT, and LÄUFT filling from the foot as the download goes', async () => {
    serve({
      'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('tok1', 'Erstes Buch') }), wish(2, { book: FOREIGN('tok2', 'Zweites Buch') }), wish(3, { book: FOREIGN('tok2', 'Zweites Buch'), kind: 'audio' })] },
      'GET /api/v1/procure/running': { downloads: [running(9, { book: FOREIGN('tok2', 'Zweites Buch'), progress: 42 })] },
    })
    await page.show(wrap(<IncomingSection look="spines" genre={null} />))
    const spines = [...document.querySelectorAll<HTMLElement>('.incoming-ghost')]
    expect(spines.map((item) => plain(item.textContent))).toEqual(['Erstes BuchGESUCHT', 'Zweites BuchLÄUFT'])
    expect(spines[1].classList.contains('running')).toBe(true)
    expect(spines[1].style.getPropertyValue('--prog')).toBe('42%')
    expect(plain(document.querySelector('.incoming-sec h2')!.textContent)).toBe('Unterwegs 2')
    expect(document.querySelector('.sec-link')!.getAttribute('href')).toBe('/acquire')
  })

  it('stands as covers with a mark in the grid, and narrows to the genre chosen', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('tok1', 'Erstes Buch') }), wish(2, { book: FOREIGN('tok2', 'Krimibuch', { genre: 'krimi' }) })] } })
    await page.show(wrap(<IncomingSection look="covers" genre="krimi" />))
    const covers = [...document.querySelectorAll('.book.inc')]
    expect(covers).toHaveLength(1)
    expect(plain(covers[0].querySelector('.inmark')!.textContent)).toBe('gesucht')
    expect(plain(covers[0].querySelector('b')!.textContent)).toBe('Krimibuch')
  })

  it('is not there when nobody asked for a book that is not on a shelf, or for a person who may not ask', async () => {
    serve()
    await page.show(wrap(<IncomingSection look="spines" genre={null} />))
    expect(document.querySelector('[data-testid="incoming"]')).toBeNull()
    page.close()
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }), 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('tok1', 'Erstes Buch') })] } })
    await page.show(wrap(<IncomingSection look="spines" genre={null} />))
    expect(document.querySelector('[data-testid="incoming"]')).toBeNull()
  })

  it('opens the page of the book over the library', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('tok1', 'Erstes Buch') })] } })
    await page.show(wrap(<IncomingSection look="spines" genre={null} />))
    expect(document.querySelector('[data-foreign="tok1"]')).not.toBeNull()
  })
})

describe('the band at the foot of a spine', () => {
  it('marks a book that lacks the kind that is asked for, and no other', async () => {
    const have = work(5)
    const other = work(6)
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { kind: 'audio', book: book({ work_id: 5, book: null, title: 'Buch 5' }) }),
          wish(2, { kind: 'ebook', state: 'upgrade', book: book({ work_id: 6, book: null, title: 'Buch 6' }) }),
        ],
      },
    })
    await page.show(wrap(<ShelfSections works={[have, other]} counts={{ roman: 2 }} found={null} onOpen={() => undefined} />))
    expect(document.querySelector('[data-work="5"]')!.classList.contains('incoming')).toBe(true)
    expect(document.querySelector('[data-work="6"]')!.classList.contains('incoming')).toBe(false)
  })
})

describe('a card in Discover', () => {
  it('shows what was asked for in the place of "Merken", and leads to the page Beschaffen', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('g1', 'Fremdes Buch g1') })] } })
    await page.show(wrap(<ForeignCard card={card('g1')} onOpen={() => undefined} />))
    const badge = document.querySelector('[data-testid="badge-acquire"]')!
    expect(plain(badge.textContent)).toBe('Gesucht')
    expect(badge.getAttribute('href')).toBe('/acquire')
    expect(document.querySelector('[data-wish]')).toBeNull()
    page.close()
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('g1', 'x') })] }, 'GET /api/v1/procure/running': { downloads: [running(2, { book: FOREIGN('g1', 'x') })] } })
    await page.show(wrap(<ForeignCard card={card('g1')} onOpen={() => undefined} />))
    expect(plain(document.querySelector('[data-testid="badge-acquire"]')!.textContent)).toBe('Wird beschafft')
    expect(document.querySelector('[data-testid="badge-acquire"]')!.getAttribute('href')).toBe('/acquire?reiter=running')
  })

  it('keeps "Merken" for a person who may not ask for books', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(wrap(<ForeignCard card={card('g1')} onOpen={() => undefined} />))
    expect(document.querySelector('[data-wish="g1"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="badge-acquire"]')).toBeNull()
  })

  it('offers "Beschaffen" on a card of a row and opens the dialog for that book', async () => {
    serve({ 'GET /api/v1/procure/book-options?book=g1': options() })
    await page.show(wrap(<ForeignCard card={card('g1')} mode="row" onOpen={() => undefined} />))
    const button = document.querySelector<HTMLButtonElement>('[data-procure="g1"]')!
    expect(button.textContent).toBe('Beschaffen')
    await page.click(button)
    expect(page.dialog!.getAttribute('aria-label')).toBe('Beschaffen')
    expect(page.dialog!.textContent).toContain('Fremdes Buch g1')
    // A book that is asked for shows that instead.
    page.close()
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: FOREIGN('g1', 'x') })] } })
    await page.show(wrap(<ForeignCard card={card('g1')} mode="row" onOpen={() => undefined} />))
    expect(plain(document.querySelector('[data-testid="acquire-done"]')!.textContent)).toBe('Gesucht')
    expect(document.querySelector('[data-procure]')).toBeNull()
  })

  it('has no "Beschaffen" on a card of a row for a person who may not ask for books', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(wrap(<ForeignCard card={card('g1')} mode="row" onOpen={() => undefined} />))
    expect(document.querySelector('[data-procure]')).toBeNull()
    expect(document.querySelector('[data-wish="g1"]')).not.toBeNull()
  })

  it('has no such button on a card of the grid', async () => {
    serve()
    await page.show(wrap(<ForeignCard card={card('g1')} onOpen={() => undefined} />))
    expect(document.querySelector('[data-procure]')).toBeNull()
  })
})

describe('the line on the page of a book', () => {
  const line = async (state: Parameters<typeof StateLine>[0]['state']) => {
    serve()
    await page.show(<StateLine state={state} />)
    const found = document.querySelector('[data-testid="state-line"]')!
    return { text: plain(found.textContent), href: found.getAttribute('href'), bar: found.querySelector('.pg i') as HTMLElement | null }
  }

  it('says how far a download is, with a bar while it downloads', async () => {
    const { text, href, bar } = await line({ kind: 'audio', wish: null, download: running(1, { phase: 'downloading', progress: 42.4 }) })
    expect(text).toBe('Wird beschafft · lädt 42 %')
    expect(href).toBe('/acquire?reiter=running')
    expect(bar!.style.width).toBe('42.4%')
  })

  it('says where a wish stands', async () => {
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'searching' }), download: null })).text).toBe('Sucht gerade')
    page.close()
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'upgrade', detail: { have: 'mp3_128', goal: 'flac' } }), download: null })).text).toBe('Upgrade gesucht · hat MP3 128, sucht bis FLAC')
    page.close()
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'waiting', release_date: '2026-11-19' }), download: null })).text).toBe('Gesucht · erscheint am 19. November 2026')
    page.close()
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'capped' }), download: null })).text).toBe('Wartet aufs Tageslimit')
    page.close()
    const until = new Date(Date.now() + 5 * 3_600_000).toISOString()
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'capped', detail: { until } }), download: null })).text).toMatch(/^Wartet aufs Tageslimit bis (\p{L}+\.?,? )?\d\d:\d\d$/u)
    page.close()
    expect((await line({ kind: 'ebook', wish: wish(1, { state: 'not_found', next_try_at: new Date(Date.now() + 3 * 3_600_000 - 60_000).toISOString() }), download: null })).text).toBe('Gesucht · nächster Versuch in 3 Std')
  })
})
