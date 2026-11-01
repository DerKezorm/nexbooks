/**
 * The page of a book that is not on a shelf: the words and facts in their order, the forms the sources
 * know as twins (the missing one dashed, the "get it" button shut), the note that the book is not in the library, the wish
 * button, the series as a small shelf with ghosts for the volumes the person lacks, and the ways to leave.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { WishProvider } from '../state/wish'
import { work } from '../test/books'
import { bookPage, card, volume } from '../test/discover'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { ForeignPage } from './ForeignPage'

const me = { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>
}

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="discover/book/:token" element={<ForeignPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </WishProvider>
  )
}

const BACK = { pathname: '/discover', search: '?list=new', hash: '', state: null, key: 'k1' }
const arvel = (over = {}) =>
  bookPage('g2', {
    title: 'Glas und Asche',
    authors: ['Iris Quist'],
    genre: 'fantasy',
    color: '#4f2b47',
    series: 'Die Chroniken von Arvel',
    series_pos: 2,
    released: '2026-09-15',
    sources: ['hardcover', 'audible'],
    kinds: ['ebook', 'audio'],
    forms: [{ kind: 'ebook', language: 'de', variant: '' }],
    description: 'Erste Zeile\nzweite Zeile.\n\nNeuer Absatz.',
    forms_known: [
      { kind: 'ebook', language: 'de', variant: '', minutes: null, narrators: [], pages: 320, source: 'hardcover' },
      { kind: 'audio', language: 'de', variant: 'unabridged', minutes: 540, narrators: ['Clara Vienne'], pages: null, source: 'audible' },
      { kind: 'audio', language: 'de', variant: 'abridged', minutes: 240, narrators: ['Ben Hollister'], pages: null, source: 'audible' },
    ],
    ...over,
  })

function serve(book = arvel(), more: Record<string, unknown> = {}) {
  server.answers = { 'GET /api/v1/wishlist': { count: 0, items: [] }, [`GET /api/v1/discover/books/${book.token}`]: book, ...more }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const q = (selector: string) => document.querySelector<HTMLElement>(selector)
const all = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)]
const at = (token: string, state?: unknown) => ({ pathname: `/discover/book/${token}`, state })

describe('fetching the book', () => {
  it('offers "Releases ansehen" beside "Merken" and on the cards of the kinds the person may fetch', async () => {
    serve(arvel(), { 'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Hörbücher', kind: 'audio' }] } })
    await page.show(app(), at('g2'))
    const want = [...q('.want')!.querySelectorAll('button')].map((b) => b.textContent)
    expect(want).toEqual(['Merken', 'Releases ansehen'])
    const offered = all('[data-testid="releases"]').map((button) => button.closest('.twin')!.getAttribute('data-kind'))
    expect(offered).toEqual(['audio'])
    server.answers['POST /api/v1/procure/searches'] = { id: 4, state: 'queued', error: '', language: 'de', kinds: ['audio'], work_id: null, book: 'g2', title: 'Glas und Asche', author: '', created_at: '2026-10-15T10:00:00Z', finished_at: null, indexers: [], cards: [], releases: [] }
    await page.click(q('[data-testid="releases"]'))
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ book: 'g2' })
    expect(page.dialog!.getAttribute('aria-label')).toBe('Releases ansehen')
  })
})

describe('the words of the page', () => {
  it('says the title in the language of the person, with the one of the source under it', async () => {
    serve(arvel({ title: 'Glas och aska', language: 'sv', titles: [{ text: 'Glas och aska', language: 'sv' }, { text: 'Glas und Asche', language: 'de' }] }))
    await page.show(app(), at('g2'))
    expect(q('.d-title')!.textContent).toBe('Glas und Asche')
    expect(q('[data-testid="original-title"]')!.textContent).toBe('Original: Glas och aska')
    expect(q('[data-testid="foreign"]')!.getAttribute('aria-label')).toBe('Glas und Asche')
  })

  it('shows no second title when the book has none in the language of the person', async () => {
    serve(arvel({ title: 'Glas och aska', titles: [{ text: 'Glas och aska', language: 'sv' }] }))
    await page.show(app(), at('g2'))
    expect(q('.d-title')!.textContent).toBe('Glas och aska')
    expect(q('[data-testid="original-title"]')).toBeNull()
  })

  it('names the genre and the series with the volume, the title, the author, the description and the facts', async () => {
    serve()
    await page.show(app(), at('g2'))
    expect(q('.d-eyebrow')!.textContent).toBe('Fantasy · Die Chroniken von Arvel, Band 2')
    expect(q('.d-title')!.textContent).toBe('Glas und Asche')
    expect(plain(q('.d-by')!.textContent)).toBe('von Iris Quist')
    expect(all('.d-blurb p').map((p) => p.textContent)).toEqual(['Erste Zeile zweite Zeile.', 'Neuer Absatz.'])
    expect(all('.d-facts span').map((s) => plain(s.textContent))).toEqual(['2026erschienen', '320Seiten', '3Fassungen', '9 Std 0 MinHörzeit'])
    expect(q('.detail')!.style.getPropertyValue('--bc')).toBe('#4f2b47')
  })

  it('says it is not in the library and where the details come from, and offers "Merken" alone to who may not fetch books', async () => {
    serve()
    await page.show(app(), at('g2'))
    const want = q('.want')!
    const buttons = [...want.querySelectorAll('button')]
    expect(buttons.map((b) => b.textContent)).toEqual(['Merken'])
    expect(buttons[0]!.classList.contains('primary')).toBe(true)
    expect(document.querySelectorAll('[data-testid="releases"]')).toHaveLength(0)
    expect(q('[data-testid="notmine"]')!.textContent).toBe('Nicht in deiner Bibliothek · Angaben von Hardcover und Audible')
  })

  it('shows the cover in the colour of the book when there is no picture, and the picture when there is one', async () => {
    serve(arvel({ cover_url: '/api/v1/discover/books/g2/cover?v=abc123def456' }))
    await page.show(app(), at('g2'))
    expect(q('.d-cover img')!.getAttribute('src')).toBe('/api/v1/discover/books/g2/cover?v=abc123def456')
    page.close()
    serve()
    await page.show(app(), at('g2'))
    expect(q('.d-cover [data-testid="cover-drawn"]')).toBeTruthy()
  })
})

describe('the forms', () => {
  it('stand as twins: the e-book with its pages, the recording with its running time and narrator, the rest as chips', async () => {
    serve()
    await page.show(app(), at('g2'))
    const twins = all('[data-testid="twin"]')
    expect(twins.map((t) => plain(t.querySelector('h4')!.textContent))).toEqual(['E-Book · Deutsch', 'Hörbuch · ungekürzt'])
    expect(twins.map((t) => plain(t.querySelector('.tw-sub')!.textContent))).toEqual(['320 Seiten', '9 Std 0 Min · gelesen von Clara Vienne'])
    expect(twins.map((t) => t.querySelector('.tw-pos')!.textContent)).toEqual(['Nicht in deiner Bibliothek', 'Nicht in deiner Bibliothek'])
    // Without the right to fetch books the cards offer nothing to press.
    expect(twins.every((t) => t.querySelector('button.go') === null)).toBe(true)
    expect(all('[data-testid="more-editions"] span').map((s) => plain(s.textContent))).toEqual(['Hörbuch · gekürzt · 4 Std 0 Min'])
    expect(q('.d-h')!.textContent).toBe('Ein Buch, mehrere Fassungen')
  })

  it('say that the sources do not know a form, in a dashed card', async () => {
    serve(arvel({ forms_known: [{ kind: 'ebook', language: 'de', variant: '', minutes: null, narrators: [], pages: null, source: 'hardcover' }], sources: ['hardcover', 'audible'] }))
    await page.show(app(), at('g2'))
    const missing = q('[data-testid="twin-missing"]')!
    expect(plain(missing.querySelector('h4')!.textContent)).toBe('Kein Hörbuch bekannt')
    expect(plain(missing.querySelector('.tw-sub')!.textContent)).toBe('Hardcover und Audible kennen diese Fassung nicht.')
    expect(missing.querySelector('button')).toBeNull()
    expect(q('.d-h')!.textContent).toBe('Ein Buch, eine Fassung')
  })

  it('give the day a book that is yet to come comes out', async () => {
    serve(arvel({ upcoming: true, released: '2026-12-04' }))
    await page.show(app(), at('g2'))
    expect(plain(q('[data-testid="twin"] .tw-pos')!.textContent)).toBe('Erscheint am 4. Dezember 2026')
    expect(q('[data-testid="twin"] .tw-pos b')!.textContent).toBe('4. Dezember 2026')
  })

  it('say that the other forms are being looked for, and look again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve(arvel({ pending: true }))
      await page.show(app(), at('g2'))
      expect(q('[data-testid="looking"]')!.textContent).toBe('nexbooks sieht noch nach weiteren Fassungen …')
      serve(arvel({ pending: false }))
      await vi.advanceTimersByTimeAsync(5500)
      await vi.waitFor(() => expect(q('[data-testid="looking"]')).toBeNull())
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the wish', () => {
  it('puts the book on the list, says "Gemerkt", and takes it off again', async () => {
    serve(arvel(), {
      'PUT /api/v1/wishlist/g2': { count: 1, items: [card('g2', { title: 'Glas und Asche', wished: true })] },
      'DELETE /api/v1/wishlist/g2': { count: 0, items: [] },
    })
    await page.show(app(), at('g2'))
    const button = q('.want button')!
    await page.click(button)
    expect(server.changes()).toEqual(['PUT /api/v1/wishlist/g2'])
    expect(button.textContent).toBe('Gemerkt')
    expect(button.classList.contains('primary')).toBe(false)
    await page.click(button)
    expect(server.changes()).toEqual(['PUT /api/v1/wishlist/g2', 'DELETE /api/v1/wishlist/g2'])
    expect(button.textContent).toBe('Merken')
  })

  it('says "Gemerkt" from the start when the book is on the list', async () => {
    serve(arvel({ wished: true }))
    await page.show(app(), at('g2'))
    expect(q('.want button')!.textContent).toBe('Gemerkt')
  })
})

describe('a book that is on the shelf', () => {
  it('opens its page on the shelf, because this one knows the book only from the catalogue', async () => {
    serve(arvel({ owned_work_id: 12 }))
    await page.show(app(), at('g2'))
    await idle()
    expect(q('[data-testid="where"]')!.textContent).toBe('/work/12')
  })
})

describe('the series and the author', () => {
  const own = work(9, { title: 'Die Krone der Weberinnen', genre: 'fantasy', series: { id: 3, name: 'Die Chroniken von Arvel', pos: 1 } })
  const volumes = [
    volume(1, 'own', { work: own }),
    volume(2, 'missing', { book: card('g2', { title: 'Glas und Asche', series: 'Die Chroniken von Arvel', series_pos: 2, genre: 'fantasy' }) }),
    volume(3, 'soon', { book: card('g3', { title: 'Der Thron aus Bernstein', series: 'Die Chroniken von Arvel', series_pos: 3, genre: 'fantasy', upcoming: true, released: '2027-02-01' }) }),
  ]

  it('stand as small shelves: the volumes the person has as spines, the ones they lack as ghosts, this one marked', async () => {
    serve(arvel({ volumes, more: [work(21, { title: 'Briefe aus Triest', authors: [{ id: 5, name: 'Iris Quist' }] })] }))
    await page.show(app(), at('g2'))
    const shelves = all('.mini-shelf')
    expect(shelves).toHaveLength(2)
    const spines = [...shelves[0]!.querySelectorAll<HTMLElement>('.spine')]
    expect(spines.map((s) => s.className.includes('ghost'))).toEqual([false, true, true])
    expect(spines.map((s) => s.classList.contains('soon'))).toEqual([false, false, true])
    expect(spines.map((s) => s.classList.contains('current'))).toEqual([false, true, false])
    expect(plain(spines[1]!.querySelector('.t')!.textContent)).toBe('Band 2 · Glas und Asche')
    expect(spines.slice(1).map((s) => s.querySelector('.a')!.textContent)).toEqual(['FEHLT', 'BALD'])
    expect(all('.d-h').map((h) => plain(h.textContent))).toEqual(['Ein Buch, mehrere Fassungen', 'Reihe · Die Chroniken von Arvel', 'Mehr von Iris Quist'])
    expect([...shelves[1]!.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))).toEqual(['21'])
  })

  it('lead from a ghost to the page of that book, and from a spine to the book of the shelf, replacing this page', async () => {
    serve(arvel({ volumes }), { 'GET /api/v1/discover/books/g3': bookPage('g3', { title: 'Der Thron aus Bernstein' }) })
    await page.show(app(), at('g2', { background: BACK }))
    await page.click(all('.mini-shelf .spine')[2] as HTMLElement)
    // The page of the other book stands in the place of this one.
    expect(q('.d-title')!.textContent).toBe('Der Thron aus Bernstein')
    page.close()
    serve(arvel({ volumes }))
    await page.show(app(), at('g2', { background: BACK }))
    await page.click(all('.mini-shelf .spine')[0] as HTMLElement)
    expect(q('[data-testid="where"]')!.textContent).toBe('/work/9')
  })

  it('leave out the shelf of the series when the book is the only one we know of it', async () => {
    serve(arvel({ volumes: [volumes[1]!] }))
    await page.show(app(), at('g2'))
    expect(all('.mini-shelf')).toHaveLength(0)
  })
})

describe('leaving, and a book that is gone', () => {
  it('goes back to the page it was opened from with Escape and with the button', async () => {
    serve()
    await page.show(app(), at('g2', { background: BACK, from: null }))
    expect(q('.d-back')!.textContent).toBe('Entdecken')
    await page.click(q('.d-back'))
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover?list=new')
    page.close()
    serve()
    await page.show(app(), at('g2', { background: BACK, from: null }))
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    await idle()
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover?list=new')
  })

  it('keeps the card it was opened from on the page when the page cannot be read, and says why', async () => {
    serve()
    server.fails = { 'GET /api/v1/discover/books/g2': { status: 429, code: 'discover_too_often' } }
    const origin = card('g2', { title: 'Glas und Asche', authors: ['Iris Quist'], genre: 'fantasy' })
    await page.show(app(), at('g2', { background: BACK, card: origin }))
    expect(q('.d-title')!.textContent).toBe('Glas und Asche')
    expect(q('[data-testid="foreign-failure"]')!.textContent).toBe('Zu viele Anfragen auf einmal. Warte eine Minute.')
  })

  it('tells that there is no such book, in words', async () => {
    serve()
    server.fails = { 'GET /api/v1/discover/books/g2': { status: 404, code: 'not_found' } }
    await page.show(app(), at('g2'))
    expect(q('[data-testid="foreign-failure"]')!.textContent).toBe('Dieses Buch gibt es nicht mehr, oder der Link stimmt nicht.')
    expect(q('.d-back')).toBeTruthy()
  })

  it('locks the page behind it and gives the lock back; the focus is on the page', async () => {
    serve()
    await page.show(app(), at('g2'))
    const layer = q('[data-testid="foreign"]')!
    expect(layer.getAttribute('role')).toBe('dialog')
    expect(document.activeElement).toBe(layer)
    expect(document.body.classList.contains('lock')).toBe(true)
    page.close()
    expect(document.body.classList.contains('lock')).toBe(false)
  })

  it('comes in English, with no dash as a pause', async () => {
    await changeLanguage('en', false)
    serve(arvel({ upcoming: true, released: '2026-12-04' }))
    await page.show(app(), at('g2'))
    expect(q('[data-testid="notmine"]')!.textContent).toBe('Not in your library · details from Hardcover and Audible')
    expect(q('.want button')!.textContent).toBe('Save')
    expect(plain(q('[data-testid="twin"] .tw-pos')!.textContent)).toBe('Out on December 4, 2026')
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})
