/**
 * Wishes and following the downloads in a real browser, headless, against the real backend and the stand-ins of
 * `e2e/fake-procure.mjs` (an indexer and a SABnzbd on 127.0.0.1, which the tests move on): a book is asked for on its page, the
 * search runs, the best release goes to SABnzbd, tracking follows it (progress, done, a folder nexbooks cannot find with the
 * suggestion the operator confirms, a failure with the history, a job nobody asked for), cancelling asks first, the shelf marks the
 * book, and the lists fit a phone.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-acquire' }
const FAR = process.env.E2E_PROCURE!
const DOWNLOADS = process.env.E2E_DOWNLOADS!
const SHOTS = process.env.E2E_SHOTS

type Keys = { indexer: string; sab: string }
let keys: Keys

test.describe.configure({ mode: 'serial' })
// Tracking asks the programs every fifteen seconds: the tests wait for rounds.
test.setTimeout(150_000)

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(6)
}

test.beforeAll(async ({ playwright }) => {
  const context = await playwright.request.newContext()
  keys = (await (await context.get(`${FAR}/__keys`)).json()) as Keys
  await context.dispose()
})

let salt = ''

test.beforeEach(async ({ page }, testInfo) => {
  // Other specs (and the browsers before this one) leave wishes and downloads of their own behind.
  forget()
  await ensureLibrary(page)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  const indexer = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 0, language: 'de' } })
  expect(indexer.ok(), await indexer.text()).toBe(true)
  const program = await page.request.post('/api/v1/procure/clients', { headers: TAB, data: { kind: 'sabnzbd', url: `${FAR}/sab`, username: '', secret: keys.sab, category: 'nexbooks' } })
  expect(program.ok(), await program.text()).toBe(true)
  salt = testInfo.project.name.toUpperCase()
  await page.request.get(`${FAR}/__reset?salt=${testInfo.project.name}`)
})

/** The app keeps what it did in its tables: the next test starts from nothing. */
function forget(): void {
  const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('downloads', 'procure_wishes', 'procure_history', 'foreign_jobs', 'path_mappings', 'release_searches', 'blocklist'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python, ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

test.afterEach(async ({ page }) => {
  forget()
  const get = async (where: string) => (await page.request.get(where)).json()
  for (const item of ((await get('/api/v1/procure/indexers')) as { indexers: { id: number }[] }).indexers) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
  for (const item of ((await get('/api/v1/procure/clients')) as { clients: { id: number }[] }).clients) await page.request.delete(`/api/v1/procure/clients/${item.id}`, { headers: TAB })
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
  fs.rmSync(path.join(DOWNLOADS, 'complete'), { recursive: true, force: true })
  fs.rmSync(path.join(DOWNLOADS, 'usenet'), { recursive: true, force: true })
})

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
}

async function schwarzerSand(page: Page): Promise<number> {
  const works = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
  const found = works.items.find((item) => item.title.startsWith('Schwarzer'))
  expect(found).toBeTruthy()
  return found!.id
}

/** The recording of the book is asked for through the API (the tests of the page start from the point where a wish is there). */
async function wishAudio(page: Page, work: number): Promise<void> {
  const answer = await page.request.post('/api/v1/procure/wishes', { headers: TAB, data: { items: [{ work_id: work }], kinds: ['audio'], language: 'de' } })
  expect(answer.ok(), await answer.text()).toBe(true)
}

const releaseName = () => `Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE${salt}`

async function waitForDownload(page: Page): Promise<void> {
  await page.goto('/acquire?reiter=running')
  await expect(page.locator('[data-download]')).toHaveCount(1, { timeout: 60_000 })
  await handedOver(page)
  await page.reload()
}

/** The download is past its hand-over: the program has the job (a download that is still being handed over is shown
 * as "sending" a moment before; the program is asked for its category first). */
async function handedOver(page: Page): Promise<void> {
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/procure/running')).json()) as { downloads: { phase: string }[] }).downloads.map((item) => item.phase).join(','), { timeout: 60_000, intervals: [250] })
    .toMatch(/^(?!.*sending).+$/)
}


async function moveJob(page: Page, query: string): Promise<void> {
  const answer = await page.request.get(`${FAR}/__sab?job=last&${query}`)
  expect(answer.ok()).toBe(true)
}

test('a book is asked for on its page, found, loaded and followed until it waits to be filed', async ({ page }) => {
  const work = await schwarzerSand(page)
  await page.goto(`/work/${work}`)
  const missing = page.locator('.twin.missing[data-kind="audio"]')
  await expect(missing.locator('[data-testid="acquire"]')).toHaveText('Hörbuch beschaffen')
  await shot(page, 'app-work-missing')
  await missing.locator('[data-testid="acquire"]').click()
  const dialog = page.getByRole('dialog', { name: 'Beschaffen' })
  await expect(dialog.locator('[data-testid="acquire-sum"]')).toContainText('Hörbuch: nimmt')
  await shot(page, 'app-dialog')
  await dialog.locator('[data-testid="acquire-go"]').click()
  await expect(page.locator('[data-testid="acquire-notice"]')).toContainText('Wunsch angelegt: Schwarzer Sand')
  // The page of the book says where the recording stands, and then that it loads.
  await expect(missing.locator('[data-testid="state-line"]')).toBeVisible({ timeout: 30_000 })
  await waitForDownload(page)
  const row = page.locator('[data-download]')
  await expect(row).toContainText(releaseName())
  await expect(page.locator('.tabs a[href="/acquire"] .wishcount')).toHaveText('1')
  await moveJob(page, 'pct=40')
  await expect(row.locator('.pgrow')).toContainText('40 %', { timeout: 60_000 })
  await expect(row.locator('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '40')
  await shot(page, 'app-running')
  const folder = path.join(DOWNLOADS, 'complete', 'nexbooks', releaseName())
  fs.mkdirSync(folder, { recursive: true })
  await moveJob(page, `storage=${encodeURIComponent(folder)}`)
  // Done in the program: the folder is checked. It holds no book, so the release is turned down and blocked.
  await rejectedForNoBook(page)
})

/** The check after the download turned the release down: its folder held no book. */
async function rejectedForNoBook(page: Page): Promise<void> {
  await page.goto('/acquire?reiter=history')
  const line = page.locator('.hr[data-result="rejected"]')
  await expect(line).toHaveCount(1, { timeout: 60_000 })
  await expect(line).toContainText('kein lesbares Buch in den Dateien')
}

test('a folder nexbooks cannot find comes with a suggestion, which the operator confirms', async ({ page }) => {
  const work = await schwarzerSand(page)
  await wishAudio(page, work)
  await waitForDownload(page)
  const real = path.join(DOWNLOADS, 'usenet', 'complete', 'nexbooks', releaseName())
  fs.mkdirSync(real, { recursive: true })
  await moveJob(page, `storage=${encodeURIComponent(`/downloads/complete/nexbooks/${releaseName()}`)}`)
  await page.goto('/acquire?reiter=needs')
  const problem = page.locator('[data-problem]')
  await expect(problem).toHaveCount(1, { timeout: 60_000 })
  await expect(problem.locator('.rchip')).toHaveText('Pfad nicht gefunden')
  await expect(problem.locator('.pair')).toContainText('/downloads')
  // The card says in one sentence what to do.
  await expect(problem.locator('[data-testid="mapping-todo"]')).toHaveText('Prüf den Vorschlag und bestätige ihn. Danach holt nexbooks die Datei selbst.')
  await shot(page, 'app-needs-path')
  await shots(page, 'pfadzuordnung-karte', '[data-problem]')
  await problem.locator('[data-act="mapping"]').click()
  const dialog = page.getByRole('dialog', { name: 'Pfadzuordnung bestätigen' })
  await expect(dialog).toContainText('Ist das derselbe Ordner?')
  await shot(page, 'app-mapping')
  await dialog.locator('[data-act="confirm-mapping"]').click()
  await expect(page.locator('[data-problem]')).toHaveCount(0, { timeout: 60_000 })
  // Found now, and checked: the folder holds no book.
  await rejectedForNoBook(page)
})

test('cancelling asks first, takes the job out of the program, and leaves the wish waiting', async ({ page }) => {
  const work = await schwarzerSand(page)
  await wishAudio(page, work)
  await waitForDownload(page)
  await page.locator('[data-download] [data-act="cancel"]').click()
  const dialog = page.getByRole('dialog', { name: 'Download abbrechen?' })
  await expect(dialog).toContainText('Soll das Release gesperrt und ein anderes gesucht werden?')
  await shot(page, 'app-cancel')
  await dialog.getByRole('button', { name: 'Nichts ändern', exact: true }).click()
  await expect(page.locator('[data-download]')).toHaveCount(1)
  await page.locator('[data-download] [data-act="cancel"]').click()
  await page.getByRole('dialog', { name: 'Download abbrechen?' }).locator('[data-act="cancel-only"]').click()
  await expect(page.locator('[data-download]')).toHaveCount(0)
  await page.goto('/acquire')
  await expect(page.locator('[data-wish]')).toHaveCount(1)
  await expect(page.locator('[data-wish]')).toContainText('Du hast den Download abgebrochen.')
  const gone = await page.request.get(`${FAR}/__sab?job=last`)
  expect(gone.status()).toBe(404)
})

test('a failed job is blocked, written into the history, and the wish says that nothing else fits', async ({ page }) => {
  const work = await schwarzerSand(page)
  await wishAudio(page, work)
  await waitForDownload(page)
  await moveJob(page, 'fail=Aborted%2C%20cannot%20be%20completed')
  await page.goto('/acquire?reiter=history')
  const line = page.locator('[data-result="failed"]')
  await expect(line).toHaveCount(1, { timeout: 60_000 })
  await expect(line).toContainText('unvollständig')
  await shot(page, 'app-history')
  await page.goto('/acquire?reiter=blocklist')
  await expect(page.locator('[data-block]')).toHaveCount(1)
  await expect(page.locator('[data-block]')).toContainText(releaseName())
  await page.goto('/acquire')
  await expect(page.locator('[data-wish]')).toContainText(/Nichts gefunden|Sucht gerade/, { timeout: 60_000 })
})

test('a job nobody asked for is shown, never touched, and can be ignored', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'The look at the category comes once a minute: one engine is enough.')
  test.setTimeout(180_000)
  await page.request.get(`${FAR}/__sab/foreign?name=Unbekannter.Autor.Unbekanntes.Buch.2019.GERMAN.EPUB-EXAMPLE`)
  await page.goto('/acquire?reiter=needs')
  const card = page.locator('[data-foreign]')
  await expect(card).toHaveCount(1, { timeout: 120_000 })
  await expect(card).toContainText('Fremder Auftrag in der Kategorie')
  await shot(page, 'app-needs-foreign')
  await card.locator('[data-act="ignore"]').click()
  await page.getByRole('dialog', { name: 'Auftrag ignorieren?' }).getByRole('button', { name: 'Ignorieren' }).click()
  await expect(page.locator('[data-foreign]')).toHaveCount(0)
})

test('the shelf marks a book whose recording is asked for', async ({ page }) => {
  const work = await schwarzerSand(page)
  await wishAudio(page, work)
  await page.goto('/')
  await expect(page.locator(`[data-work="${work}"]`).first()).toHaveClass(/incoming/, { timeout: 30_000 })
  await shot(page, 'app-shelf-band')
})

test('the lists fit a phone without making the page wider', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const work = await schwarzerSand(page)
  await wishAudio(page, work)
  await waitForDownload(page)
  await moveJob(page, 'pct=40')
  for (const tab of ['', '?reiter=running', '?reiter=needs', '?reiter=history', '?reiter=blocklist']) {
    await page.goto(`/acquire${tab}`)
    await expect(page.locator('[data-testid="acquire"]')).toBeVisible()
    const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(wide, tab).toBeLessThanOrEqual(0)
  }
  await page.goto('/acquire?reiter=running')
  await shot(page, 'app-running-390')
})

