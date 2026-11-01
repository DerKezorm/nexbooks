/**
 * Looking into a book from the review list: the first pages of an e-book in the reader's frame, a short listen into a recording,
 * and what the file says about itself beside each proposal. None of it may change anything on the server: no place, no
 * bookmark, no decision. The frame is a real iframe here that loads nothing; what it would say, the test says.
 */
import { act } from 'react'

import type { CompareRow, FileFacts, Library, Proposal, ReviewItem } from '../api/client'
import { changeLanguage } from '../i18n'
import type { FromFrame } from '../lib/readFrame'
import { ebook, edition, recording, work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { eventually, idle } from '../test/wait'
import { ReviewPage } from './ReviewPage'

const server = new Server()
const page = new Page()

function proposal(over: Partial<Proposal> = {}): Proposal {
  return {
    id: 'openlibrary:OL1W:', source: 'openlibrary', title: 'Der stille Obstgarten', subtitle: '', authors: ['Mara Lindqvist'], narrators: [], year: 2021,
    languages: ['de'], series: '', series_pos: null, runtime_min: null, variant: '', market: '', score: 68,
    why: [{ code: 'title_exact', text: 'x' }], missing: [], against: [], detail: {}, lacks: [], lacks_join: 'or', cap: '', fatal: false, found: false, cover_url: null, ...over,
  }
}
function item(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: 7, edition_id: 70, work_id: 700, library_id: 1, state: 'maybe', reason: 'author_weak', reason_text: 'x', kind: 'ebook', title: 'Der stille Obstgarten',
    subtitle: '', authors: ['Mara Lindqvist'], language: 'de', year: null, series: '', series_pos: null, runtime_min: null, trash_days: 30, file: 'Lindqvist/Obstgarten.epub', cover_url: null, gap: null,
    proposals: [proposal()], ...over,
  }
}
const library: Library = { id: 1, name: 'Familie', kind: 'both', my_right: 'manage', state: 'idle', counts: { ebooks: 3, audiobooks: 0, files: 3, bytes: 1000, problems: 0, review: 1, entries: 0, pending: 0 }, progress: { done: 0, total: 0 }, matching: 'idle', resting: [] }

const row = (field: CompareRow['field'], file: string, source: string, state: CompareRow['state'], unit: CompareRow['unit'] = ''): CompareRow => ({ field, file, source, state, unit })

function facts(over: Partial<FileFacts> = {}): FileFacts {
  return {
    entry_id: 7, edition_id: 70, kind: 'ebook', folder: 'Lindqvist',
    files: [{ id: 700, name: 'Obstgarten.epub', folder: 'Lindqvist', format: 'epub', size: 1000, part: 1, duration: 0, pages: 210, chapters: 12, protected: false }],
    says: {
      title: 'Der stille Obstgarten', subtitle: '', authors: ['Mara Lindqvist'], narrators: [], series: '', series_pos: null, isbn: '', asin: '', publisher: 'Obstgarten Verlag', year: 2019,
      language: 'de', text_language: 'de', genre: '', subjects: ['Roman'], pages: 210, pages_counted: false, duration: 0, chapters: 12,
    },
    cover_url: null,
    proposals: [{ proposal_id: 'openlibrary:OL1W:', rows: [row('title', 'Der stille Obstgarten', 'Der stille Obstgarten', 'agrees'), row('year', '2019', '2021', 'differs'), row('isbn', '', '9780000000002', 'file_silent'), row('authors', 'Mara Lindqvist', 'Lindqvist', 'similar')] }],
    ...over,
  }
}

