/**
 * Checking and filing in a real browser, headless, against the real backend and the stand-ins of
 * `e2e/fake-procure.mjs`: a finished download is checked and filed into its library and the book shows its recording; a
 * file that names another book waits under "Braucht dich", and blocking it asks first; a book uploaded from the library goes
 * into the library the same way; the upload fits a phone. Each test files into a library of its own, made in a folder of
 * its own and taken away afterwards, so that the shelf of the other specs stays as it is.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-filing00' }
const FAR = process.env.E2E_PROCURE!
const DOWNLOADS = process.env.E2E_DOWNLOADS!
const SHOTS = process.env.E2E_SHOTS

type Keys = { indexer: string; sab: string }
let keys: Keys
let salt = ''
let made: number[] = []

test.describe.configure({ mode: 'serial' })
test.setTimeout(150_000)

const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.resolve('..', 'backend', venv) : 'python'

/** Invented books, made by the makers of the backend's tests. */
function make(code: string): void {
  execFileSync(python, ['-c', code], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
}

/** The album says which reading it is: without such a word (or a running time from a catalogue) a person decides. */
function recording(folder: string, album: string): void {
  make(['from pathlib import Path', 'from tests.bookmaker import make_mp3', `for n in (1, 2): make_mp3(Path(${JSON.stringify(folder)}) / f'{n:02d}.mp3', seconds=3.0, track=n, title=${JSON.stringify(album)}, album=${JSON.stringify(album + ' (ungekürzt)')}, artist='Jonas Lindholm')`].join(String.fromCharCode(10)))
}

function epub(file: string, title: string): void {
  make(['from pathlib import Path', 'from tests.bookmaker import make_epub', `make_epub(Path(${JSON.stringify(file)}), title=${JSON.stringify(title)}, authors=('Ada Beispiel',), language='de')`].join(String.fromCharCode(10)))
}

function forget(): void {
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('downloads', 'procure_wishes', 'procure_history', 'foreign_jobs', 'path_mappings', 'release_searches', 'blocklist', 'procure_files', 'procure_notices'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python, ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
}

/** A library of the test's own, in a new folder below the books. */
async function library(page: Page, name: string, kind: 'both' | 'audio' | 'ebook' = 'both'): Promise<{ id: number; folder: string }> {
  const folder = path.join(process.env.E2E_BOOKS!, `${name.toLowerCase()}-${salt.toLowerCase()}`)
  fs.rmSync(folder, { recursive: true, force: true })
  fs.mkdirSync(folder, { recursive: true })
  const answer = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: `${name} ${salt}`, path: folder, kind, scan_enabled: false } })
  expect(answer.ok(), await answer.text()).toBe(true)
  const id = ((await answer.json()) as { id: number }).id
  made.push(id)
  return { id, folder }
}

async function schwarzerSand(page: Page): Promise<number> {
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(6)
  const works = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
  const found = works.items.find((item) => item.title.startsWith('Schwarzer'))
  expect(found).toBeTruthy()
  return found!.id
}

async function ensureShelf(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; name: string }[]
  if (!mine.some((item) => item.name === 'Regal')) {
    const answer = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(answer.ok(), await answer.text()).toBe(true)
  }
}

test.beforeAll(async ({ playwright }) => {
  const context = await playwright.request.newContext()
  keys = (await (await context.get(`${FAR}/__keys`)).json()) as Keys
  await context.dispose()
})

test.beforeEach(async ({ page }, testInfo) => {
  forget()
  made = []
  await ensureShelf(page)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  const indexer = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 0, language: 'de' } })
  expect(indexer.ok(), await indexer.text()).toBe(true)
  const program = await page.request.post('/api/v1/procure/clients', { headers: TAB, data: { kind: 'sabnzbd', url: `${FAR}/sab`, username: '', secret: keys.sab, category: 'nexbooks' } })
  expect(program.ok(), await program.text()).toBe(true)
  salt = testInfo.project.name.toUpperCase()
  await page.request.get(`${FAR}/__reset?salt=${testInfo.project.name}`)
})

test.afterEach(async ({ page }) => {
  forget()
  const get = async (where: string) => (await page.request.get(where)).json()
  for (const item of ((await get('/api/v1/procure/indexers')) as { indexers: { id: number }[] }).indexers) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
  for (const item of ((await get('/api/v1/procure/clients')) as { clients: { id: number }[] }).clients) await page.request.delete(`/api/v1/procure/clients/${item.id}`, { headers: TAB })
  for (const id of made) await page.request.delete(`/api/v1/libraries/${id}`, { headers: TAB })
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
  fs.rmSync(path.join(DOWNLOADS, 'complete'), { recursive: true, force: true })
})

