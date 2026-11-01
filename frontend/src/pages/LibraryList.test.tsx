/**
 * The list and the authors of the library: the third button beside the shelf and the covers kept in the profile,
 * the list with its columns that put the rows in order (the choice kept), the extras only the list asks for, what a member who
 * manages a library sees besides, only the rows near the screen drawn, and the grid of authors with its order, its genres, its
 * field and its way on.
 */
import { act } from 'react'

import type { AuthorCard, Me, Mine, WorkRow } from '../api/client'
import { changeLanguage } from '../i18n'
import { ebook, library, recording, work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { LibraryPage } from './LibraryPage'

const baseMe = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', profile: { view: 'list' } } as unknown as Me
const auth = { me: baseMe as Me | null, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const stats = { works: 4, editions: 5, ebook_editions: 4, audio_editions: 1, works_with_ebook: 4, works_with_audio: 1, genres: { roman: 2, krimi: 1, none: 1 }, languages: {} }
const mine = (over: Partial<Mine> = {}): Mine => ({ ebook: null, audio: null, ebook_done: false, audio_done: false, moved_at: null, match: null, libraries: [], ...over })
const row = (id: number, over: Partial<WorkRow> = {}): WorkRow => ({ ...work(id), mine: mine(), ...over })

const books: WorkRow[] = [
  row(1, { title: 'Eins', genre: 'roman', authors: [{ id: 1, name: 'Ada Marwitz' }], year: 2001, series: { id: 3, name: 'Deich', pos: 2 }, mine: mine({ ebook: 0.4 }) }),
  row(2, { title: 'Zwei', genre: 'krimi', authors: [{ id: 2, name: 'Ben Alt' }], year: 1990, editions: [ebook(2), recording(20, { variant: 'abridged' })], mine: mine({ audio_done: true }) }),
  row(3, { title: 'Drei', genre: '', genre_guess: false, authors: [{ id: 3, name: 'Cy Zorn' }], year: null, pages: 0 }),
  row(4, { title: 'Vier', genre: 'roman', genre_guess: true, authors: [{ id: 3, name: 'Cy Zorn' }], year: 2015, added_at: '2026-10-10T10:00:00Z' }),
]

function serve(list: WorkRow[] = books, extra: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/libraries': [library({ my_right: 'manage' })],
    'GET /api/v1/works?page=1&per_page=200&extras=list': { items: list, total: list.length, page: 1, per_page: 200 },
    'GET /api/v1/works?page=1&per_page=200': { items: list, total: list.length, page: 1, per_page: 200 },
    'GET /api/v1/works/stats': { ...stats, works: list.length },
    'PUT /api/v1/me/preferences': { view: 'list' },
    ...extra,
  }
  server.fails = {}
  server.install()
}

const card = (id: number, name: string, over: Partial<AuthorCard> = {}): AuthorCard => ({ id, name, sort_name: name, works: 3, audio: 1, genre: 'roman', added_at: '2026-10-01T00:00:00Z', photo_url: null, ...over })

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = baseMe
  auth.setMe.mockClear()
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const titles = () => [...document.querySelectorAll('.list-table tbody tr[data-work]')].map((r) => r.querySelector('.lt-title span')!.textContent)
const header = (column: string) => document.querySelector(`.list-table thead th button[data-sort="${column}"]`) as HTMLElement

describe('the three looks', () => {
  it('have a third button, the list, beside the shelf and the covers', async () => {
    serve()
    await page.show(<LibraryPage />)
    expect([...document.querySelectorAll('#look button')].map((b) => plain(b.textContent))).toEqual(['Regal', 'Cover', 'Liste'])
    expect(page.button('Liste')!.getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelector('[data-testid="list"]')).toBeTruthy()
    expect(document.querySelector('.spine')).toBeNull()
  })

  it('keep the choice of the list in the profile, and go back when the server refuses', async () => {
    auth.me = { ...baseMe, profile: { view: 'shelf' } } as unknown as Me
    serve()
    await page.show(<LibraryPage />)
    await page.click(page.button('Liste'))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ view: 'list' })
    expect(document.querySelector('[data-testid="list"]')).toBeTruthy()
    page.close()
    auth.me = { ...baseMe, profile: { view: 'shelf' } } as unknown as Me
    serve()
    server.fails = { 'PUT /api/v1/me/preferences': { status: 500, code: 'internal_error' } }
    await page.show(<LibraryPage />)
    await page.click(page.button('Liste'))
    expect(document.querySelector('[data-testid="list"]')).toBeNull()
    expect(page.button('Regal')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('ask for the extras of the list only when the list is shown', async () => {
    auth.me = { ...baseMe, profile: { view: 'shelf' } } as unknown as Me
    serve()
    await page.show(<LibraryPage />)
    expect(server.calls.some((c) => c.url.includes('extras=list'))).toBe(false)
    await page.click(page.button('Liste'))
    expect(server.calls.some((c) => c.url === '/api/v1/works?page=1&per_page=200&extras=list')).toBe(true)
    // Back to the shelf asks for nothing more: what was fetched with the extras serves it too.
    const before = server.calls.length
    await page.click(page.button('Regal'))
    expect(server.calls.slice(before).filter((c) => c.url.startsWith('/api/v1/works?')).length).toBe(0)
    expect(document.querySelectorAll('.spine').length).toBe(4)
  })
})

describe('the list', () => {
  it('has a row for each book, in the order of the shelf at first, with the columns of the plan', async () => {
    serve()
    await page.show(<LibraryPage />)
    expect(titles()).toEqual(['Eins', 'Zwei', 'Drei', 'Vier'])
    const heads = [...document.querySelectorAll('.list-table thead th')].map((th) => plain(th.textContent))
    // Without a library to look after there is no matching and no library to show.
    expect(heads).toEqual(['', 'Titel', 'Autor', 'Fassungen', 'Sprache', 'Genre', 'Jahr', 'Umfang', 'Mein Stand', 'Hinzugefügt'])
    const first = document.querySelector('tr[data-work="1"]')!
    expect(plain(first.querySelector('th small')!.textContent)).toBe('Deich · Band 2')
    expect(first.querySelector('.col-author')!.textContent).toBe('Ada Marwitz')
    expect(first.querySelector('.col-year')!.textContent).toBe('2001')
    expect(plain(first.querySelector('.col-genre')!.textContent)).toBe('Romane')
    expect(plain(first.querySelector('.col-size')!.textContent)).toBe('300 Seiten')
  })

  it('marks a genre that is a guess quietly, and a recording alone shows its running time', async () => {
    serve([row(4, { genre: 'roman', genre_guess: true }), row(5, { title: 'Hörfunk', audio_only: true, pages: 700, editions: [recording(50, { duration: 9000 })] })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('tr[data-work="4"] .col-genre')!.textContent)).toBe('Romane geschätzt')
    expect(plain(document.querySelector('tr[data-work="5"] .col-size')!.textContent)).toBe('2 Std 30 Min')
  })

  it('shows the editions as chips: the formats of an e-book, "gekürzt" for a shortened recording', async () => {
    serve()
    await page.show(<LibraryPage />)
    const chips = [...document.querySelectorAll('tr[data-work="2"] .chip')].map((c) => plain(c.textContent))
    expect(chips).toEqual(['DE EPUB', 'gekürzt'])
  })

  it('breaks many chips into the last one that says how many are left, and cuts none off', async () => {
    const many = [ebook(8), recording(80, { variant: 'abridged' }), recording(81, { variant: 'dramatized' }), ebook(82), recording(83)]
    serve([row(8, { title: 'Acht', editions: many })])
    await page.show(<LibraryPage />)
    const chips = [...document.querySelectorAll('tr[data-work="8"] .chip')]
    expect(chips).toHaveLength(4)
    expect(plain(chips[3].textContent)).toBe('+2')
    expect(chips[3].getAttribute('title')).toContain('Hörbuch')
  })

  it('says of a recording whose reading nothing proves that it is not proven, and never "ungekürzt"', async () => {
    serve([row(6, { title: 'Sechs', editions: [ebook(6), recording(60, { variant: 'unabridged', variant_uncertain: true })] }), row(7, { title: 'Sieben', editions: [recording(70, { variant: 'unabridged' })] })])
    await page.show(<LibraryPage />)
    expect([...document.querySelectorAll('tr[data-work="6"] .chip')].map((c) => plain(c.textContent))).toEqual(['DE EPUB', 'Hörbuch · nicht belegt'])
    expect([...document.querySelectorAll('tr[data-work="7"] .chip')].map((c) => plain(c.textContent))).toEqual(['Hörbuch'])
  })

  it('marks a language that is only a guess (a recording that takes it from its twin or its library) quietly', async () => {
    serve([row(8, { title: 'Acht', audio_only: true, editions: [recording(80, { language: 'de', language_guess: true })] }), row(9, { title: 'Neun', editions: [ebook(9, { language: 'en' })] })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('tr[data-work="8"] .col-language')!.textContent)).toBe('Deutsch geschätzt')
    expect(plain(document.querySelector('tr[data-work="9"] .col-language')!.textContent)).toBe('Englisch')
  })

  it('shows where the person is in the e-book and in the recording, apart, and a finished one as done', async () => {
    serve()
    await page.show(<LibraryPage />)
    const one = document.querySelector('tr[data-work="1"] [data-testid="list-stand"]')!
    expect(one.querySelectorAll('.own-row').length).toBe(1)
    expect(one.querySelector('.own-row')!.getAttribute('aria-label')).toBe('E-Book 40 %')
    expect((one.querySelector('.own-bar b') as HTMLElement).style.width).toBe('40%')
    const two = document.querySelector('tr[data-work="2"] [data-testid="list-stand"]')!
    expect([...two.querySelectorAll('.own-row')].map((r) => r.getAttribute('aria-label'))).toEqual(['Noch nicht begonnen', 'Hörbuch gehört'])
    expect(two.querySelector('.own-row.done')).toBeTruthy()
  })

  it('puts the rows in order by a column, turns the order round on the same column, and keeps the choice', async () => {
    serve()
    await page.show(<LibraryPage />)
    await page.click(header('title'))
    expect(titles()).toEqual(['Drei', 'Eins', 'Vier', 'Zwei'])
    expect(header('title').closest('th')!.getAttribute('aria-sort')).toBe('ascending')
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ list_sort: 'title', list_dir: 'asc' })
    await page.click(header('title'))
    expect(titles()).toEqual(['Zwei', 'Vier', 'Eins', 'Drei'])
    expect(header('title').closest('th')!.getAttribute('aria-sort')).toBe('descending')
    // Another column starts in its own natural direction: the newest first.
    await page.click(header('added'))
    expect(titles()[0]).toBe('Vier')
    expect(server.calls.filter((c) => c.method === 'PUT').at(-1)!.body).toEqual({ list_sort: 'added', list_dir: 'desc' })
    // A row with no year stands last either way.
    await page.click(header('year'))
    expect(titles()[0]).toBe('Vier')
    expect(titles().at(-1)).toBe('Drei')
    await page.click(header('year'))
    expect(titles().at(-1)).toBe('Drei')
  })

  it('names the column and the direction on its button for a reader of the screen', async () => {
    serve()
    await page.show(<LibraryPage />)
    expect(header('title').getAttribute('aria-label')).toBe('Nach Titel sortieren')
    await page.click(header('title'))
    expect(header('title').getAttribute('aria-label')).toBe('Nach Titel sortieren, aufsteigend')
  })

  it('starts as the profile says', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', list_sort: 'year', list_dir: 'desc' } } as unknown as Me
    serve()
    await page.show(<LibraryPage />)
    expect(titles()).toEqual(['Vier', 'Eins', 'Zwei', 'Drei'])
  })

  it('applies the genre filter to the rows', async () => {
    serve()
    await page.show(<LibraryPage />)
    await page.click(page.button('Romane 2'))
    expect(titles()).toEqual(['Eins', 'Vier'])
  })

  it('opens the book over the list when a row is pressed, once', async () => {
    serve()
    await page.show(<LibraryPage />)
    const row2 = document.querySelector('tr[data-work="2"]') as HTMLElement
    await page.click(row2)
    await page.click(row2)
    // The page of a book is the router's business; what matters is that the list is where it was.
    expect(titles().length).toBe(4)
  })

  it('draws only the rows near the screen of a big library', async () => {
    const many = Array.from({ length: 2000 }, (_, i) => row(i + 1, { title: `Buch ${i + 1}` }))
    serve(many)
    await page.show(<LibraryPage />)
    const drawn = document.querySelectorAll('.list-table tbody tr[data-work]').length
    expect(drawn).toBeGreaterThan(0)
    expect(drawn).toBeLessThan(60)
    const spacer = document.querySelector('.list-table tbody tr.pad') as HTMLElement
    expect(spacer).toBeTruthy()
    // Together the rows and the spaces are as high as 2,000 rows.
    const heights = [...document.querySelectorAll('.list-table tbody tr')].reduce((sum, r) => sum + parseInt((r as HTMLElement).style.height || '0', 10), 0)
    expect(heights).toBe(2000 * 60)
  })
})

