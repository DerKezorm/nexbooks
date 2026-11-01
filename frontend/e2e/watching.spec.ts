/**
 * Watching an author in a real browser, headless, against the real backend: the invented catalogues of two authors
 * (`e2e/catalog-seed.json`, written into the data folder by `backend/tools/seed_catalog.py`) and an indexer that is only there
 * to be switched on. What the unit tests cannot see: that the switch on the page of an author wishes the book announced in the
 * first language books are fetched in (and nothing that is out), that a book announced in another language only waits on the
 * wish list and says why, that "Gesucht" and the wish list say where a book came from, that switching off asks first and
 * sends nothing before the answer, that a person who may not fetch books gets the wish list instead, that the strip in Browse
 * names the author with the next day, and that the card keeps to the width of a phone.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-watching' }
const FAR = process.env.E2E_PROCURE!
const PASSWORD = 'e2e member password long'
const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = (): string => (fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python')

type Tokens = Record<string, { token: string; works: Record<string, string> }>

test.describe.configure({ mode: 'serial' })

async function shelfLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; name: string }[]
  if (!mine.find((library) => library.name === 'Regal')) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/authors', { params: { q: 'Lindholm' } })).json()) as { items: unknown[] }).items.length, { timeout: 90_000, intervals: [1000] })
    .toBe(1)
}

/** The shelf is read and the catalogues are in the database (once); their tokens. */
async function seeded(page: Page): Promise<Tokens> {
  await shelfLibrary(page)
  const out = path.join(process.env.E2E_DATA_DIR!, 'catalog-tokens.json')
  if (!fs.existsSync(out)) {
    execFileSync(python(), ['tools/seed_catalog.py', '--spec', path.resolve('e2e', 'catalog-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--out', out], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  }
  return JSON.parse(fs.readFileSync(out, 'utf-8')) as Tokens
}

async function lindholm(page: Page): Promise<number> {
  const found = (await (await page.request.get('/api/v1/authors', { params: { q: 'Lindholm' } })).json()) as { items: { id: number }[] }
  return found.items[0]!.id
}

/** What the tests made is taken away, so that the next browser starts from the same page. */
function forget(): void {
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('author_watch_items', 'author_watches', 'wishlist', 'downloads', 'procure_wishes', 'procure_history', 'release_searches'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python(), ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

let keys: { indexer: string }
/** The formats and languages as they were before a test changed the languages books are fetched in. */
let saved: Record<string, unknown> | null = null

async function fetchIn(page: Page, languages: string[]): Promise<void> {
  const formats = (await (await page.request.get('/api/v1/procure/formats')).json()) as Record<string, unknown>
  const keep = Object.fromEntries(Object.entries(formats).filter(([key]) => !['libraries', 'known_ebook', 'known_audio', 'known_languages', 'samples'].includes(key)))
  saved = saved ?? keep
  const put = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: { ...keep, languages, original: false } })
  expect(put.ok(), await put.text()).toBe(true)
}

test.beforeAll(async ({ playwright }) => {
  const context = await playwright.request.newContext()
  keys = (await (await context.get(`${FAR}/__keys`)).json()) as { indexer: string }
  await context.dispose()
})

test.beforeEach(async ({ page }) => {
  forget()
  await seeded(page)
  // The page in English; the language of the account plays no part in what the watch wishes.
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
  const indexer = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 0, language: 'de' } })
  expect(indexer.ok(), await indexer.text()).toBe(true)
})

test.afterEach(async ({ page }) => {
  forget()
  if (saved) {
    const back = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: saved })
    expect(back.ok(), await back.text()).toBe(true)
    saved = null
  }
  const listed = (await (await page.request.get('/api/v1/procure/indexers')).json()) as { indexers: { id: number }[] }
  for (const item of listed.indexers) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
})

const card = (page: Page) => page.getByTestId('watch-card')
// (The switch shows what the server answered: a click that has not been answered yet does not change it, so it is clicked,
// not checked.)
const switchOf = (page: Page) => page.getByTestId('watch-switch')

