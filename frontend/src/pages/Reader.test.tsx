/**
 * The reader's page without a browser (jsdom): what it picks to open, what it says when there is nothing to open, that the
 * place is saved only after the person has moved, that another device's place is asked about before anything is saved, that
 * a bookmark is deleted only after the question, and that a change of the look reaches the account and the frame. The frame
 * itself is a real iframe here that loads nothing; the messages it would send are sent by the test, as from its window.
 * (The real frames, the real turning of pages and the hostile book are in `e2e/reader.spec.ts`.)
 */
import { act } from 'react'
import { Route, Routes } from 'react-router-dom'

import type { BookmarkOut, EditionOut, Me, ProgressOut } from '../api/client'
import { changeLanguage } from '../i18n'
import type { FromFrame } from '../lib/readFrame'
import { ebook, edition, recording, work } from '../test/books'
import { Page, Server } from '../test/harness'
import { idle } from '../test/wait'
import { ReaderPage } from './ReaderPage'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'member', language: 'de', profile: { theme: 'light', view: 'shelf', reader_size: 100, reader_theme: 'light' } } as unknown as Me
const auth = { me, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function edi(over: Partial<EditionOut> = {}): EditionOut {
  return edition(ebook(5, { formats: ['epub'] }), over)
}

const place = (over: Partial<ProgressOut> = {}): ProgressOut => ({
  edition_id: 5, work_id: 1, locator: 'epubcfi(/6/20)', position: null, fraction: 0.6, unit: 'chapter', chapter: 16, chapter_count: 38, chapter_title: 'Sechzehn',
  device_key: 'other-device', device: 'Pixel 8', moved_at: '2026-10-13T11:00:00Z', received_at: '2026-10-13T11:00:01Z', age: 60, ...over,
})

const mark = (id: number, fraction: number, title: string): BookmarkOut => ({ id, edition_id: 5, locator: `epubcfi(/6/${id})`, position: null, fraction, chapter_title: title, note: '', created_at: '2026-10-12T10:00:00Z' })

function serve(options: { edition?: EditionOut; progress?: ProgressOut | null; bookmarks?: BookmarkOut[] } = {}) {
  const e = options.edition ?? edi()
  server.answers = {
    'GET /api/v1/editions/5': { work: work(1, { title: 'Das Buch', editions: [{ ...ebook(5) }] }), edition: e },
    'GET /api/v1/editions/5/progress': options.progress ?? null,
    'GET /api/v1/editions/5/bookmarks': options.bookmarks ?? [],
    'GET /api/v1/files/50/content': {},
    'GET /api/v1/files/51/content': {},
    'PUT /api/v1/editions/5/progress': { applied: true, progress: place({ device_key: 'me' }) },
    'PUT /api/v1/me/preferences': { ...me.profile, reader_size: 110 },
  }
  server.fails = {}
  server.install()
}

async function show(at = '/read/5') {
  await page.show(
    <Routes>
      <Route path="/read/:editionId" element={<ReaderPage />} />
      <Route path="*" element={<p>elsewhere</p>} />
    </Routes>,
    at,
  )
  // The page asks for the edition, the place and the bookmarks, then fetches the file.
  await act(async () => await new Promise((resolve) => setTimeout(resolve, 30)))
  await idle()
}

const frame = () => document.querySelector('[data-testid="reader-frame"]') as HTMLIFrameElement | null

/** The frame says something, as from its own window. */
async function say(message: FromFrame) {
  const source = frame()!.contentWindow!
  await act(async () => {
    window.dispatchEvent(new MessageEvent('message', { data: message, source }))
    await Promise.resolve()
  })
}

const opened: FromFrame = { type: 'opened', title: 'Das Buch', toc: [{ id: 0, label: 'Eins', depth: 0 }, { id: 1, label: 'Zwei', depth: 0 }], marks: [0.5], fixed: false, rtl: false, pages: 0 }
const relocate = (over: Partial<Extract<FromFrame, { type: 'relocate' }>> = {}): FromFrame => ({
  type: 'relocate', reason: 'navigation', locator: 'epubcfi(/6/4)', fraction: 0.1, tocId: 0, unit: 'chapter', chapter: 1, chapterCount: 2, chapterTitle: 'Eins', ...over,
})

const saves = () => server.calls.filter((call) => call.method === 'PUT' && call.url === '/api/v1/editions/5/progress')

let kept: Map<string, string>

beforeEach(async () => {
  await changeLanguage('de', false)
  kept = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
  })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
  vi.stubGlobal('scrollTo', () => undefined)
  window.HTMLElement.prototype.scrollIntoView = () => undefined
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('what the reader opens', () => {
  it('opens an EPUB in the frame of books, with the origin of the page, and fetches its bytes with the session', async () => {
    serve({ edition: edi({ files: [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }] }) })
    await show()
    expect(frame()!.getAttribute('src')).toBe('/readview.html')
    expect(frame()!.getAttribute('sandbox')).toBe('allow-same-origin allow-scripts')
    expect(server.calls.some((call) => call.url === '/api/v1/files/50/content')).toBe(true)
    expect(document.querySelector('.rd-title b')!.textContent).toBe('Das Buch')
  })

  it('opens a PDF in the frame of PDFs, which has no origin at all', async () => {
    serve({ edition: edi({ formats: ['pdf'], files: [{ id: 51, format: 'pdf', path: 'a.pdf', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }] }) })
    await show()
    expect(frame()!.getAttribute('src')).toBe('/pdfview.html')
    expect(frame()!.getAttribute('sandbox')).toBe('allow-scripts')
  })

  it('takes the EPUB when there are several files, the PDF when the address asks and there is one', async () => {
    const files = [
      { id: 51, format: 'pdf', path: 'a.pdf', size: 1000, part: 2, duration: 0, sha256: 'b', protected: false },
      { id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false },
    ]
    serve({ edition: edi({ formats: ['epub', 'pdf'], files }) })
    await show()
    expect(frame()!.getAttribute('src')).toBe('/readview.html')
    page.close()
    serve({ edition: edi({ formats: ['epub', 'pdf'], files }) })
    await show('/read/5?format=pdf')
    expect(frame()!.getAttribute('src')).toBe('/pdfview.html')
  })

  it('does not open a copy-protected book, says so, never fetches it, and offers the file', async () => {
    serve({ edition: edi({ files: [{ id: 50, format: 'azw3', path: 'a.azw3', size: 1000, part: 1, duration: 0, sha256: 'a', protected: true }] }) })
    await show()
    expect(frame()).toBeNull()
    const text = document.querySelector('[data-testid="reader-failed"]')!.textContent!
    expect(text).toContain('kopiergeschützt')
    expect(text).toContain('umgeht den Schutz nicht')
    expect((document.querySelector('a.go') as HTMLAnchorElement).getAttribute('href')).toBe('/api/v1/files/50/download')
    expect(server.calls.some((call) => call.url.includes('/content'))).toBe(false)
  })

  it('opens the open file of an edition of which another is protected', async () => {
    serve({
      edition: edi({
        formats: ['epub', 'pdf'],
        files: [
          { id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: true },
          { id: 51, format: 'pdf', path: 'a.pdf', size: 1000, part: 2, duration: 0, sha256: 'b', protected: false },
        ],
      }),
    })
    await show()
    expect(frame()!.getAttribute('src')).toBe('/pdfview.html')
    expect(server.calls.some((call) => call.url === '/api/v1/files/50/content')).toBe(false)
  })

  it('says there is nothing to read for a recording, and a missing edition says what any missing thing says', async () => {
    serve({ edition: edition(recording(5), {}) })
    await show()
    expect(document.querySelector('[data-testid="reader-failed"]')!.textContent).toContain('keine Datei')
    page.close()
    serve()
    server.fails['GET /api/v1/editions/5'] = { status: 404, code: 'not_found' }
    await show()
    expect(document.querySelector('[data-testid="reader-failed"]')!.textContent).toContain('gibt es nicht')
    expect(frame()).toBeNull()
  })

  it('opens a number that is none as an edition that does not exist', async () => {
    serve()
    await show('/read/abc')
    expect(document.querySelector('[data-testid="reader-failed"]')!.textContent).toContain('gibt es nicht')
  })
})