// --- Gesucht as a list (made-up wishes in the answer of the list) ----------------------------------------------------

type Made = { id: number; title: string; author: string; kind: 'ebook' | 'audio'; state: string; by: string; hours: number | null; detail?: Record<string, unknown> }

const MADE: Made[] = [
  { id: 1, title: 'Die Nebelbahn', author: 'Rune Falkner', kind: 'ebook', state: 'not_found', by: 'Mira', hours: 5, detail: { total: 12, reasons: [{ code: 'other_language', count: 9 }, { code: 'bundle', count: 3 }] } },
  { id: 2, title: 'Das Glashaus am Fluss', author: 'Iris Quist', kind: 'audio', state: 'searching', by: 'Tom', hours: null },
  { id: 3, title: 'Laternenstraße', author: 'Rune Falkner', kind: 'ebook', state: 'waiting', by: 'Mira', hours: null },
  { id: 4, title: 'Nordlicht über dem Hafen', author: 'Ada Feldkamp', kind: 'audio', state: 'upgrade', by: 'Mira', hours: 30, detail: { have: 'mp3_128', goal: 'flac' } },
  { id: 5, title: 'Der stille Archivar', author: 'Jonas Lindholm', kind: 'ebook', state: 'not_found', by: 'Tom', hours: 2, detail: { total: 4, reasons: [{ code: 'abridged', count: 4 }] } },
  { id: 6, title: 'Zwölf Türme', author: 'Iris Quist', kind: 'ebook', state: 'idle', by: 'Mira', hours: null, detail: { reason: 'no_right' } },
]