test('switching on wishes the book that is announced, "Gesucht" says where it came from, and switching off asks first', async ({ page }) => {
  // Books are fetched in English first, then in German; "Die letzte Flut" is announced in German only.
  await fetchIn(page, ['en', 'de'])
  await page.goto(`/person/${await lindholm(page)}`)
  await expect(card(page)).toContainText('Watch Jonas Lindholm')
  await expect(card(page)).not.toHaveClass(/\bon\b/)
  await switchOf(page).click({ force: true })
  await expect(card(page)).toHaveClass(/\bon\b/)
  // Nothing is wished in German while English comes first: the book waits on the wish list, and the page says why.
  await expect(card(page).getByTestId('watch-said')).toContainText('You watch Jonas Lindholm. 1 book is on your wish list.')
  await expect(card(page).getByTestId('watch-summary')).toContainText('is not announced in English yet, only in German; until then it is on your wish list')
  await expect(page.getByTestId('soon-work').getByTestId('soon-watch')).toHaveText('on the wish list until it is announced in English')
  await page.goto('/discover')
  await expect(page.getByTestId('wishlist').getByTestId('via-watch')).toHaveText('Through watching Jonas Lindholm · announced in German')
  // The person picks German for this author: now it is wished.
  await page.goto(`/person/${await lindholm(page)}`)
  await expect(card(page)).toHaveClass(/\bon\b/)
  await expect(page.getByTestId('watch-language').locator('option').first()).toHaveText('English (first choice)')
  await page.getByTestId('watch-language').selectOption('de')
  await expect(card(page).getByTestId('watch-said')).toContainText('1 more book is wished now.')
  await expect(card(page).getByTestId('watch-summary')).toContainText('Announced and wished: “Die letzte Flut”.')
  await shots(page, 'a2-beobachten-lindholm', '[data-testid="watch-card"]')

  // "Gesucht": the wish waits for its day and names the author.
  await page.goto('/acquire')
  const wanted = page.getByTestId('wanted-list')
  await expect(wanted).toContainText('Die letzte Flut')
  await expect(wanted).toContainText('Through watching Jonas Lindholm')
  await expect(wanted).toContainText('Waiting for the release')

  // Browse, the first tab of Discover, names the author with the day of the next book under its head.
  await page.goto('/discover')
  const strip = page.getByTestId('watch-strip')
  await expect(strip).toContainText('Jonas Lindholm')
  await expect(strip).toContainText('next book on Apr 20, 2031')

  // Switching off asks first: "Cancel" sends nothing, the default keeps the wishes.
  const stops: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'DELETE' && request.url().includes('/api/v1/watching/')) stops.push(request.url())
  })
  await page.goto(`/person/${await lindholm(page)}`)
  await expect(card(page)).toHaveClass(/\bon\b/)
  await switchOf(page).click({ force: true })
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Stop watching Jonas Lindholm?')
  await expect(dialog.getByTestId('watch-keep')).toBeChecked()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()
  expect(stops).toEqual([])
  await expect(card(page)).toHaveClass(/\bon\b/)
  await switchOf(page).click({ force: true })
  await dialog.getByTestId('watch-stop').click()
  await expect(card(page).getByTestId('watch-said')).toContainText('You no longer watch Jonas Lindholm. Wishes already made stay.')
  expect(stops).toHaveLength(1)
  expect(stops[0]).toContain('wishes=keep')
  await expect(card(page)).not.toHaveClass(/\bon\b/)
  await page.goto('/acquire')
  await expect(page.getByTestId('wanted-list')).toContainText('Through watching Jonas Lindholm')

  // Switched on again and off with the wishes taken away: nothing is left.
  await page.goto(`/person/${await lindholm(page)}`)
  await switchOf(page).click({ force: true })
  await expect(card(page)).toHaveClass(/\bon\b/)
  await switchOf(page).click({ force: true })
  await dialog.getByTestId('watch-remove').check()
  await expect(dialog).toContainText('That cannot be taken back.')
  await dialog.getByTestId('watch-stop').click()
  await expect(card(page).getByTestId('watch-said')).toContainText('The wishes that had not started are gone.')
  await page.goto('/acquire')
  await expect(page.getByTestId('wanted-empty')).toBeVisible()
})

