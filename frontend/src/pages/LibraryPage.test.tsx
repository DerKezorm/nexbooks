/**
 * The library page: the line of numbers above the grid of covers, the cover of every book
 * (the picture, or one drawn in its colour), the chips of its editions with the formats, more books on request, and the
 * empty pages for everybody who has no books yet.
 */
import type { Me, WorkBrief } from '../api/client'
import { changeLanguage } from '../i18n'
import { Page, Server, plain } from '../test/harness'
import { LibraryPage } from './LibraryPage'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', profile: { view: 'grid' } } as unknown as Me
const auth = { me: me as Me | null, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function work(id: number, over: Partial<WorkBrief> = {}): WorkBrief {
  return {
    id, title: `Buch ${id}`, authors: [{ id: 1, name: 'Ada Feldkamp' }], series: null, genre: 'roman', genre_guess: false, color: '#7d2f2b', cover_url: null, pages: 300,
    year: 2020, pattern: id % 5, tall: 0.5, gold: false, audio_only: false, added_at: '2026-10-01T10:00:00Z',
    editions: [{ id, library_id: 1, kind: 'ebook', variant: '', variant_uncertain: false, language: 'de', language_guess: false, formats: ['epub'], pages: 300, duration: 0, narrators: [] }],
    ...over,
  }
}
const library = { id: 1, name: 'Familie', kind: 'both', my_right: 'read', state: 'idle', counts: { ebooks: 3, audiobooks: 1, files: 4, bytes: 1000, problems: 0, review: 0, entries: 0, pending: 0 }, matching: 'idle', resting: [] }
const stats = { works: 400, editions: 583, ebook_editions: 400, audio_editions: 183, works_with_ebook: 400, works_with_audio: 169, genres: {}, languages: {} }

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  server.answers = {}
  server.fails = {}
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function serve(works: WorkBrief[], total = works.length) {
  server.answers = {
    'GET /api/v1/libraries': [library],
    'GET /api/v1/works?page=1&per_page=200': { items: works, total, page: 1, per_page: 200 },
    'GET /api/v1/works/stats': { ...stats, works: total },
  }
  server.install()
}

describe('the line above the grid', () => {
  it('counts works, editions and the works with an audiobook, in its words', async () => {
    serve([work(1)], 400)
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.lead p')!.textContent)).toBe('400 Werke · 583 Fassungen · 169 davon mit Hörbuch')
    expect(document.querySelectorAll('.lead p b').length).toBe(3)
  })

  it('writes the figures with the separator of the language and speaks English too', async () => {
    serve([work(1)], 2000)
    await changeLanguage('en', false)
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.lead p')!.textContent)).toBe('2,000 works · 583 editions · 169 of them with an audiobook')
  })

  it('says work and not works for one', async () => {
    server.answers = {
      'GET /api/v1/libraries': [library],
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 1, editions: 1, works_with_audio: 0 },
    }
    server.install()
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.lead p')!.textContent)).toBe('1 Werk · 1 Fassung · 0 davon mit Hörbuch')
  })
})