function serve(items: ReviewItem[] = [item()], more: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/libraries': [library],
    'GET /api/v1/libraries/1/review?limit=50&offset=0': { total: items.length, items },
    'GET /api/v1/review/7/file': facts(),
    ...more,
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('what the file says', () => {
  it('is read only when it is asked for, and then shows the files, their folder and what is written in them', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    expect(server.called('GET', '/api/v1/review/7/file')).toBeUndefined()
    await page.click(page.button('Was die Datei sagt'))
    expect(server.called('GET', '/api/v1/review/7/file')).toBeTruthy()
    const panel = document.querySelector('[data-testid="file-facts"]') as HTMLElement
    expect(plain(panel.querySelector('[data-testid="file-folder"]')!.textContent)).toBe('Ordner: Lindqvist')
    expect([...panel.querySelectorAll('[data-testid="file-names"] li')].map((li) => plain(li.textContent))).toEqual(['Obstgarten.epub'])
    const text = plain(panel.textContent)
    expect(text).toContain('Obstgarten Verlag')
    expect(text).toContain('2019')
    expect(text).toContain('etwa 210 Seiten')
    // What the file does not say is said so, not left out.
    expect(panel.querySelector('dd.is-silent')).toBeTruthy()
    expect(plain(panel.textContent)).toContain('steht nicht drin')
    expect(server.changes()).toEqual([])
  })

  it('tells what contradicts a proposal from what the file leaves unsaid, beside the proposal', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    expect(document.querySelector('[data-testid="proposal-compare"]')).toBeNull()
    await page.click(page.button('Was die Datei sagt'))
    const compare = document.querySelector('[data-testid="proposal"] [data-testid="proposal-compare"]') as HTMLElement
    const groups = Object.fromEntries([...compare.querySelectorAll('.compare-group')].map((group) => [group.getAttribute('data-state'), plain(group.textContent)]))
    expect(Object.keys(groups)).toEqual(['differs', 'similar', 'agrees', 'file_silent'])
    expect(groups.differs).toContain('Die Datei widerspricht')
    expect(groups.differs).toContain('Jahr')
    expect(groups.differs).toContain('Datei: 2019 · Quelle: 2021')
    expect(groups.similar).toContain('Autor')
    expect(groups.agrees).toContain('Titel')
    expect(groups.file_silent).toContain('Die Datei sagt dazu nichts')
    expect(groups.file_silent).toContain('ISBN')
    // A contradiction is not mixed up with silence.
    expect(groups.differs).not.toContain('ISBN')
  })

  it('reads the comparison again when a search added proposals', async () => {
    serve([item()], { 'POST /api/v1/review/7/search': item({ proposals: [proposal(), proposal({ id: 'hardcover:9:', source: 'hardcover', title: 'Another' })] }) })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Was die Datei sagt'))
    expect(server.calls.filter((call) => call.url === '/api/v1/review/7/file').length).toBe(1)
    await page.click(page.button('Anderes suchen'))
    await page.click(page.button('Suchen'))
    await eventually(() => expect(server.calls.filter((call) => call.url === '/api/v1/review/7/file').length).toBe(2))
  })

  it('says it when the file cannot be read, and changes nothing', async () => {
    serve()
    server.fails = { 'GET /api/v1/review/7/file': { status: 404, code: 'not_found' } }
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Was die Datei sagt'))
    expect(document.querySelector('[data-testid="file-facts"]')).toBeNull()
    expect(document.querySelector('[role="alert"]')).toBeTruthy()
    expect(server.changes()).toEqual([])
  })

  it('shows the picture inside the file from nexbooks only', async () => {
    serve([item()], { 'GET /api/v1/review/7/file': facts({ cover_url: '/api/v1/review/7/file-cover?v=abc' }) })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Was die Datei sagt'))
    const src = document.querySelector('[data-testid="file-cover"] img')!.getAttribute('src')!
    expect(src.startsWith('/api/v1/')).toBe(true)
  })

  it('counts the pages of a PDF as counted and tells a recording by its running time and narrators', async () => {
    const audio = facts({
      kind: 'audio',
      files: [{ id: 800, name: '01.mp3', folder: 'A', format: 'mp3', size: 10, part: 1, duration: 600, pages: 0, chapters: 0, protected: false }],
      says: { ...facts().says, narrators: ['Ben Hollister'], duration: 3600, pages: 0, chapters: 3 },
      proposals: [{ proposal_id: 'openlibrary:OL1W:', rows: [row('runtime', '60', '600', 'differs', 'minutes')] }],
    })
    serve([item({ kind: 'audio' })], { 'GET /api/v1/review/7/file': audio })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Was die Datei sagt'))
    const text = plain(document.querySelector('[data-testid="file-facts"]')!.textContent)
    expect(text).toContain('Ben Hollister')
    expect(text).toContain('1:00:00 · 60 Min.')
    expect(plain(document.querySelector('[data-testid="proposal-compare"]')!.textContent)).toContain('Datei: 60 Min. · Quelle: 600 Min.')
  })
})