describe('the place', () => {
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]

  it('saves nothing for a book that is only opened', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say(relocate())
    page.close()
    expect(saves()).toEqual([])
    // Not even a copy of the place is kept on this device.
    expect(kept.has('nexbooks.read.1.5')).toBe(false)
  })

  it('saves the place after the person turned a page, when the page is left, and keeps a copy on this device', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say(relocate({ reason: 'page', fraction: 0.25, chapter: 2, chapterTitle: 'Zwei', locator: 'epubcfi(/6/8)' }))
    expect(JSON.parse(kept.get('nexbooks.read.1.5')!)).toMatchObject({ locator: 'epubcfi(/6/8)', fraction: 0.25 })
    page.close()
    const sent = saves()
    expect(sent).toHaveLength(1)
    expect(sent[0].body).toMatchObject({ locator: 'epubcfi(/6/8)', fraction: 0.25, unit: 'chapter', chapter: 2, chapter_count: 2, chapter_title: 'Zwei' })
    expect(String(sent[0].body!.device_key)).toMatch(/^[A-Za-z0-9_-]{4,40}$/)
    expect(typeof sent[0].body!.device).toBe('string')
    expect(Number(sent[0].body!.age)).toBeGreaterThanOrEqual(0)
  })

  it('counts a jump the person asked for (a chapter in the contents) as a move of theirs', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say(relocate())
    await page.click(document.querySelector<HTMLElement>('[data-testid="open-contents"]'))
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await page.click([...document.querySelectorAll<HTMLElement>('.rd-toc')].find((line) => line.textContent === 'Zwei'))
    expect(post.mock.calls[0][0]).toEqual({ type: 'goto-toc', id: 1 })
    await say(relocate({ reason: 'navigation', chapter: 2, fraction: 0.5, tocId: 1 }))
    page.close()
    expect(saves()).toHaveLength(1)
  })

  it('opens at the place of the server on a device that has none, without a question', async () => {
    serve({ edition: edi({ files: file }), progress: place() })
    await show()
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await say({ type: 'ready' })
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
    const open = post.mock.calls.find((call) => (call[0] as { type: string }).type === 'open')!
    expect(open[0]).toMatchObject({ type: 'open', format: 'epub', locator: 'epubcfi(/6/20)', fraction: 0.6 })
    expect((open[0] as { data: ArrayBuffer }).data).toBeInstanceOf(ArrayBuffer)
  })

  it('opens by the share of the book where the place of the server has no CFI (a place KOReader reported)', async () => {
    serve({ edition: edi({ files: file }), progress: place({ locator: '', fraction: 0.42, chapter: 0, chapter_count: 0, chapter_title: '', device: 'Kobo Libra 2' }) })
    await show()
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await say({ type: 'ready' })
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
    const open = post.mock.calls.find((call) => (call[0] as { type: string }).type === 'open')!
    expect(open[0]).toMatchObject({ type: 'open', locator: '', fraction: 0.42 })
  })

  it('jumps by the share of the book when the other place has no CFI, and says it in percent', async () => {
    kept.set('nexbooks.read.1.5', JSON.stringify({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: Date.parse('2026-10-13T08:00:00Z') }))
    serve({ edition: edi({ files: file }), progress: place({ locator: '', fraction: 0.42, chapter: 0, chapter_count: 0, chapter_title: '', device: 'Kobo Libra 2' }) })
    await show()
    await say(opened)
    expect(document.querySelector('[data-testid="reader-hint"]')!.textContent).toContain('Auf Kobo Libra 2 bist du weiter: 42 %. Dorthin springen?')
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await page.click(document.querySelector<HTMLElement>('[data-testid="hint-jump"]'))
    expect(post.mock.calls[0][0]).toEqual({ type: 'goto-fraction', fraction: 0.42 })
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
  })

  it('does not open at the place another person of this browser left in the book', async () => {
    // The family tablet: somebody reads to 71 % and signs out; the next person who opens the book finds nothing of it.
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say(relocate({ reason: 'page', fraction: 0.71, chapter: 2, locator: 'epubcfi(/6/40)' }))
    expect(JSON.parse(kept.get('nexbooks.read.1.5')!)).toMatchObject({ fraction: 0.71 })
    page.close()
    auth.me = { ...me, id: 2, name: 'tom' } as Me
    try {
      serve({ edition: edi({ files: file }), progress: null })
      await show()
      expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
      const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
      await say({ type: 'ready' })
      await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
      const open = post.mock.calls.find((call) => (call[0] as { type: string }).type === 'open')!
      expect((open[0] as { locator?: unknown }).locator ?? null).toBeNull()
      expect((open[0] as { fraction?: unknown }).fraction ?? null).toBeNull()
    } finally {
      auth.me = me
    }
  })

  it('asks, and saves nothing, while another device is further along; staying ends the question', async () => {
    kept.set('nexbooks.read.1.5', JSON.stringify({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: Date.parse('2026-10-13T08:00:00Z') }))
    serve({ edition: edi({ files: file }), progress: place() })
    await show()
    const hint = document.querySelector('[data-testid="reader-hint"]')!
    expect(hint.textContent).toContain('Auf Pixel 8 bist du weiter: Kapitel 16. Dorthin springen?')
    // The frame opens at this device's own place.
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await say({ type: 'ready' })
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
    expect(post.mock.calls.find((call) => (call[0] as { type: string }).type === 'open')![0]).toMatchObject({ locator: 'epubcfi(/6/4)' })
    await say(opened)
    await say(relocate({ reason: 'navigation' }))
    page.close()
    expect(saves()).toEqual([])
  })

  it('jumps to the other place when asked to', async () => {
    kept.set('nexbooks.read.1.5', JSON.stringify({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: Date.parse('2026-10-13T08:00:00Z') }))
    serve({ edition: edi({ files: file }), progress: place() })
    await show()
    await say(opened)
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    await page.click(document.querySelector<HTMLElement>('[data-testid="hint-jump"]'))
    expect(post.mock.calls[0][0]).toEqual({ type: 'goto', locator: 'epubcfi(/6/20)' })
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
    await say(relocate({ reason: 'navigation', fraction: 0.6, chapter: 16 }))
    page.close()
    expect(saves()).toHaveLength(1)
    expect(saves()[0].body).toMatchObject({ fraction: 0.6, chapter: 16 })
  })

  it('lets a device that stays save its own place afterwards', async () => {
    kept.set('nexbooks.read.1.5', JSON.stringify({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: Date.parse('2026-10-13T08:00:00Z') }))
    serve({ edition: edi({ files: file }), progress: place() })
    await show()
    await say(opened)
    await page.click(document.querySelector<HTMLElement>('[data-testid="hint-stay"]'))
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
    await say(relocate({ reason: 'page', fraction: 0.12 }))
    page.close()
    expect(saves()).toHaveLength(1)
  })

  it('shows what it says in English, and never pauses with a dash', async () => {
    await changeLanguage('en', false)
    kept.set('nexbooks.read.1.5', JSON.stringify({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: Date.parse('2026-10-13T08:00:00Z') }))
    serve({ edition: edi({ files: file }), progress: place() })
    await show()
    await say(opened)
    expect(document.querySelector('[data-testid="reader-hint"]')!.textContent).toContain('You are further on Pixel 8: chapter 16. Jump there?')
    // A turn of the page by the person answers the question (they stay).
    await say(relocate({ reason: 'navigation', chapter: 1, chapterCount: 2, fraction: 0.5 }))
    expect(document.querySelector('[data-testid="reader-where"]')!.textContent).toBe('Chapter 1 of 2 · 50 %')
    expect(document.body.textContent).not.toMatch(/[–—]/)
    await say(relocate({ reason: 'page', chapter: 1, chapterCount: 2, fraction: 0.52 }))
    expect(document.querySelector('[data-testid="reader-hint"]')).toBeNull()
  })
})