describe('the grid of covers', () => {
  it('draws a book without a cover in its colour, with author, title and series, and the real picture when there is one', async () => {
    serve([
      work(1, { title: 'Die Salzwiese', series: { id: 4, name: 'Deich-Krimis', pos: 2 }, color: '#7d2f2b' }),
      work(2, { title: 'Mit Bild', cover_url: '/api/v1/works/2/cover?v=abcdef012345', color: '#215258' }),
    ])
    await page.show(<LibraryPage />)
    const drawn = document.querySelector('[data-testid="cover-drawn"]') as HTMLElement
    expect(plain(drawn.textContent)).toBe('Ada FeldkampDie SalzwieseDeich-Krimis · 2')
    expect(drawn.style.getPropertyValue('--c')).toBe('#7d2f2b')
    // The text colour follows the design's rule: light text on a dark cover.
    expect(drawn.style.getPropertyValue('--ink')).toBe('#efe6d2')
    const picture = document.querySelector('[data-testid="cover-picture"] img') as HTMLImageElement
    expect(picture.getAttribute('src')).toBe('/api/v1/works/2/cover?v=abcdef012345')
    expect(picture.getAttribute('loading')).toBe('lazy')
  })

  it('lets a light cover wear dark text and a recording alone be a square sleeve with headphones', async () => {
    serve([
      work(1, { color: '#d9cba9' }),
      work(2, { audio_only: true, color: '#1f304b', editions: [{ id: 9, library_id: 1, kind: 'audio', variant: 'abridged', variant_uncertain: false, language: 'de', language_guess: false, formats: ['m4b'], pages: 0, duration: 3600, narrators: [] }] }),
    ])
    await page.show(<LibraryPage />)
    const covers = [...document.querySelectorAll<HTMLElement>('.cover')]
    expect(covers[0].style.getPropertyValue('--ink')).toBe('#2a2420')
    expect(covers[1].classList.contains('is-audio')).toBe(true)
    expect(covers[1].querySelector('.c-audio svg')).toBeTruthy()
  })

  it('shows a chip for every edition: the language and the formats of an e-book, the variant of a recording', async () => {
    serve([
      work(1, {
        editions: [
          { id: 1, library_id: 1, kind: 'ebook', variant: '', variant_uncertain: false, language: 'de', language_guess: false, formats: ['epub', 'azw3', 'mobi'], pages: 300, duration: 0, narrators: [] },
          { id: 2, library_id: 1, kind: 'audio', variant: 'unabridged', variant_uncertain: true, language: 'de', language_guess: false, formats: ['m4b'], pages: 0, duration: 3600, narrators: [] },
          { id: 3, library_id: 1, kind: 'audio', variant: 'dramatized', variant_uncertain: false, language: 'de', language_guess: false, formats: ['mp3'], pages: 0, duration: 3600, narrators: [] },
        ],
      }),
    ])
    await page.show(<LibraryPage />)
    const chips = [...document.querySelectorAll('.chips .chip')].map((chip) => ({ kind: chip.getAttribute('data-kind'), text: chip.textContent }))
    expect(chips).toEqual([
      { kind: 'ebook', text: 'DE' },
      { kind: 'formats', text: 'EPUB · AZW3 · MOBI' },
      { kind: 'audio', text: '' },
      { kind: 'audio', text: 'Hörspiel' },
    ])
  })

  it('names the book and its author for a screen reader, and has a title and an author line', async () => {
    serve([work(1, { title: 'Das Haus am Nordkanal' })])
    await page.show(<LibraryPage />)
    const book = document.querySelector('[data-testid="book"]')!
    expect(book.getAttribute('aria-label')).toBe('Das Haus am Nordkanal, Ada Feldkamp')
    expect(book.querySelector('b')!.textContent).toBe('Das Haus am Nordkanal')
    expect(book.querySelector('small')!.textContent).toBe('Ada Feldkamp')
  })

  it('fetches the next page by itself while there are more books, and shows no book twice', async () => {
    const first = Array.from({ length: 200 }, (_, index) => work(index + 1))
    serve(first, 202)
    server.answers['GET /api/v1/works?page=2&per_page=200'] = { items: [work(200), work(201), work(202)], total: 202, page: 2, per_page: 200 }
    await page.show(<LibraryPage />)
    expect(document.querySelectorAll('[data-testid="book"]').length).toBe(202)
    expect(server.calls.filter((call) => call.url.startsWith('/api/v1/works?')).map((call) => call.url)).toEqual(['/api/v1/works?page=1&per_page=200', '/api/v1/works?page=2&per_page=200'])
    expect(document.querySelector('[data-testid="library-loading-more"]')).toBeNull()
  })
})