describe('a look into the book', () => {
  const read = () => ({
    'GET /api/v1/editions/70': { work: work(700, { title: 'Der stille Obstgarten', editions: [ebook(70)] }), edition: edition(ebook(70, { formats: ['epub'] }), { files: [{ id: 700, format: 'epub', path: 'Obstgarten.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }] }) },
    'GET /api/v1/files/700/content': {},
  })
  const frame = () => document.querySelector('[data-testid="peek-frame"]') as HTMLIFrameElement | null
  async function say(message: FromFrame) {
    const source = frame()!.contentWindow!
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: message, source }))
      await Promise.resolve()
    })
  }

  it('opens the first pages of an e-book in the frame of the reader and saves nothing, however far it is read', async () => {
    serve([item()], read())
    await page.show(<ReviewPage />, '/review?library=1')
    expect(page.button('Reinhören')).toBeUndefined()
    await page.click(page.button('Reinlesen'))
    await eventually(() => expect(frame()).toBeTruthy())
    expect(frame()!.getAttribute('src')).toBe('/readview.html')
    expect(frame()!.getAttribute('sandbox')).toBe('allow-same-origin allow-scripts')
    expect(server.calls.some((call) => call.url === '/api/v1/files/700/content')).toBe(true)
    await say({ type: 'opened', title: 'Der stille Obstgarten', toc: [], marks: [], fixed: false, rtl: false, pages: 0 })
    await say({ type: 'relocate', reason: 'page', locator: 'epubcfi(/6/8)', fraction: 0.4, tocId: 0, unit: 'chapter', chapter: 2, chapterCount: 5, chapterTitle: 'Zwei' })
    expect(plain(document.querySelector('[data-testid="peek-where"]')!.textContent)).toBe('40 Prozent')
    expect(plain(page.dialog!.textContent)).toContain('Dein Lesestand ändert sich dadurch nicht')
    // No place, no bookmark, no mark that the book was opened: nothing but reading went to the server.
    expect(server.changes()).toEqual([])
  })

  it('says that part of the book is missing from the file, as the reader does', async () => {
    serve([item()], read())
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinlesen'))
    await eventually(() => expect(frame()).toBeTruthy())
    expect(document.querySelector('[data-testid="peek-missing"]')).toBeNull()
    await say({ type: 'notice', kind: 'missing' })
    expect(plain(document.querySelector('[data-testid="peek-missing"]')!.textContent)).toContain('fehlt in der Datei')
  })

  it('says plainly when there is no file the reader can show', async () => {
    serve([item()], {
      'GET /api/v1/editions/70': { work: work(700, { editions: [ebook(70)] }), edition: edition(ebook(70, { formats: ['epub'] }), { files: [{ id: 700, format: 'epub', path: 'a.epub', size: 10, part: 1, duration: 0, sha256: 'a', protected: true }] }) },
    })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinlesen'))
    expect(document.querySelector('[data-testid="peek-none"]')).toBeTruthy()
    expect(frame()).toBeNull()
    expect(plain(page.dialog!.textContent)).toContain('kopiergeschützt')
  })

  it('offers the formats an edition has and opens the one chosen', async () => {
    serve([item()], {
      'GET /api/v1/editions/70': { work: work(700, { editions: [ebook(70)] }), edition: edition(ebook(70, { formats: ['epub', 'pdf'] }), { files: [
        { id: 700, format: 'epub', path: 'a.epub', size: 10, part: 1, duration: 0, sha256: 'a', protected: false },
        { id: 701, format: 'pdf', path: 'a.pdf', size: 10, part: 1, duration: 0, sha256: 'b', protected: false },
      ] }) },
      'GET /api/v1/files/701/content': {},
      'GET /api/v1/files/700/content': {},
    })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinlesen'))
    await eventually(() => expect(frame()).toBeTruthy())
    await page.click(page.button('PDF', page.dialog!))
    await eventually(() => expect(frame()!.getAttribute('src')).toBe('/pdfview.html'))
    expect(server.changes()).toEqual([])
  })

  it('offers no format the reader cannot show: not a protected file and not a format it does not know', async () => {
    serve([item()], {
      'GET /api/v1/editions/70': { work: work(700, { editions: [ebook(70)] }), edition: edition(ebook(70, { formats: ['epub', 'pdf', 'azw3'] }), { files: [
        { id: 700, format: 'epub', path: 'a.epub', size: 10, part: 1, duration: 0, sha256: 'a', protected: false },
        { id: 701, format: 'pdf', path: 'a.pdf', size: 10, part: 1, duration: 0, sha256: 'b', protected: false },
        { id: 702, format: 'azw3', path: 'a.azw3', size: 10, part: 1, duration: 0, sha256: 'c', protected: true },
        { id: 703, format: 'docx', path: 'a.docx', size: 10, part: 1, duration: 0, sha256: 'd', protected: false },
      ] }) },
      'GET /api/v1/files/700/content': {},
    })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinlesen'))
    await eventually(() => expect(frame()).toBeTruthy())
    const group = page.dialog!.querySelector('[role="group"]') as HTMLElement
    expect([...group.querySelectorAll('button')].map((item) => item.textContent)).toEqual(['EPUB', 'PDF'])
  })
})