describe('bookmarks', () => {
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]

  it('ask before one is deleted, say it cannot be taken back, and send nothing until the one click', async () => {
    serve({ edition: edi({ files: file }), bookmarks: [mark(7, 0.3, 'Kapitel Sieben')] })
    server.answers['DELETE /api/v1/bookmarks/7'] = 204
    await show()
    await say(opened)
    await say(relocate())
    await page.click(document.querySelector<HTMLElement>('[data-testid="open-contents"]'))
    await page.click(document.querySelector<HTMLElement>('[data-testid="tab-bookmarks"]'))
    expect(document.querySelector('[data-testid="bookmark"]')!.textContent).toContain('Kapitel Sieben')
    await page.click(document.querySelector<HTMLElement>('[data-testid="bookmark-delete"]'))
    const dialog = page.dialog!
    expect(dialog.textContent).toContain('Kapitel Sieben')
    expect(dialog.textContent).toContain('nicht zurückholen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Abbrechen', dialog))
    expect(server.changes()).toEqual([])
    expect(document.querySelector('[data-testid="bookmark"]')).toBeTruthy()
    await page.click(document.querySelector<HTMLElement>('[data-testid="bookmark-delete"]'))
    await page.inDialog('Löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/bookmarks/7'])
    expect(document.querySelector('[data-testid="bookmark"]')).toBeNull()
  })

  it('are set at the place and listed; a second one at the same place is not made', async () => {
    serve({ edition: edi({ files: file }), bookmarks: [] })
    server.answers['POST /api/v1/editions/5/bookmarks'] = mark(8, 0.1, 'Eins')
    await show()
    await say(opened)
    await say(relocate({ fraction: 0.1 }))
    const button = document.querySelector('[data-testid="add-bookmark"]') as HTMLButtonElement
    await page.click(button)
    expect(server.called('POST', '/api/v1/editions/5/bookmarks')!.body).toEqual({ locator: 'epubcfi(/6/4)', fraction: 0.1, chapter_title: 'Eins' })
    expect(document.querySelector('[data-testid="reader-status"]')!.textContent).toBe('Lesezeichen gesetzt.')
    expect(button.getAttribute('aria-pressed')).toBe('true')
    // Again at the same place: no second request, the list opens.
    const before = server.changes().length
    await page.click(button)
    expect(server.changes().length).toBe(before)
    expect(document.querySelector('[data-testid="reader-panel"]')!.getAttribute('data-tab')).toBe('bookmarks')
  })
})