describe('when there is nothing to show', () => {
  it('sends the operator to the settings and tells a member that the operator shares a library', async () => {
    server.answers = { 'GET /api/v1/libraries': [] }
    server.install()
    await page.show(<LibraryPage />)
    const empty = document.querySelector('[data-testid="library-empty"]')!
    expect(empty.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=libraries')
    page.close()
    auth.me = { ...me, role: 'member' } as Me
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="library-empty"] a')).toBeNull()
    expect(plain(document.querySelector('[data-testid="library-empty"]')!.textContent)).toContain('Der Betreiber gibt dir eine Bibliothek frei.')
  })

  it('says that a library is being read while it is, and that no books were found when it is not', async () => {
    server.answers = {
      'GET /api/v1/libraries': [{ ...library, state: 'running' }],
      'GET /api/v1/works?page=1&per_page=200': { items: [], total: 0, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 0, editions: 0 },
    }
    server.install()
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="library-nothing"]')!.textContent)).toContain('liest deine Ordner gerade ein')
    page.close()
    server.answers['GET /api/v1/libraries'] = [library]
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="library-nothing"]')!.textContent)).toContain('noch keine Bücher gefunden')
  })

  it('tells what went wrong in words when the server refuses', async () => {
    server.answers = {}
    server.fails = { 'GET /api/v1/libraries': { status: 500, code: 'internal_error' } }
    server.install()
    await page.show(<LibraryPage />)
    expect(document.querySelector('[role="alert"]')!.textContent).toBeTruthy()
  })

  it('never uses a dash as a pause', async () => {
    serve([work(1)])
    await page.show(<LibraryPage />)
    expect(document.querySelector('main, .view')!.textContent).not.toMatch(/[–—]/)
  })
})

describe('while a scan runs', () => {
  it('looks again every few seconds and stops when it is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    server.answers = {
      'GET /api/v1/libraries': [{ ...library, state: 'running' }],
      'GET /api/v1/works?page=1&per_page=200': { items: [], total: 0, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 0 },
    }
    server.install()
    await page.show(<LibraryPage />)
    const before = server.calls.filter((call) => call.url === '/api/v1/libraries').length
    // The scan finishes meanwhile: the next look finds the books.
    server.answers['GET /api/v1/libraries'] = [library]
    server.answers['GET /api/v1/works?page=1&per_page=200'] = { items: [work(1)], total: 1, page: 1, per_page: 200 }
    server.answers['GET /api/v1/works/stats'] = { ...stats, works: 1, editions: 1, works_with_audio: 0 }
    await vi.advanceTimersByTimeAsync(3100)
    const { idle } = await import('../test/wait')
    await idle()
    expect(server.calls.filter((call) => call.url === '/api/v1/libraries').length).toBeGreaterThan(before)
    expect(document.querySelectorAll('[data-testid="book"]').length).toBe(1)
    const after = server.calls.filter((call) => call.url === '/api/v1/libraries').length
    await vi.advanceTimersByTimeAsync(10_000)
    expect(server.calls.filter((call) => call.url === '/api/v1/libraries').length).toBe(after)
  })
})

describe('books that come in while a scan runs', () => {
  it('show as they come, and the list is not fetched anew at every look', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const running = [{ ...library, state: 'running', progress: { done: 2, total: 9 } }]
    server.answers = {
      'GET /api/v1/libraries': running,
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 1, editions: 1, works_with_audio: 0 },
    }
    server.install()
    await page.show(<LibraryPage />)
    const { idle } = await import('../test/wait')
    await idle()
    expect(document.querySelectorAll('[data-testid="book"]').length).toBe(1)
    const lists = () => server.calls.filter((call) => call.url.startsWith('/api/v1/works?page=')).length
    const first = lists()
    // A second book comes in at the next look: the list waits for its turn, the numbers do not.
    server.answers['GET /api/v1/works?page=1&per_page=200'] = { items: [work(1), work(2)], total: 2, page: 1, per_page: 200 }
    server.answers['GET /api/v1/works/stats'] = { ...stats, works: 2, editions: 2, works_with_audio: 0 }
    await vi.advanceTimersByTimeAsync(3100)
    await idle()
    expect(lists()).toBe(first)
    await vi.advanceTimersByTimeAsync(6000)
    await idle()
    expect(lists()).toBeGreaterThan(first)
    expect(document.querySelectorAll('[data-testid="book"]').length).toBe(2)
  })
})