async function loadInto(page: Page, work: number, libraryId: number): Promise<void> {
  const answer = await page.request.post('/api/v1/procure/wishes', { headers: TAB, data: { items: [{ work_id: work }], kinds: ['audio'], language: 'de', library_id: libraryId } })
  expect(answer.ok(), await answer.text()).toBe(true)
  await page.goto('/acquire?reiter=running')
  await expect(page.locator('[data-download]')).toHaveCount(1, { timeout: 60_000 })
  await handedOver(page)
}

/** The download is past its hand-over: the program has the job (a download that is still being handed over is shown
 * as "sending" a moment before; the program is asked for its category first). */
async function handedOver(page: Page): Promise<void> {
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/procure/running')).json()) as { downloads: { phase: string }[] }).downloads.map((item) => item.phase).join(','), { timeout: 60_000, intervals: [250] })
    .toMatch(/^(?!.*sending).+$/)
}

async function finishJob(page: Page, folder: string): Promise<void> {
  const answer = await page.request.get(`${FAR}/__sab?job=last&storage=${encodeURIComponent(folder)}`)
  expect(answer.ok()).toBe(true)
}

test('a finished download is checked and filed, and the book has its recording', async ({ page }) => {
  const work = await schwarzerSand(page)
  const { id, folder: shelf } = await library(page, 'Eingang', 'audio')
  await loadInto(page, work, id)
  const folder = path.join(DOWNLOADS, 'complete', 'nexbooks', `Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE${salt}`)
  recording(folder, 'Schwarzer Sand')
  await finishJob(page, folder)
  await page.goto('/acquire?reiter=history')
  const line = page.locator('.hr[data-result="done"]')
  await expect(line).toHaveCount(1, { timeout: 90_000 })
  // What was wished and what was filed stand side by side (since the wrong book of the probe).
  await expect(line).toContainText('Gewünscht: Schwarzer Sand · Jonas Lindholm')
  await expect(line).toContainText(`eingeordnet als „Schwarzer Sand“ in Eingang ${salt}`)
  await shot(page, 'app-history-filed')
  const placed = fs.readdirSync(shelf, { recursive: true }).map(String).filter((name) => name.endsWith('.mp3'))
  expect(placed.length).toBe(2)
  // The book shows its recording now, and nothing is on its way any more.
  await page.goto(`/work/${work}`)
  await expect(page.locator('.twin[data-kind="audio"]:not(.missing)')).toHaveCount(1, { timeout: 30_000 })
  await page.goto('/acquire?reiter=running')
  await expect(page.locator('[data-download]')).toHaveCount(0)
})

test('files that name another book wait for a person, can be looked into, and blocking them asks first', async ({ page, browserName }) => {
  const work = await schwarzerSand(page)
  const { id, folder: shelf } = await library(page, 'Zweifel', 'audio')
  await loadInto(page, work, id)
  const folder = path.join(DOWNLOADS, 'complete', 'nexbooks', `Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE${salt}`)
  recording(folder, 'Ein ganz anderer Titel')
  await finishJob(page, folder)
  await page.goto('/acquire?reiter=needs')
  const card = page.locator('[data-code="files_unassigned"]')
  await expect(card).toHaveCount(1, { timeout: 90_000 })
  await expect(card.locator('.rchip')).toHaveText('Buch nicht sicher erkannt')
  await expect(card).toContainText('In den Dateien steht: Ein ganz anderer Titel · Jonas Lindholm')
  await shot(page, 'app-needs-unassigned')
  // The files still lie in the download folder: a person can see what they say and listen into them before answering.
  await card.locator('[data-testid="look-facts"]').click()
  await expect(card.locator('[data-testid="look-facts-panel"]')).toContainText('01.mp3')
  await expect(card.locator('[data-testid="look-facts-panel"]')).toContainText('Ein ganz anderer Titel')
  await expect(card.locator('[data-testid="look-read"]')).toHaveCount(0)
  await card.locator('[data-testid="look-listen"]').click()
  await expect(page.locator('[data-testid="peek-play"]')).toBeEnabled()
  if (browserName !== 'webkit') {
    // (Playwright's WebKit on Windows plays no recording of a signed-in person: see player.spec.ts.)
    const asked: string[] = []
    page.on('request', (request) => {
      if (/\/procure\/downloads\/\d+\/files\/\d+\/content/.test(request.url())) asked.push(request.url())
    })
    await page.locator('[data-testid="peek-play"]').click()
    await expect
      .poll(() => page.evaluate(() => [...document.querySelectorAll<HTMLAudioElement>('[data-testid="peek-audio"]')].some((audio) => !audio.paused && audio.currentTime > 0)), { timeout: 20_000 })
      .toBe(true)
    expect(asked.length).toBeGreaterThan(0)
    await page.locator('[data-testid="peek-play"]').click()
  }
  await shot(page, 'app-needs-look')
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="peek-listen"]')).toHaveCount(0)
  await card.locator('[data-act="block"]').click()
  const dialog = page.getByRole('dialog', { name: 'Release sperren und weitersuchen?' })
  await expect(dialog).toContainText('Die geladenen Dateien werden gelöscht')
  await dialog.getByRole('button', { name: 'Abbrechen' }).click()
  await expect(card).toHaveCount(1)
  await card.locator('[data-act="block"]').click()
  await page.getByRole('dialog', { name: 'Release sperren und weitersuchen?' }).getByRole('button', { name: 'Sperren und weitersuchen' }).click()
  await expect(page.locator('[data-code="files_unassigned"]')).toHaveCount(0, { timeout: 30_000 })
  await page.goto('/acquire?reiter=blocklist')
  await expect(page.locator('[data-block]')).toHaveCount(1)
  // Nothing went into the library.
  expect(fs.readdirSync(shelf, { recursive: true }).map(String).filter((name) => name.endsWith('.mp3'))).toEqual([])
})