function wishes(): unknown[] {
  const now = Date.now()
  return MADE.map((item) => ({
    id: item.id,
    kind: item.kind,
    language: 'de',
    state: item.state,
    detail: item.detail ?? null,
    library: { id: 1, name: item.kind === 'ebook' ? 'Bücher' : 'Hörbücher' },
    by: item.by,
    mine: item.by === 'Mira',
    created_at: new Date(now - (item.id + 1) * 3_600_000).toISOString(),
    searched_at: item.hours === null ? null : new Date(now - item.hours * 3_600_000 / 2).toISOString(),
    next_try_at: item.hours === null ? null : new Date(now + item.hours * 3_600_000).toISOString(),
    release_date: item.state === 'waiting' ? '2027-02-01' : '',
    waiting_for_a_place: false,
    handing_over_again: false,
    book: { work_id: null, book: `tok${item.id}`, title: item.title, authors: [item.author], series: null, series_pos: null, color: '#8e3b46', cover_url: null },
  }))
}

async function madeUpWishes(page: Page): Promise<void> {
  await page.route('**/api/v1/procure/wishes', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: { wishes: wishes() } }) : route.fallback()))
}

test.describe('Gesucht as a list', () => {
  // (From the second page on the worker of the app stands before the requests, and WebKit does not show them to `page.route`.)
  test.use({ serviceWorkers: 'block' })

  test('is the first look, the columns put the wishes in order, the cards are one press away and the choice stays', async ({ page }) => {
    await madeUpWishes(page)
    // (An earlier test of this browser may have chosen the cards.)
    await page.goto('/')
    await page.evaluate(() => window.localStorage.removeItem('nexbooks.acquire.wanted'))
    await page.goto('/acquire')
    const rows = page.locator('[data-testid="wanted-list"] tbody tr[data-wish]')
    await expect(rows).toHaveCount(6)
    await expect(rows.first()).toContainText('Die Nebelbahn')
    await expect(rows.first()).toContainText('Nichts gefunden')
    await shots(page, 'gesucht-liste')

    await page.locator('thead [data-sort="title"]').click()
    await expect(rows.first()).toContainText('Das Glashaus am Fluss')
    await page.locator('thead [data-sort="title"]').click()
    await expect(rows.first()).toContainText('Zwölf Türme')

    await rows.first().locator('[data-act="remove"]').click()
    await expect(page.getByRole('dialog')).toContainText('Gefundene oder geladene Bücher bleiben, wie sie sind.')
    await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click()

    await page.locator('[data-wish="1"] [data-act="reasons"]').click()
    await expect(page.getByRole('dialog')).toContainText('12 Treffer, keiner passte')
    await expect(page.getByTestId('wanted-reasons')).toContainText('9 × andere Sprache')
    await shots(page, 'gesucht-gruende')
    await page.getByRole('dialog').locator('[data-close]').click()

    await page.locator('[data-look="cards"]').click()
    await expect(page.getByTestId('wanted')).toBeVisible()
    await shots(page, 'gesucht-karten')
    await page.reload()
    await expect(page.getByTestId('wanted')).toBeVisible()
    await page.locator('[data-look="list"]').click()
    await expect(page.getByTestId('wanted-list')).toBeVisible()
  })
})