describe('the look', () => {
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]

  it('is changed at once in the frame and kept with the account a moment later', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await page.click(document.querySelector<HTMLElement>('[data-testid="open-look"]'))
    const post = vi.spyOn(frame()!.contentWindow!, 'postMessage')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await act(async () => (page.button('Größer') as HTMLButtonElement).click())
      const sent = post.mock.calls.map((call) => call[0] as { type: string; look?: { size: number } })
      expect(sent.find((message) => message.type === 'look')!.look!.size).toBe(110)
      expect(document.querySelector('[data-testid="look-size"]')!.textContent).toBe('110 %')
      expect(server.changes()).toEqual([])
      await act(async () => void vi.advanceTimersByTime(600))
    } finally {
      vi.useRealTimers()
    }
    await idle()
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ reader_size: 110 })
  })

  it('leaves out what a book with fixed pages ignores', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say({ ...(opened as Extract<FromFrame, { type: 'opened' }>), fixed: true })
    await page.click(document.querySelector<HTMLElement>('[data-testid="open-look"]'))
    expect(document.querySelector('[data-testid="look-panel"]')!.textContent).toContain('feste Seiten')
    expect(document.querySelector('[data-testid="look-font"]')).toBeNull()
    expect(document.querySelector('[data-testid="look-margin"]')).toBeNull()
  })
})