test('a book uploaded from the library goes into the library like a download', async ({ page }) => {
  const { id } = await library(page, 'Hochladen', 'ebook')
  // In a short folder of its own, never below the output folder of the run: Chromium and Firefox read a file chosen in a
  // dialog themselves, and below a folder that is deep (a long checkout path plus the name of the test) the path is past
  // what Windows lets a program open without a long-path prefix. The file reads as empty then, the page says "leer", and
  // the button stays shut. WebKit gets the bytes from the test and never noticed.
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'nexbooks-e2e-upload-'))
  const file = path.join(folder, 'Kleine Probe.epub')
  epub(file, 'Kleine Probe')
  expect(fs.statSync(file).size).toBeGreaterThan(0)
  await page.goto('/')
  const dialog = page.getByRole('dialog', { name: 'Bücher hochladen' })
  // The page may still be settling when the button is first pressed (Firefox): press again until the dialog is there.
  await expect(async () => {
    await page.locator('[data-testid="library-upload"]').click()
    await expect(dialog).toBeVisible({ timeout: 3000 })
  }).toPass({ timeout: 30_000 })
  await expect(dialog.locator('[data-testid="upload-drop"]')).toContainText('Dateien hierher ziehen')
  await dialog.locator('[data-testid="upload-input"]').setInputFiles(file)
  await expect(dialog.locator('[data-testid="upload-files"]')).toContainText('Kleine Probe.epub')
  await expect(dialog.locator('[data-testid="upload-files"] li.bad')).toHaveCount(0)
  await dialog.locator('.opt', { hasText: `Hochladen ${salt}` }).locator('input').check()
  await shot(page, 'app-upload-pick')
  await dialog.locator('[data-act="upload"]').click()
  await expect(dialog.locator('[data-testid="upload-result"]')).toContainText('Das Buch liegt jetzt im Regal.', { timeout: 90_000 })
  await shot(page, 'app-upload-done')
  await dialog.locator('[data-act="open-book"]').click()
  await expect(page.locator('h1')).toContainText('Kleine Probe')
  const shown = (await (await page.request.get('/api/v1/procure/history')).json()) as { entries: { result: string; note: { library?: string } }[] }
  expect(shown.entries[0].result).toBe('done')
  expect(shown.entries[0].note.library).toBe(`Hochladen ${salt}`)
  expect(id).toBeGreaterThan(0)
  fs.rmSync(folder, { recursive: true, force: true })
})

test('the upload fits a phone without making the page wider', async ({ page }) => {
  await library(page, 'Telefon', 'ebook')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const dialog = page.getByRole('dialog', { name: 'Bücher hochladen' })
  // The page may still be settling when the button is first pressed (Firefox): press again until the dialog is there.
  await expect(async () => {
    await page.locator('[data-testid="library-upload"]').click()
    await expect(dialog).toBeVisible({ timeout: 3000 })
  }).toPass({ timeout: 30_000 })
  await expect(dialog).toBeVisible()
  await shot(page, 'app-upload-390')
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(wide).toBeLessThanOrEqual(0)
  const box = await dialog.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
})