test('a person who may not fetch books gets the wish list, with the author named', async ({ page, browser, playwright }) => {
  const tokens = await seeded(page)
  const invite = await page.request.post('/api/v1/invites', { data: { days: 1 }, headers: TAB })
  const token = new URL(((await invite.json()) as { link: string }).link).pathname.split('/').pop()!
  const outside = await playwright.request.newContext({ baseURL: test.info().project.use.baseURL })
  const name = `reader${test.info().project.name}`
  const joined = await outside.post(`/api/v1/invite/${token}`, { data: { name, password: PASSWORD }, headers: TAB })
  expect(joined.ok(), await joined.text()).toBe(true)
  await outside.dispose()
  const own = await browser.newContext({ storageState: { cookies: [], origins: [] } })
  try {
    const reader = await own.newPage()
    await reader.goto('/login')
    await reader.getByLabel('Name').fill(name)
    await reader.getByLabel('Password').fill(PASSWORD)
    await reader.getByRole('button', { name: 'Sign in', exact: true }).click()
    await reader.waitForURL((url) => !url.pathname.startsWith('/login'))
    await reader.goto(`/author/${tokens['Jonas Lindholm']!.token}`)
    await expect(card(reader)).toContainText('go onto your wish list')
    // No choice of kind: nothing is fetched for this person.
    await expect(card(reader).locator('[role="radiogroup"]')).toHaveCount(0)
    await switchOf(reader).click({ force: true })
    await expect(card(reader)).toHaveClass(/\bon\b/)
    // (German comes first of the languages books are fetched in here, and the book is announced in German.)
    await expect(card(reader).getByTestId('watch-said')).toContainText('You watch Jonas Lindholm. 1 book is on your wish list.')
    await expect(card(reader).getByTestId('watch-summary')).toContainText('Announced and put on your wish list: “Die letzte Flut”.')
    await reader.goto('/discover')
    const wishes = reader.getByTestId('wishlist')
    await expect(wishes).toContainText('Die letzte Flut')
    await expect(wishes.getByTestId('via-watch')).toHaveText('Through watching Jonas Lindholm')
    // Nobody else reads the watch: the operator's page of the same author is off.
    await page.goto(`/person/${await lindholm(page)}`)
    await expect(card(page)).not.toHaveClass(/\bon\b/)
    expect(((await (await page.request.get('/api/v1/watching')).json()) as { items: unknown[] }).items).toEqual([])
  } finally {
    await own.close()
  }
})

test('pictures in German (only with E2E_SHOTS)', async ({ page }) => {
  const folder = process.env.E2E_SHOTS
  test.skip(!folder || test.info().project.name !== 'chromium', 'pictures only when asked for, in Chromium')
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })
  try {
    const at = `/person/${await lindholm(page)}`
    await page.goto(at)
    await expect(card(page)).toBeVisible()
    // Off, then on (the book to come is wished in German, the first language books are fetched in).
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ colorScheme: 'light' })
    await page.evaluate(() => document.fonts.ready)
    await card(page).screenshot({ path: path.join(folder!, 'a2-de-beobachten-aus-1440-hell.png') })
    await switchOf(page).click({ force: true })
    await expect(card(page)).toHaveClass(/\bon\b/)
    await expect(card(page).getByTestId('watch-summary')).toContainText('Angekündigt und gewünscht')
    for (const width of [1440, 390]) {
      for (const colorScheme of ['light', 'dark'] as const) {
        await page.setViewportSize({ width, height: width > 400 ? 900 : 844 })
        await page.emulateMedia({ colorScheme })
        await page.goto(at)
        await expect(card(page)).toHaveClass(/\bon\b/)
        await page.evaluate(() => document.fonts.ready)
        await page.waitForTimeout(300)
        const name = colorScheme === 'light' ? 'hell' : 'dunkel'
        await card(page).screenshot({ path: path.join(folder!, `a2-de-beobachten-${width}-${name}.png`) })
        await page.screenshot({ path: path.join(folder!, `a2-de-autor-${width}-${name}.png`) })
        await page.goto('/discover')
        await expect(page.getByTestId('watch-strip')).toBeVisible()
        await page.screenshot({ path: path.join(folder!, `a2-de-entdecken-${width}-${name}.png`) })
        await page.goto('/acquire')
        await expect(page.getByTestId('wanted-list')).toContainText('Über „Beobachten“ von Jonas Lindholm')
        await page.screenshot({ path: path.join(folder!, `a2-de-gesucht-${width}-${name}.png`) })
      }
    }
  } finally {
    await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })
    await page.emulateMedia({ colorScheme: 'light' })
  }
})

test('keeps to the width of a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/person/${await lindholm(page)}`)
  await switchOf(page).click({ force: true })
  await expect(card(page)).toHaveClass(/\bon\b/)
  await expect(card(page).getByTestId('watch-summary')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
  await page.goto('/discover')
  await expect(page.getByTestId('watch-strip')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
})