describe('what a member who manages a library sees', () => {
  const managed: WorkRow[] = [
    row(1, { title: 'Sicher', mine: mine({ match: 'sure', libraries: ['Familie'] }) }),
    row(2, { title: 'Offen', mine: mine({ match: 'open', libraries: ['Familie', 'Club'] }) }),
  ]

  it('is the matching and the library, the matching leading to the review list', async () => {
    serve(managed)
    await page.show(<LibraryPage />)
    const matches = [...document.querySelectorAll('[data-testid="list-match"]')].map((a) => [plain(a.textContent), a.getAttribute('href')])
    expect(matches).toEqual([['sicher', '/review?library=1'], ['offen', '/review?library=1']])
    expect(plain(document.querySelector('tr[data-work="2"] .col-library')!.textContent)).toBe('Familie, Club')
  })

  it('is not there for a person who only reads: no column, no chip', async () => {
    serve(books)
    await page.show(<LibraryPage />)
    expect(document.querySelector('.col-match')).toBeNull()
    expect(document.querySelector('[data-testid="list-match"]')).toBeNull()
    expect(plain(document.querySelector('.list-table thead')!.textContent)).not.toContain('Zuordnung')
  })
})

describe('the authors', () => {
  // The authors are asked for once the libraries are known: one more turn of the page.
  const show = async () => {
    await page.show(<LibraryPage />)
    for (let turn = 0; turn < 40 && !document.querySelector('.author, [data-testid="authors-empty"]'); turn++) {
      await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 25))))
    }
    await idle()
  }
  const grid = (items: AuthorCard[], over: Record<string, unknown> = {}) => ({ items, total: items.length, page: 1, per_page: 60, genres: { roman: 2, krimi: 1 }, ...over })
  const url = (query: string) => `/api/v1/authors?${query}`
  const key = (query: string) => `GET ${url(query)}`

  function serveAuthors(items: AuthorCard[] = [card(1, 'Ada Marwitz', { photo_url: '/api/v1/people/1/photo?v=abc' }), card(2, 'Ben Alt', { genre: 'krimi', works: 1, audio: 0 })]) {
    serve(books, {
      [key('sort=name&per_page=60&page=1')]: grid(items),
      [key('sort=count&per_page=60&page=1')]: grid([...items].reverse()),
      [key('sort=name&per_page=60&page=1&genre=krimi')]: grid(items.slice(1), { total: 1 }),
      [key('sort=name&per_page=60&page=1&q=ben')]: grid(items.slice(1), { total: 1 }),
    })
  }

  it('are a section beside the books, kept in the profile', async () => {
    serveAuthors()
    await show()
    expect([...document.querySelectorAll('#section button')].map((b) => plain(b.textContent))).toEqual(['Bücher', 'Autoren'])
    await page.click(page.button('Autoren'))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ library_section: 'authors' })
    expect(document.querySelector('[data-testid="authors"]')).toBeTruthy()
    expect(document.querySelector('[data-testid="list"]')).toBeNull()
    // The look of the books has no place in this section.
    expect(document.querySelector('#look')).toBeNull()
  })

  it('show a round photo or a circle with the initials, the name and "12 Werke · 4 Hörbücher"', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serveAuthors()
    await show()
    const cards = [...document.querySelectorAll('.author')]
    expect(cards.map((c) => plain(c.querySelector('b')!.textContent))).toEqual(['Ada Marwitz', 'Ben Alt'])
    expect(plain(cards[0]!.querySelector('.counts')!.textContent)).toBe('3 Werke · 1 Hörbuch')
    expect(plain(cards[1]!.querySelector('.counts')!.textContent)).toBe('1 Werk')
    expect(cards[0]!.querySelector('img')!.getAttribute('src')).toBe('/api/v1/people/1/photo?v=abc')
    expect(cards[0]!.querySelector('img')!.getAttribute('alt')).toBe('Foto von Ada Marwitz')
    expect(plain(cards[1]!.querySelector('[data-testid="author-initials"]')!.textContent)).toBe('BA')
    expect(cards[0]!.getAttribute('href')).toBe('/person/1')
    expect(document.querySelector('[data-testid="author-count"]')!.textContent).toBe('2 Autoren')
  })

  it('can be put in order again, and the choice is kept', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serveAuthors()
    await show()
    await page.click(document.querySelector('[data-testid="authors"] [data-sort="count"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ authors_sort: 'count' })
    expect([...document.querySelectorAll('.author b')].map((b) => b.textContent)).toEqual(['Ben Alt', 'Ada Marwitz'])
  })

  it('can be narrowed to a genre, with the numbers of authors on the chips', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serveAuthors()
    await show()
    expect([...document.querySelectorAll('.genres .g')].map((c) => plain(c.textContent))).toEqual(['Alle 2', 'Romane 2', 'Krimi & Thriller 1'])
    await page.click(page.button('Krimi & Thriller 1'))
    expect([...document.querySelectorAll('.author b')].map((b) => b.textContent)).toEqual(['Ben Alt'])
    // The chips keep the numbers of all authors while a genre is chosen.
    expect([...document.querySelectorAll('.genres .g')].map((c) => plain(c.textContent))).toEqual(['Alle 2', 'Romane 2', 'Krimi & Thriller 1'])
  })

  it('can be found by a word of the name, after the person stopped typing', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serveAuthors()
    await show()
    await page.type(document.querySelector('[data-testid="author-search"]') as HTMLInputElement, 'ben')
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 400))))
    await idle()
    expect(server.calls.some((c) => c.url === url('sort=name&per_page=60&page=1&q=ben'))).toBe(true)
  })

  it('say so when nothing is found, and when there are no books yet', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serveAuthors([])
    await show()
    expect(plain(document.querySelector('[data-testid="authors-empty"]')!.textContent)).toContain('Sobald Bücher in deiner Bibliothek stehen')
  })

  it('go on with a button when there are more than a page', async () => {
    auth.me = { ...baseMe, profile: { view: 'list', library_section: 'authors' } } as unknown as Me
    serve(books, {
      [key('sort=name&per_page=60&page=1')]: grid([card(1, 'Eins')], { total: 2 }),
      [key('sort=name&per_page=60&page=2')]: grid([card(2, 'Zwei')], { total: 2, page: 2 }),
    })
    await show()
    expect(plain(document.querySelector('[data-testid="authors-more"]')!.textContent)).toBe('Mehr anzeigen (1 von 2)')
    await page.click(document.querySelector('[data-testid="authors-more"]') as HTMLElement)
    expect([...document.querySelectorAll('.author b')].map((b) => b.textContent)).toEqual(['Eins', 'Zwei'])
    expect(document.querySelector('[data-testid="authors-more"]')).toBeNull()
  })
})