describe('the series under a title', () => {
  it('shows the series and the volume, when they are known, between the title and the author', async () => {
    serve([work(1, { title: 'Salzhafen', series: { id: 3, name: 'Hafen-Chronik', pos: 2 } }), work(2, { title: 'Ohne Reihe' }), work(3, { title: 'Nur Reihe', series: { id: 4, name: 'Lose Reihe', pos: null } })])
    await page.show(<LibraryPage />)
    const books = [...document.querySelectorAll('[data-testid="book"]')]
    expect(books.map((book) => plain(book.querySelector('[data-testid="book-series"]')?.textContent ?? ''))).toEqual(['Hafen-Chronik · 2', '', 'Lose Reihe'])
    expect([...books[0].children].map((child) => child.tagName.toLowerCase()).slice(1, 4)).toEqual(['b', 'i', 'small'])
  })
})

describe('the hint to the review list', () => {
  const managed = { ...library, my_right: 'manage', counts: { ...library.counts, review: 3 } }

  it('is for a person who looks after a library with books to look at, and leads to the list of all of them', async () => {
    server.answers = {
      'GET /api/v1/libraries': [managed],
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': stats,
    }
    server.install()
    await page.show(<LibraryPage />)
    const hint = document.querySelector('[data-testid="review-hint"]') as HTMLAnchorElement
    expect(plain(hint.textContent)).toBe('3 Bücher brauchen deinen BlickPrüfen')
    // The note counts every library, so it leads to the list of all of them.
    expect(hint.getAttribute('href')).toBe('/review')
  })

  it('counts every library the person looks after and says book for one', async () => {
    server.answers = {
      'GET /api/v1/libraries': [{ ...managed, counts: { ...managed.counts, review: 1 } }, { ...managed, id: 2, counts: { ...managed.counts, review: 0 } }, { ...library, id: 3, counts: { ...library.counts, review: 5 } }],
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': stats,
    }
    server.install()
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="review-hint"]')!.textContent)).toBe('1 Buch braucht deinen BlickPrüfen')
  })

  it('is not there when nothing waits or the person only reads', async () => {
    serve([work(1)])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="review-hint"]')).toBeNull()
    page.close()
    server.answers['GET /api/v1/libraries'] = [{ ...library, counts: { ...library.counts, review: 4 } }]
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="review-hint"]')).toBeNull()
  })

  it('says it in English too', async () => {
    server.answers = {
      'GET /api/v1/libraries': [managed],
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': stats,
    }
    server.install()
    await changeLanguage('en', false)
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="review-hint"]')!.textContent)).toBe('3 books need your lookReview')
  })
})

