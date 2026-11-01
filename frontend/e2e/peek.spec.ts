/**
 * Looking into a book from the review list, and sending a wrong book away, in a real browser (headless), against the real
 * backend with invented books. The list of books the matching is unsure about is made up by the test (the sources are off here,
 * so the real matching would leave nothing to look at); the editions it points to are real, so the real frame of the reader, the
 * real files and the real recordings are used. The checks that matter: the first pages show, the recording plays, and **nothing
 * is saved** (no place, no bookmark). Sending a book away is checked with the answers of the page of the book made up; the
 * route itself, with real files, is in the backend's tests.
 *
 * WebKit plays nothing here (see player.spec.ts): the recording is not played there.
 */
import { expect, test, type Frame, type Page } from '@playwright/test'
import path from 'node:path'

import { shots } from './shots'

// The answers about the review list are made up by the page's routes; the service worker of the app would answer first.
test.use({ serviceWorkers: 'block' })

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-peek0000' }
const WEBKIT_SILENT = 'Playwright WebKit on Windows loads media without cookies (Media Foundation): no recording of a signed-in person plays there'

type Edition = { id: number; work: number }
const editions = new Map<string, Edition>()
const libraries: number[] = []
let reading = 0
let listening = 0
let parts = 0

async function make(page: Page, name: string, folder: string, kind: string, total: number): Promise<number> {
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name, path: path.join(process.env.E2E_BOOKS!, folder), kind, scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  const id = ((await made.json()) as { id: number }).id
  libraries.push(id)
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${id}&per_page=100`)).json()) as { total: number }).total, { timeout: 90_000, intervals: [1000] })
    .toBe(total)
  const list = (await (await page.request.get(`/api/v1/works?library=${id}&per_page=100`)).json()) as { items: { id: number; title: string; editions: { id: number; kind: string }[] }[] }
  for (const item of list.items) editions.set(`${folder}:${item.title}`, { id: item.editions.find((edition) => edition.kind === (kind === 'audio' ? 'audio' : 'ebook'))!.id, work: item.id })
  return id
}

async function ensure(page: Page): Promise<void> {
  if (reading) return
  reading = await make(page, 'Peek reading', 'reading', 'ebook', 16)
  listening = await make(page, 'Peek listening', 'listening', 'audio', 8)
}

async function ensureParts(page: Page): Promise<void> {
  if (parts) return
  parts = await make(page, 'Peek parts', 'listening-parts', 'audio', 1)
}

test.afterAll(async ({ browser }) => {
  // The books of this spec leave with it: the shelf spec counts the works it sees.
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  for (const id of libraries) await context.request.delete(`/api/v1/libraries/${id}`, { headers: TAB })
  await context.close()
})

const PROPOSAL = {
  id: 'openlibrary:OL1W:', source: 'openlibrary', title: 'The Long Road', subtitle: '', authors: ['Lindqvist'], narrators: [], year: 2021, languages: ['en'], series: '', series_pos: null,
  runtime_min: null, variant: '', market: '', score: 68, why: [{ code: 'title_exact', text: 'x' }, { code: 'author_weak', text: 'x' }], missing: [], against: [], detail: {},
  lacks: ['isbn'], lacks_join: 'or', cap: 'author_weak', fatal: false, found: false, cover_url: null,
}

function listed(library: number, edition: number, kind: 'ebook' | 'audio', title: string) {
  return {
    id: 7, edition_id: edition, work_id: 1, library_id: library, state: 'maybe', reason: 'author_weak', reason_text: '', kind, title,
    subtitle: '', authors: ['Mara Lindqvist'], language: 'en', year: null, series: '', series_pos: null, file: `Peek/${title}.epub`, cover_url: null, gap: null, proposals: [PROPOSAL],
  }
}

function facts(edition: number, kind: 'ebook' | 'audio') {
  return {
    entry_id: 7, edition_id: edition, kind, folder: 'Peek', files: [{ id: 1, name: 'The Long Road.epub', folder: 'Peek', format: 'epub', size: 10, part: 1, duration: 0, pages: 100, chapters: 3, protected: false }],
    says: { title: 'The Long Road', subtitle: '', authors: ['Mara Lindqvist'], narrators: [], series: '', series_pos: null, isbn: '', asin: '', publisher: 'Orchard House', year: 2019, language: 'en', text_language: 'en', genre: '', subjects: ['Fiction'], pages: 100, pages_counted: false, duration: 0, chapters: 3 },
    cover_url: null,
    proposals: [{ proposal_id: 'openlibrary:OL1W:', rows: [
      { field: 'title', file: 'The Long Road', source: 'The Long Road', state: 'agrees', unit: '' },
      { field: 'authors', file: 'Mara Lindqvist', source: 'Lindqvist', state: 'similar', unit: '' },
      { field: 'year', file: '2019', source: '2021', state: 'differs', unit: '' },
      { field: 'language', file: 'en', source: 'en', state: 'agrees', unit: '' },
      { field: 'isbn', file: '', source: '9780000000002', state: 'file_silent', unit: '' },
      { field: 'publisher', file: 'Orchard House', source: '', state: 'source_silent', unit: '' },
    ] }],
  }
}

async function show(page: Page, library: number, entry: ReturnType<typeof listed>): Promise<void> {
  await page.route('**/api/v1/libraries', (route) => (route.request().method() === 'GET'
    ? route.fulfill({ json: [{ id: library, name: 'Peek', kind: 'both', path: '/books/Peek', my_right: 'manage', state: 'idle', scan_enabled: true, scanned_at: null, report: null, counts: { ebooks: 1, audiobooks: 1, files: 1, bytes: 1, problems: 0, review: 1, entries: 1, pending: 0 }, progress: { done: 0, total: 0 }, matching: 'idle', resting: [] }] })
    : route.continue()))
  await page.route(/\/api\/v1\/libraries\/\d+\/review/, (route) => route.fulfill({ json: { total: 1, items: [entry] } }))
  await page.route('**/api/v1/review/7/file', (route) => route.fulfill({ json: facts(entry.edition_id, entry.kind as 'ebook' | 'audio') }))
  await page.goto(`/review?library=${library}`)
  await expect(page.locator('[data-testid="review-item"]')).toHaveCount(1)
}

/** Everything the page sent that could save a place or a mark. */
function watchSaving(page: Page): string[] {
  const sent: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET' && /\/(progress|bookmarks|finished)\b/.test(request.url())) sent.push(`${request.method()} ${request.url()}`)
  })
  return sent
}

/**
 * What Safari does and the others do not: an element gets no data until it has been played in a tap (preload is ignored), and
 * one that was played in a tap may load and play later by itself. The address of a part is held back until then.
 */
const LOADS_AFTER_A_TAP = `(() => {
  const proto = HTMLMediaElement.prototype
  const own = Object.getOwnPropertyDescriptor(proto, 'src')
  const play = proto.play
  Object.defineProperty(proto, 'src', {
    configurable: true,
    get() { return this.__held !== undefined ? this.__held : own.get.call(this) },
    set(value) {
      if (this.__tapped) { this.__held = undefined; own.set.call(this, value) } else this.__held = value
    },
  })
  proto.play = function () {
    if (navigator.userActivation.isActive && !this.__tapped) {
      this.__tapped = true
      if (this.__held !== undefined) { const held = this.__held; this.__held = undefined; own.set.call(this, held) }
    }
    return play.call(this)
  }
})()`

const pageFrame = (page: Page): Frame | undefined => page.frames().find((frame) => frame.url().startsWith('blob:'))

test.describe('a look into a book', () => {
  test('shows the first pages of an e-book, turns them, and saves nothing', async ({ page }) => {
    await ensure(page)
    const book = editions.get('reading:The Long Road')!
    const sent = watchSaving(page)
    await show(page, reading, listed(reading, book.id, 'ebook', 'The Long Road'))
    await page.locator('[data-testid="review-read"]').click()
    await expect(page.locator('[data-testid="peek-book"][data-phase="open"]')).toBeVisible({ timeout: 30_000 })
    await expect.poll(async () => (await pageFrame(page)?.locator('body').innerText()) ?? '', { timeout: 30_000 }).not.toBe('')
    await expect(page.locator('[data-testid="peek-where"]')).toHaveText(/\d+ percent/)
    const first = Number(/(\d+) percent/.exec((await page.locator('[data-testid="peek-where"]').textContent()) ?? '')![1])
    for (let turn = 0; turn < 3; turn++) await page.locator('[data-testid="peek-next"]').click()
    await expect
      .poll(async () => Number(/(\d+) percent/.exec((await page.locator('[data-testid="peek-where"]').textContent()) ?? '')?.[1] ?? -1), { timeout: 15_000 })
      .toBeGreaterThan(first)
    await expect(page.getByText('None of this is saved')).toBeVisible()
    await shots(page, 'peek-read')
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="peek-dialog"]')).toHaveCount(0)
    // What the file says, beside the proposal: the contradiction apart from what the file leaves unsaid.
    await page.locator('[data-testid="review-facts"]').click()
    await expect(page.locator('[data-testid="proposal-compare"] .compare-group[data-state="differs"]')).toContainText('File: 2019 · Source: 2021')
    await expect(page.locator('[data-testid="proposal-compare"] .compare-group[data-state="file_silent"]')).toContainText('ISBN')
    await shots(page, 'peek-file', '[data-testid="review-item"]')
    // Not one place, bookmark or mark went to the server, and the server knows no place of this book.
    expect(sent).toEqual([])
    const place = await page.request.get(`/api/v1/editions/${book.id}/progress`)
    expect(place.ok()).toBe(true)
    expect(await place.json()).toBeNull()
  })

  test('says what the reader cannot show instead of an empty page', async ({ page }) => {
    await ensure(page)
    const locked = editions.get('reading:Locked Room')
    test.skip(!locked, 'the invented copy-protected book is not part of this run')
    await show(page, reading, listed(reading, locked!.id, 'ebook', 'Locked Room'))
    await page.locator('[data-testid="review-read"]').click()
    await expect(page.locator('[data-testid="peek-none"]')).toBeVisible()
    await expect(page.locator('[data-testid="peek-frame"]')).toHaveCount(0)
  })
})

test.describe('a listen into a recording', () => {
  test('plays from the start and from a third in, lists the chapters and saves nothing', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await ensure(page)
    const book = editions.get('listening:The Quiet Harbour')!
    const sent = watchSaving(page)
    await show(page, listening, listed(listening, book.id, 'audio', 'The Quiet Harbour'))
    await page.locator('[data-testid="review-listen"]').click()
    await expect(page.locator('[data-testid="peek-time"]')).toHaveText('0:00 / 1:00')
    await page.locator('[data-testid="peek-play"]').click()
    const playing = () =>
      page.evaluate(() => {
        const element = [...document.querySelectorAll<HTMLAudioElement>('[data-testid="peek-audio"]')].find((audio) => !audio.paused)
        return element ? element.currentTime : -1
      })
    await expect.poll(playing, { timeout: 15_000 }).toBeGreaterThan(0)
    await page.locator('[data-jump="third"]').click()
    await expect.poll(playing, { timeout: 15_000 }).toBeGreaterThanOrEqual(19)
    await page.locator('[data-testid="peek-chapters-toggle"]').click()
    await expect(page.locator('[data-testid="peek-chapters"] li')).toHaveCount(3)
    await shots(page, 'peek-listen', '[data-testid="review-item"]')
    await page.locator('[data-testid="peek-play"]').click()
    await expect.poll(playing).toBe(-1)
    expect(sent).toEqual([])
    const place = await page.request.get(`/api/v1/editions/${book.id}/progress`)
    expect(await place.json()).toBeNull()
  })

  for (const strict of [false, true]) test(`plays a recording that lies in many files, one chapter to a file${strict ? ', in a browser that loads media only after a tap' : ''}`, async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    if (strict) await page.addInitScript(LOADS_AFTER_A_TAP)
    await ensureParts(page)
    const book = editions.get('listening-parts:Der lange Weg')!
    expect(book, 'the recording in twelve files was read as one book').toBeTruthy()
    const sent = watchSaving(page)
    const asked: string[] = []
    page.on('request', (request) => {
      if (/\/files\/\d+\/content/.test(request.url())) asked.push(request.url())
    })
    await show(page, parts, listed(parts, book.id, 'audio', 'Der lange Weg'))
    await page.locator('[data-testid="review-listen"]').click()
    await expect(page.locator('[data-testid="peek-listen"]')).toBeVisible()
    await expect(page.locator('[data-testid="peek-time"]')).toHaveText(/^0:00 \/ (0:59|1:00)$/)
    await expect(page.locator('[data-testid="peek-play"]')).toBeEnabled()
    await page.locator('[data-testid="peek-play"]').click()
    const playing = () =>
      page.evaluate(() => {
        const element = [...document.querySelectorAll<HTMLAudioElement>('[data-testid="peek-audio"]')].find((audio) => !audio.paused)
        return element ? element.currentTime : -1
      })
    await expect.poll(playing, { timeout: 15_000 }).toBeGreaterThan(0)
    expect(asked.length, 'the file was asked for').toBeGreaterThan(0)
    // From a third in: the seventh file, not the first.
    await page.locator('[data-jump="third"]').click()
    await expect.poll(async () => asked.some((url) => !asked[0].endsWith(url.split('/files/')[1])), { timeout: 15_000 }).toBe(true)
    await expect.poll(playing, { timeout: 15_000 }).toBeGreaterThanOrEqual(0)
    await shots(page, 'peek-listen-parts', '[data-testid="review-item"]')
    expect(sent).toEqual([])
    expect(await (await page.request.get(`/api/v1/editions/${book.id}/progress`)).json()).toBeNull()
  })

  test('has its buttons and the honest notes in every browser', async ({ page }) => {
    await ensure(page)
    const book = editions.get('listening:Seven Bridges')!
    await show(page, listening, listed(listening, book.id, 'audio', 'Seven Bridges'))
    await expect(page.locator('[data-testid="review-read"]')).toHaveCount(0)
    await page.locator('[data-testid="review-listen"]').click()
    await expect(page.locator('[data-testid="peek-listen"]')).toContainText('Your place in the recording does not change')
  })
})

test.describe('a wrong book', () => {
  test('is sent away from the page of the book after a question, and the answer says what became of it', async ({ page }) => {
    await ensure(page)
    const book = editions.get('reading:The Long Road')!
    const real = (await (await page.request.get(`/api/v1/works/${book.work}`)).json()) as { editions: Record<string, unknown>[] }
    real.editions[0].delivery = { release: 'Mara.Lindqvist-The.Long.Road-EPUB-EN-2021-EXAMPLE', indexer: 'Shelf', delivered_at: '2026-10-20T10:00:00Z', trash_days: 30 }
    await page.route(new RegExp(`/api/v1/works/${book.work}$`), (route) => route.fulfill({ json: real }))
    const sent: string[] = []
    await page.route(/\/api\/v1\/editions\/\d+\/wrong-book/, (route) => {
      sent.push(route.request().url())
      return route.fulfill({ json: { trash_id: 3, replacement: 'searching', code: '', work_id: book.work, missing: [], people: 2 } })
    })
    await page.goto(`/work/${book.work}`)
    await page.locator('[data-testid="edit-book"]').click()
    await expect(page.locator('[data-testid="wrong-book-row"]')).toContainText('Mara.Lindqvist-The.Long.Road-EPUB-EN-2021-EXAMPLE')
    await page.locator('[data-act="wrong-book"]').click()
    await expect(page.getByText('Send this book away?')).toBeVisible()
    await expect(page.getByText('keeps them for 30 days')).toBeVisible()
    await expect(page.getByText('come back when you take the files out of the trash')).toBeVisible()
    await shots(page, 'peek-wrong-ask')
    expect(sent).toEqual([])
    await page.getByRole('button', { name: 'Send away' }).click()
    await expect(page.locator('[data-testid="wrong-done"]')).toContainText('nexbooks is looking for another release of the book now')
    await expect(page.locator('[data-testid="wrong-people"]')).toContainText('history of 2 people are kept and come back')
    await shots(page, 'peek-wrong-done')
    expect(sent.length).toBe(1)
    await page.locator('[data-testid="wrong-done-ok"]').click()
    await expect(page.locator('[data-testid="wrong-done"]')).toHaveCount(0)
  })
})