describe('what the frame tells about the book', () => {
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]

  it('says in plain words that no book is opened where the walls around a page do not hold, and offers the file', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say({ type: 'failed', reason: 'unsafe' })
    const failed = document.querySelector('[data-testid="reader-failed"]')!
    expect(failed.getAttribute('data-reason')).toBe('unsafe')
    expect(failed.textContent).toContain('eigenen Code auszuführen')
    expect(failed.querySelector('a[href="/api/v1/files/50/download"]')).not.toBeNull()
  })

  it('tells the person that a page had to be flattened or cut', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say({ type: 'notice', kind: 'flattened' })
    expect(document.querySelector('[data-testid="reader-status"]')!.textContent).toContain('so tief verschachtelt')
    await say({ type: 'notice', kind: 'cut' })
    expect(document.querySelector('[data-testid="reader-status"]')!.textContent).toContain('abgeschnitten')
  })

  it('tells the person that part of the book is missing from the file', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    await say({ type: 'notice', kind: 'missing' })
    expect(document.querySelector('[data-testid="reader-status"]')!.textContent).toContain('fehlt in der Datei')
  })
})

describe('whom the page listens to', () => {
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]

  /** Somebody else says it: another window, not the frame of the reader. */
  async function sayFrom(source: MessageEventSource | null, message: unknown) {
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: message, source }))
      await Promise.resolve()
    })
  }

  it('hears nothing from a window that is not its own frame: a page of the book, another frame, the window itself', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    const stranger = document.createElement('iframe')
    document.body.append(stranger)
    const reader = document.querySelector('[data-testid="reader"]')!
    for (const source of [stranger.contentWindow, window, null]) {
      await sayFrom(source, opened)
      await sayFrom(source, relocate({ reason: 'page', fraction: 0.9, chapter: 2 }))
      await sayFrom(source, { type: 'failed', reason: 'broken' })
      await sayFrom(source, { type: 'link', href: 'https://example.com/' })
      await sayFrom(source, { type: 'notice', kind: 'cut' })
    }
    expect(reader.getAttribute('data-phase')).toBe('loading')
    expect(document.querySelector('[data-testid="reader-failed"]')).toBeNull()
    expect(document.querySelector('[data-testid="reader-status"]')).toBeNull()
    page.close()
    expect(saves()).toEqual([])
    stranger.remove()
    // The frame itself is heard.
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    expect(document.querySelector('[data-testid="reader"]')!.getAttribute('data-phase')).toBe('open')
  })

  it('asks before it opens a link of a book, shows the address, and opens it only for an address of the web from its own frame', async () => {
    serve({ edition: edi({ files: file }) })
    await show()
    await say(opened)
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    try {
      await sayFrom(window, { type: 'link', href: 'https://example.com/a' })
      expect(page.dialog).toBeNull()
      await say({ type: 'link', href: 'javascript:alert(1)' })
      await say({ type: 'link', href: 'data:text/html,x' })
      await say({ type: 'link', href: 'https://' + 'a'.repeat(3000) + '.example.com/' })
      expect(page.dialog).toBeNull()
      expect(open).not.toHaveBeenCalled()
      // The address of the book is shown the way the browser reads it, and nothing is opened by the question alone.
      await say({ type: 'link', href: 'HTTPS://Example.COM/a b' })
      const dialog = page.dialog!
      expect(dialog.querySelector('[data-testid="reader-link-address"]')!.textContent).toBe('https://example.com/a%20b')
      expect(dialog.textContent).toContain('Link im Buch')
      expect(open).not.toHaveBeenCalled()
      await page.click(page.button('Abbrechen', dialog))
      expect(open).not.toHaveBeenCalled()
      expect(page.dialog).toBeNull()
      await say({ type: 'link', href: 'https://example.com/a' })
      await page.click(page.button('Öffnen', page.dialog!))
      expect(open).toHaveBeenCalledTimes(1)
      expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer')
    } finally {
      open.mockRestore()
    }
  })
})