describe('the libraries being read now', () => {
  const running = (name: string, done: number, total: number, id = 1) => ({ ...library, id, name, state: 'running', progress: { done, total } })

  function serveRunning(libraries: unknown[]) {
    server.answers = {
      'GET /api/v1/libraries': libraries,
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 1 },
    }
    server.install()
  }

  it('say so in a line with a thin bar under the numbers, while books are there already', async () => {
    serveRunning([running('Hörbücher', 3353, 20592)])
    await page.show(<LibraryPage />)
    const row = document.querySelector('[data-testid="scan-row"]')!
    expect(plain(row.querySelector('.scan-text')!.textContent)).toBe('Bibliothek „Hörbücher“ wird eingelesen · 3.353 von 20.592 Dateien')
    const bar = row.querySelector('[role="progressbar"]')!
    expect(bar.getAttribute('aria-valuenow')).toBe('3353')
    expect(bar.getAttribute('aria-valuemax')).toBe('20592')
    // A reader of the screen hears what the bar is and how far it is.
    expect(plain(bar.getAttribute('aria-label'))).toBe('Einlesen von „Hörbücher“')
    expect(bar.getAttribute('aria-valuetext')).toBe('3.353 von 20.592')
    expect((bar.querySelector('i') as HTMLElement).style.width).toBe('16%')
    expect(document.querySelector('.scanning')!.previousElementSibling!.classList.contains('bar')).toBe(true)
  })

  it('give each library being read its own line, and none to one that is not', async () => {
    serveRunning([running('Hörbücher', 10, 20), running('E-Books', 5, 50, 2), { ...library, id: 3, name: 'Ruhig' }])
    await page.show(<LibraryPage />)
    expect([...document.querySelectorAll('.scan-text')].map((e) => plain(e.textContent))).toEqual(['Bibliothek „Hörbücher“ wird eingelesen · 10 von 20 Dateien', 'Bibliothek „E-Books“ wird eingelesen · 5 von 50 Dateien'])
  })

  it('show the way to the settings to the operator and not to a member', async () => {
    serveRunning([running('Hörbücher', 10, 20)])
    await page.show(<LibraryPage />)
    expect(document.querySelector('.scan-link')!.getAttribute('href')).toBe('/settings?tab=libraries')
    expect(document.querySelector('.scan-link')!.textContent).toBe('Einstellungen')
    page.close()
    auth.me = { ...me, role: 'member' } as Me
    serveRunning([running('Hörbücher', 10, 20)])
    await page.show(<LibraryPage />)
    expect(document.querySelector('.scan-link')).toBeNull()
  })

  it('speak English with the separator of that language, and write nothing when no library is being read', async () => {
    await changeLanguage('en', false)
    serveRunning([running('Audiobooks', 3353, 20592)])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.scan-text')!.textContent)).toBe('Reading library “Audiobooks” · 3,353 of 20,592 files')
    page.close()
    serveRunning([{ ...library }])
    await page.show(<LibraryPage />)
    expect(document.querySelector('.scanning')).toBeNull()
  })

  it('say that a library is being read when the number of files is not known yet', async () => {
    serveRunning([running('Hörbücher', 0, 0)])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.scan-text')!.textContent)).toBe('Bibliothek „Hörbücher“ wird eingelesen')
    expect(document.querySelector('[role="progressbar"]')).toBeNull()
  })
})

