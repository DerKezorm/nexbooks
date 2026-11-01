/**
 * The automatic work in a real browser, headless, against the real backend and the stand-in indexer and SABnzbd
 * of `e2e/fake-procure.mjs`: a wish is searched and the settings of the automatic work say when it last ran and what it did
 * (numbers per indexer, no titles), the reading of the newest entries of the indexer is shown, the limit of the day is
 * shown with what was fetched, and Gesucht says when a wish was searched last. The pages fit a phone.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-automation' }
const FAR = process.env.E2E_PROCURE!
const SHOTS = process.env.E2E_SHOTS

type Keys = { indexer: string; sab: string }
let keys: Keys

test.describe.configure({ mode: 'serial' })
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

function forget(): void {
  const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('downloads', 'procure_wishes', 'procure_history', 'foreign_jobs', 'path_mappings', 'release_searches', 'blocklist', 'auto_runs', 'rss_seen'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python, ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

test.beforeEach(async ({ page }, testInfo) => {
  forget()
  await ensureLibrary(page)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  const indexer = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 500, language: 'de' } })
  expect(indexer.ok(), await indexer.text()).toBe(true)
  const program = await page.request.post('/api/v1/procure/clients', { headers: TAB, data: { kind: 'sabnzbd', url: `${FAR}/sab`, username: '', secret: keys.sab, category: 'nexbooks' } })
  expect(program.ok(), await program.text()).toBe(true)
  await page.request.get(`${FAR}/__reset?salt=${testInfo.project.name}`)
})

test.afterEach(async ({ page }) => {
  forget()
  const get = async (where: string) => (await page.request.get(where)).json()
  for (const item of ((await get('/api/v1/procure/indexers')) as { indexers: { id: number }[] }).indexers) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
  for (const item of ((await get('/api/v1/procure/clients')) as { clients: { id: number }[] }).clients) await page.request.delete(`/api/v1/procure/clients/${item.id}`, { headers: TAB })
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
})

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
}

async function wishAudio(page: Page): Promise<void> {
  const works = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
  const found = works.items.find((item) => item.title.startsWith('Schwarzer'))
  expect(found).toBeTruthy()
  const answer = await page.request.post('/api/v1/procure/wishes', { headers: TAB, data: { items: [{ work_id: found!.id }], kinds: ['audio'], language: 'de' } })
  expect(answer.ok(), await answer.text()).toBe(true)
}

test('the settings say what the automatic work did, with numbers and no titles, and when it comes back', async ({ page }) => {
  await wishAudio(page)
  await page.goto('/settings?tab=procure&sub=automation')
  const log = page.locator('[data-testid="auto-log"]')
  await expect(log.locator('li[data-run="search"]').first()).toBeVisible({ timeout: 60_000 })
  const line = log.locator('li[data-run="search"]').first()
  await expect(line).toContainText('Wunsch gesucht')
  await expect(line).toContainText('Bücherei')
  await expect(line).toContainText('Abruf')
  // Not a title, not the key of the indexer.
  const text = (await page.locator('#auto-log').innerText()) + (await page.locator('#auto-main').innerText())
  expect(text).not.toContain('Schwarzer')
  expect(text).not.toContain(keys.indexer)
  await expect(page.locator('#auto-main')).toContainText('Letzter Lauf')
  await expect(page.locator('#auto-main')).toContainText('der nächste')
  await expect(page.locator('[data-testid^="limit-"]').first()).toContainText('heute')
  await expect(page.locator('[data-testid^="grabs-"]').first()).toContainText('Download')
  await shot(page, 'app-auto-settings')
})

test('a wish that finds a release goes on to Läuft, and the log counts the release it loaded', async ({ page }) => {
  await wishAudio(page)
  await page.goto('/acquire?reiter=running')
  await expect(page.locator('[data-download]')).toHaveCount(1, { timeout: 60_000 })
  // The download row stands in "Läuft" as soon as it is made; the line of the log is written when the wish is settled, a
  // moment later. The page of the settings looks again only every 30 seconds, so a page opened in between is reloaded.
  const line = page.locator('[data-testid="auto-log"] li[data-run="search"]').first()
  await expect(async () => {
    await page.goto('/settings?tab=procure&sub=automation')
    await expect(line).toContainText('1 Release geladen', { timeout: 4000 })
  }).toPass({ timeout: 60_000 })
})

test('the page of the settings fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await wishAudio(page)
  await page.goto('/settings?tab=procure&sub=automation')
  await expect(page.locator('#auto-log')).toBeVisible({ timeout: 60_000 })
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  expect(wide).toBe(false)
  await shot(page, 'app-auto-settings-phone')
})
