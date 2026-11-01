/**
 * The small filter of the library: the books that are here by default, those on their way on request, and both.
 */
import type { Me, WorkBrief } from '../api/client'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { book, counts, wish } from '../test/procure'
import { LibraryPage } from './LibraryPage'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'member', profile: { view: 'shelf' } } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()
const library = { id: 1, name: 'Familie', kind: 'both', my_right: 'read', state: 'idle', counts: { ebooks: 1, audiobooks: 0, files: 1, bytes: 1 } }
const stats = { works: 2, editions: 2, ebook_editions: 2, audio_editions: 0, works_with_ebook: 2, works_with_audio: 0, genres: { roman: 2 } }
const ON_THE_WAY = book({ book: 'tok1', title: 'Noch nicht da', genre: 'roman' })

function serve(works: WorkBrief[], over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/libraries': [library],
    'GET /api/v1/works?page=1&per_page=200': { items: works, total: works.length, page: 1, per_page: 200 },
    'GET /api/v1/works/stats': stats,
    'GET /api/v1/procure/counts': counts(),
    'GET /api/v1/procure/wishes': { wishes: [wish(1, { book: ON_THE_WAY })] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/wishlist': { count: 0, items: [] },
    ...over,
  }
  server.fails = {}
  server.install()
}

const show = () =>
  page.show(
    <WishProvider>
      <ProcureProvider>
        <LibraryPage />
      </ProcureProvider>
    </WishProvider>,
  )
const pick = (key: string) => page.click(document.querySelector(`[data-stock="${key}"]`) as HTMLElement)
const onShelf = () => document.querySelector('[data-testid="incoming"]') !== null

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the filter of the library', () => {
  it('shows what is here by default, and names its three choices', async () => {
    serve([work(1)])
    await show()
    const buttons = [...document.querySelectorAll('#stock button')]
    expect(buttons.map((button) => plain(button.textContent))).toEqual(['Alle', 'Nur verfügbar', 'In Beschaffung'])
    expect(document.querySelector('[data-stock="available"]')!.getAttribute('aria-pressed')).toBe('true')
    expect(onShelf()).toBe(false)
    expect(document.querySelector('[data-work="1"]')).not.toBeNull()
  })

  it('shows both under "Alle"', async () => {
    serve([work(1)])
    await show()
    await pick('all')
    expect(onShelf()).toBe(true)
    expect(document.querySelector('[data-work="1"]')).not.toBeNull()
  })

  it('shows only the books on their way under "In Beschaffung"', async () => {
    serve([work(1)])
    await show()
    await pick('incoming')
    expect(onShelf()).toBe(true)
    expect(document.querySelector('[data-work="1"]')).toBeNull()
  })

  it('says so when nothing is being fetched', async () => {
    serve([work(1)], { 'GET /api/v1/procure/wishes': { wishes: [] } })
    await show()
    await pick('incoming')
    expect(plain(document.querySelector('[data-testid="stock-nothing"]')!.textContent)).toBe('Gerade wird nichts beschafft.')
  })

  it('is not there for a person who cannot fetch books', async () => {
    serve([work(1)], { 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await show()
    expect(document.querySelector('#stock')).toBeNull()
  })
})