describe('the matching with the sources', () => {
  const managing = (counts: Record<string, number>, extra: Record<string, unknown> = {}) => ({
    ...library,
    my_right: 'manage',
    counts: { ...library.counts, entries: 202, pending: 62, ...counts },
    matching: 'running',
    resting: [],
    ...extra,
  })

  function serveMatching(libraries: unknown[]) {
    server.answers = {
      'GET /api/v1/libraries': libraries,
      'GET /api/v1/works?page=1&per_page=200': { items: [work(1)], total: 1, page: 1, per_page: 200 },
      'GET /api/v1/works/stats': { ...stats, works: 1 },
    }
    server.install()
  }

  it('says how many books are looked at of all that have a place in it, with a bar, under the line of the reading', async () => {
    serveMatching([managing({})])
    await page.show(<LibraryPage />)
    const row = document.querySelector('[data-testid="matching-row"]')!
    expect(plain(row.querySelector('.scan-text')!.textContent)).toBe('Abgleich mit den Quellen · 140 von 202 Büchern')
    const bar = row.querySelector('[role="progressbar"]')!
    expect([bar.getAttribute('aria-valuenow'), bar.getAttribute('aria-valuemax')]).toEqual(['140', '202'])
    expect([bar.getAttribute('aria-label'), bar.getAttribute('aria-valuetext')]).toEqual(['Abgleich mit den Quellen', '140 von 202'])
    expect((bar.querySelector('i') as HTMLElement).style.width).toBe('69%')
  })

  it('sits below the reading when both go on, in the same style', async () => {
    serveMatching([managing({}, { state: 'running', progress: { done: 10, total: 20 } })])
    await page.show(<LibraryPage />)
    const lines = [...document.querySelectorAll('.scanning')]
    expect(lines.map((line) => line.getAttribute('data-testid'))).toEqual(['scanning', 'matching'])
    expect(lines[1]!.previousElementSibling).toBe(lines[0])
  })

  it('names a source that rests and the time it rests until, in the time zone of the person', async () => {
    auth.me = { ...me, profile: { view: 'grid', timezone: 'Europe/Berlin' } } as unknown as Me
    serveMatching([managing({}, { resting: [{ source: 'dnb', until: '2026-10-14T22:00:00Z' }] })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="matching-rest"]')!.textContent).trim()).toBe('· DNB pausiert bis 00:00')
    page.close()
    await changeLanguage('en', false)
    serveMatching([managing({}, { resting: [{ source: 'audible', until: '2026-10-14T13:35:00Z' }] })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.scan-text')!.textContent)).toBe('Matching with the sources · 140 of 202 books · Audible paused until 03:35 PM')
  })

  it('is not there for a person who only reads, when nothing waits, or when the matching is done', async () => {
    serveMatching([{ ...managing({}), my_right: 'read', counts: { ...library.counts, entries: 0, pending: 0 }, matching: 'idle' }])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="matching"]')).toBeNull()
    page.close()
    serveMatching([managing({ pending: 0 }, { matching: 'idle' })])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="matching"]')).toBeNull()
  })

  it('sums the libraries the person looks after, and no other', async () => {
    serveMatching([managing({ entries: 100, pending: 40 }), managing({ entries: 50, pending: 10 }, { id: 2, name: 'Zweite' }), { ...library, id: 3, counts: { ...library.counts, entries: 999, pending: 500 } }])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('.scan-text')!.textContent)).toBe('Abgleich mit den Quellen · 100 von 150 Büchern')
  })

  it('keeps looking while books wait, and stops when the matching is done', async () => {
    const spy = vi.spyOn(window, 'setInterval')
    try {
      serveMatching([managing({})])
      await page.show(<LibraryPage />)
      expect(spy.mock.calls.some(([, delay]) => delay === 3000)).toBe(true)
      page.close()
      spy.mockClear()
      serveMatching([managing({ pending: 0 }, { matching: 'idle' })])
      await page.show(<LibraryPage />)
      expect(spy.mock.calls.some(([, delay]) => delay === 3000)).toBe(false)
    } finally {
      spy.mockRestore()
    }
  })

  it('adds, quietly, how many are still being matched to the hint for the books that need a look', async () => {
    serveMatching([managing({ review: 3 })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="review-hint"]')!.textContent)).toBe('3 Bücher brauchen deinen Blick · 62 werden noch abgeglichenPrüfen')
    expect(document.querySelector('[data-testid="review-pending"]')!.classList.contains('review-quiet')).toBe(true)
    page.close()
    serveMatching([managing({ review: 3, pending: 1 })])
    await page.show(<LibraryPage />)
    expect(plain(document.querySelector('[data-testid="review-hint"]')!.textContent)).toBe('3 Bücher brauchen deinen Blick · 1 wird noch abgeglichenPrüfen')
    page.close()
    serveMatching([managing({ review: 3, pending: 0 }, { matching: 'idle' })])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="review-pending"]')).toBeNull()
  })

  it('says nothing of books that wait while no source is on: they are not being matched', async () => {
    serveMatching([managing({ review: 3 }, { matching: 'off' })])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="matching"]')).toBeNull()
    expect(plain(document.querySelector('[data-testid="review-hint"]')!.textContent)).toBe('3 Bücher brauchen deinen BlickPrüfen')
    expect(document.querySelector('[data-testid="review-pending"]')).toBeNull()
  })

  it('shows no hint for books that only wait', async () => {
    serveMatching([managing({ review: 0 })])
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="review-hint"]')).toBeNull()
  })
})