describe('a book too large for the browser', () => {
  it('is named as such, offered for download, and not fetched at all', async () => {
    const big = [{ id: 50, format: 'epub', path: 'a.epub', size: 150 * 1024 * 1024, part: 1, duration: 0, sha256: 'a', protected: false }]
    serve({ edition: edi({ files: big }) })
    await show()
    const failed = document.querySelector('[data-testid="reader-failed"]')!
    expect(failed.getAttribute('data-reason')).toBe('too_large')
    expect(failed.textContent).toContain('über 100 MB')
    expect(failed.querySelector('a[href="/api/v1/files/50/download"]')).not.toBeNull()
    expect(server.calls.filter((call) => call.url === '/api/v1/files/50/content')).toEqual([])
  })

  it('is also stopped when the server says it is larger than the list of files did', async () => {
    const small = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]
    serve({ edition: edi({ files: small }) })
    server.answers['GET /api/v1/files/50/content'] = {}
    const real = globalThis.fetch
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/v1/files/50/content')) return new Response(new Uint8Array(10), { status: 200, headers: { 'content-length': String(150 * 1024 * 1024) } })
      return real(input, init)
    })
    try {
      await show()
      await act(async () => await new Promise((resolve) => setTimeout(resolve, 50)))
      expect(document.querySelector('[data-testid="reader-failed"]')!.getAttribute('data-reason')).toBe('too_large')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('"Finished" by hand', () => {
  const entry = { id: 3, work_id: 1, edition_id: 5, kind: 'read', finished_on: '2026-10-13', source: 'hand', created_at: '2026-10-13T10:00:00Z' }
  const file = [{ id: 50, format: 'epub', path: 'a.epub', size: 1000, part: 1, duration: 0, sha256: 'a', protected: false }]
  const button = () => document.querySelector('[data-testid="reader-finish"]') as HTMLButtonElement

  it('enters the book as finished with one press, however often it is pressed, and then shows the day', async () => {
    serve({ edition: edi({ files: file }) })
    server.answers['GET /api/v1/history?work=1'] = []
    server.answers['POST /api/v1/editions/5/finished'] = entry
    await show()
    expect(button().getAttribute('aria-pressed')).toBe('false')
    expect(button().getAttribute('aria-label')).toBe('Als beendet markieren')
    await act(async () => {
      button().click()
      button().click()
    })
    await idle()
    expect(server.changes().filter((c) => c.includes('finished'))).toEqual(['POST /api/v1/editions/5/finished'])
    expect(button().getAttribute('aria-pressed')).toBe('true')
    expect(button().getAttribute('aria-label')).toBe('Beendet am 13. Oktober 2026')
  })

  it('asks before it takes the entry back, and sends nothing until the answer is yes', async () => {
    serve({ edition: edi({ files: file }) })
    server.answers['GET /api/v1/history?work=1'] = [entry, { ...entry, id: 2, edition_id: 99 }]
    server.answers['DELETE /api/v1/history/3'] = 204
    await show()
    expect(button().getAttribute('aria-pressed')).toBe('true')
    await page.click(button())
    expect(page.dialog).toBeTruthy()
    expect(server.changes().filter((c) => c.includes('history'))).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes().filter((c) => c.includes('history'))).toEqual([])
    await page.click(button())
    await page.inDialog('Nicht beendet')
    expect(server.changes().filter((c) => c.includes('history'))).toEqual(['DELETE /api/v1/history/3'])
    expect(button().getAttribute('aria-pressed')).toBe('false')
  })

  it('says what the server refuses and stays as it was', async () => {
    serve({ edition: edi({ files: file }) })
    server.answers['GET /api/v1/history?work=1'] = []
    server.fails = { 'POST /api/v1/editions/5/finished': { status: 409, code: 'history_limit' } }
    server.install()
    await show()
    await page.click(button())
    expect(document.body.textContent).toContain('Dein Lesestapel ist voll.')
    expect(button().getAttribute('aria-pressed')).toBe('false')
  })
})