describe('a listen into the recording', () => {
  beforeEach(() => {
    vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) => (type.startsWith('audio/mpeg') ? 'probably' : ''))
  })
  afterEach(() => vi.restoreAllMocks())
  const chapters = [0, 1, 2, 3].map((n) => ({ number: n + 1, title: `Kapitel ${n + 1}`, start: n * 900, target: '', depth: 0 }))
  const heard = () => ({
    'GET /api/v1/editions/70': {
      work: work(700, { title: 'Der stille Obstgarten', editions: [recording(70)] }),
      edition: edition(recording(70, { duration: 3600, formats: ['mp3'] }), { files: [
        { id: 800, format: 'mp3', path: 'A/01.mp3', size: 1000, part: 1, duration: 1800, sha256: 'a', protected: false },
        { id: 801, format: 'mp3', path: 'A/02.mp3', size: 1000, part: 2, duration: 1800, sha256: 'b', protected: false },
      ], chapters }),
    },
  })

  it('has the small player in the card, jumps to the start, a third and the middle, and lists the chapters', async () => {
    serve([item({ kind: 'audio' })], heard())
    await page.show(<ReviewPage />, '/review?library=1')
    expect(page.button('Reinlesen')).toBeUndefined()
    await page.click(page.button('Reinhören'))
    await eventually(() => expect(plain(document.querySelector('[data-testid="peek-time"]')!.textContent)).toBe('0:00 / 1:00:00'))
    const jump = async (key: string) => page.click(document.querySelector(`[data-jump="${key}"]`))
    await jump('third')
    await eventually(() => expect(plain(document.querySelector('[data-testid="peek-time"]')!.textContent)).toBe('20:00 / 1:00:00'))
    await jump('middle')
    await eventually(() => expect(plain(document.querySelector('[data-testid="peek-time"]')!.textContent)).toBe('30:00 / 1:00:00'))
    await jump('start')
    await eventually(() => expect(plain(document.querySelector('[data-testid="peek-time"]')!.textContent)).toBe('0:00 / 1:00:00'))
    await page.click(document.querySelector('[data-testid="peek-chapters-toggle"]'))
    const list = [...document.querySelectorAll('[data-testid="peek-chapters"] li')].map((li) => plain(li.textContent))
    expect(list).toEqual(['Kapitel 10:00', 'Kapitel 215:00', 'Kapitel 330:00', 'Kapitel 445:00'])
    await page.click(document.querySelectorAll('[data-testid="peek-chapters"] button')[2])
    await eventually(() => expect(plain(document.querySelector('[data-testid="peek-time"]')!.textContent)).toBe('30:00 / 1:00:00'))
    expect(plain(document.querySelector('[data-testid="peek-listen"]')!.textContent)).toContain('Dein Hörstand ändert sich dadurch nicht')
    // Listening saves no place and no bookmark.
    expect(server.changes()).toEqual([])
  })

  it('says so, and offers no play button, when this browser plays none of the parts', async () => {
    serve([item({ kind: 'audio' })], heard())
    vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation(() => '')
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinhören'))
    await idle()
    expect(plain(document.querySelector('[data-testid="peek-listen-failed"]')!.textContent)).toContain('kann die Aufnahme nicht abspielen')
    expect(document.querySelector('[data-testid="peek-play"]')).toBeNull()
  })

  it('stays quiet and says so when the recording has no file', async () => {
    serve([item({ kind: 'audio' })], { 'GET /api/v1/editions/70': { work: work(700, { editions: [recording(70)] }), edition: edition(recording(70), { files: [] }) } })
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Reinhören'))
    await idle()
    expect(document.querySelector('[data-testid="peek-listen-failed"]')).toBeTruthy()
  })
})
