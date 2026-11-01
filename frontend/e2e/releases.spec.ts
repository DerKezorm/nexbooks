/**
 * "Releases ansehen" in a real browser, headless, against the real backend and the stand-ins of
 * `e2e/fake-procure.mjs` (an indexer and a SABnzbd on 127.0.0.1): the search runs as a job of its own queue, the dialog
 * follows each indexer, judges the releases (a recording that fits, an abridged one, the e-book there is already, one in
 * French), loads the best one into SABnzbd's category, asks before it loads one that was turned down, and folds its table on
 * a phone without making the page wider.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-releases' }
const FAR = process.env.E2E_PROCURE!

type Keys = { indexer: string; sab: string }
let keys: Keys

test.describe.configure({ mode: 'serial' })

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

/** What the stand-in adds to the release names in this browser (see `/__reset` of fake-procure.mjs). */
let salt = ''

test.beforeEach(async ({ page }, testInfo) => {
  await ensureLibrary(page)
  // The person reads German: the releases of the stand-in are German.
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  const indexer = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 0, language: 'de' } })
  expect(indexer.ok(), await indexer.text()).toBe(true)
  const program = await page.request.post('/api/v1/procure/clients', { headers: TAB, data: { kind: 'sabnzbd', url: `${FAR}/sab`, username: '', secret: keys.sab, category: 'nexbooks' } })
  expect(program.ok(), await program.text()).toBe(true)
  salt = testInfo.project.name.toUpperCase()
  await page.request.get(`${FAR}/__reset?salt=${testInfo.project.name}`)
})

/** A book loads one release of a kind at a time, and a search that finished a few minutes ago is shown again: the tests
 * start from nothing, so what the app wrote down about the last one goes (there is no way in the app to clear the
 * downloads at once). */
function forgetSearchesAndDownloads(): void {
  const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('downloads', 'release_searches', 'blocklist'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python, ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

test.afterEach(async ({ page }) => {
  forgetSearchesAndDownloads()
  const get = async (where: string) => (await page.request.get(where)).json()
  for (const item of ((await get('/api/v1/procure/indexers')) as { indexers: { id: number }[] }).indexers) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
  for (const item of ((await get('/api/v1/procure/clients')) as { clients: { id: number }[] }).clients) await page.request.delete(`/api/v1/procure/clients/${item.id}`, { headers: TAB })
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
})

async function openBook(page: Page): Promise<void> {
  const works = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
  const work = works.items.find((item) => item.title.startsWith('Schwarzer'))
  expect(work).toBeTruthy()
  await page.goto(`/work/${work!.id}`)
  await expect(page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]')).toBeVisible()
}

const dialog = (page: Page) => page.locator('.rel-dlg')

test('a search follows each indexer, judges the releases and loads the best into SABnzbd', async ({ page }) => {
  await openBook(page)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).getByRole('heading', { name: 'Releases ansehen' })).toBeVisible()
  await expect(dialog(page).locator('[data-testid="indexer-row"]')).toHaveCount(1)
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  await expect(dialog(page).locator('[data-testid="indexer-row"]')).toContainText('4 Treffer')
  const cards = dialog(page).locator('[data-testid="release-card"]')
  await expect(cards).toHaveCount(2)
  // The recording is the kind asked for: its card is open first.
  await expect(cards.nth(1)).toHaveAttribute('aria-pressed', 'true')
  await expect(cards.nth(1)).toContainText('würde nehmen')
  await expect(cards.nth(1)).toContainText(`Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE${salt}`)
  await expect(cards.nth(0)).toContainText('nichts passt')
  const rows = dialog(page).locator('[data-testid="release-row"]')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toHaveClass(/best/)
  await expect(rows.nth(1)).toContainText('gekürzt')
  await cards.nth(0).click()
  await expect(rows).toHaveCount(2)
  await expect(dialog(page)).toContainText('nicht besser als vorhanden')
  await expect(dialog(page)).toContainText('andere Sprache')
  await cards.nth(1).click()
  await rows.nth(0).click()
  await dialog(page).getByRole('button', { name: 'Dieses Release laden' }).click()
  await expect(dialog(page)).toHaveCount(0)
  await expect(page.locator('[data-testid="work-status"]')).toContainText(`Lädt jetzt: Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE${salt}`)
  const hits = (await (await page.request.get(`${FAR}/__hits`)).json()) as string[]
  expect(hits).toContain('GET /indexer/get/1')
  expect(hits).toContain('POST /sab/api')
})

test('a release that was turned down loads only after the question, and nothing goes out before it', async ({ page }) => {
  await openBook(page)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  const rows = dialog(page).locator('[data-testid="release-row"]')
  await rows.nth(1).click()
  await expect(dialog(page).locator('[data-testid="why-row"] li')).toHaveText('Gekürzte Fassung, die Einstellungen nehmen hier keine gekürzten.')
  await dialog(page).getByRole('button', { name: 'Trotzdem laden' }).click()
  const question = page.getByRole('dialog', { name: 'Trotzdem laden?' })
  await expect(question).toBeVisible()
  await question.getByRole('button', { name: 'Abbrechen' }).click()
  let hits = (await (await page.request.get(`${FAR}/__hits`)).json()) as string[]
  expect(hits.filter((hit) => hit.startsWith('GET /indexer/get'))).toEqual([])
  await dialog(page).getByRole('button', { name: 'Trotzdem laden' }).click()
  await page.getByRole('dialog', { name: 'Trotzdem laden?' }).getByRole('button', { name: 'Trotzdem laden' }).click()
  await expect(dialog(page)).toHaveCount(0)
  hits = (await (await page.request.get(`${FAR}/__hits`)).json()) as string[]
  expect(hits).toContain('GET /indexer/get/2')
})

test('on a phone the table folds its columns into the name and the page is no wider than the screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openBook(page)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  const first = dialog(page).locator('[data-testid="release-row"]').first()
  await expect(first.locator('td.hide-s').first()).toBeHidden()
  await expect(first.locator('small.hide-l')).toBeVisible()
  await expect(first.locator('small.hide-l')).toContainText('Bücherei')
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(wide).toBeLessThanOrEqual(0)
})

test('the search that finished is shown again within minutes, and "Neu suchen" asks the indexers once more', async ({ page }) => {
  await openBook(page)
  const open = page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]')
  await open.click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  const asked = async () => ((await (await page.request.get(`${FAR}/__hits`)).json()) as string[]).filter((hit) => hit.startsWith('GET /indexer/api')).length
  const before = await asked()
  expect(before).toBeGreaterThan(0)
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
  // Opened again: the same search, with the time it was made, and not one request more.
  await open.click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Gesucht um', { timeout: 30_000 })
  await expect(dialog(page).locator('[data-testid="release-row"]')).toHaveCount(2)
  expect(await asked()).toBe(before)
  await dialog(page).getByRole('button', { name: 'Neu suchen' }).click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  await expect(dialog(page).locator('[data-testid="release-row"]')).toHaveCount(2)
  expect(await asked()).toBeGreaterThan(before)
})

test('a second release of a book and a kind is refused while the first is on its way', async ({ page }) => {
  await openBook(page)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  const rows = dialog(page).locator('[data-testid="release-row"]')
  await rows.nth(0).click()
  await dialog(page).getByRole('button', { name: 'Dieses Release laden' }).click()
  await expect(dialog(page)).toHaveCount(0)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  await rows.nth(1).click()
  await dialog(page).getByRole('button', { name: 'Trotzdem laden' }).click()
  await page.getByRole('dialog', { name: 'Trotzdem laden?' }).getByRole('button', { name: 'Trotzdem laden' }).click()
  await expect(dialog(page).locator('[data-testid="release-note"]')).toContainText('schon ein Release unterwegs')
})

test('the table of the releases fits its box on a wide screen and its chips break into lines', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openBook(page)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
  await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
  const fit = await dialog(page).locator('.rtab-w').evaluate((box) => ({ scroll: box.scrollWidth, client: box.clientWidth }))
  expect(fit.scroll).toBeLessThanOrEqual(fit.client)
  // Every chip lies inside the box of its cell.
  const outside = await dialog(page).locator('.rtab .rchip').evaluateAll((chips) =>
    chips.filter((chip) => {
      const cell = chip.closest('td')!.getBoundingClientRect()
      const box = chip.getBoundingClientRect()
      return box.right > cell.right + 1
    }).length,
  )
  expect(outside).toBe(0)
})

// The answers these tests make up must reach the page: no service worker may answer in their place.
test.describe('with answers made up by the test', () => {
  test.use({ serviceWorkers: 'block' })

  test('a doubt is a chip of its own: never loaded by itself, never turned down, and the row says why', async ({ page }) => {
    // The stand-in's names fit or are turned down; the doubt is made up in the answer of the search, the rest is the server's.
    await page.route(/\/api\/v1\/procure\/searches\/\d+$/, async (route) => {
      const response = await route.fetch()
      const found = (await response.json()) as { releases: { reasons: { code: string }[]; rank: number | null }[] }
      const doubtful = found.releases.find((release) => release.reasons.some((reason) => reason.code === 'abridged'))
      if (doubtful) {
        doubtful.reasons = [{ code: 'maybe_other_book' }]
        doubtful.rank = null
      }
      await route.fulfill({ response, json: found })
    })
    await openBook(page)
    await page.locator('.twin.missing[data-kind="audio"] [data-testid="releases"]').click()
    await expect(dialog(page).locator('.isr-h')).toContainText('Suche beendet', { timeout: 30_000 })
    const chip = dialog(page).locator('.rtab .rchip[data-doubt]')
    await expect(chip).toHaveCount(1)
    await expect(chip).toHaveText('vielleicht anderes Buch')
    await expect(chip).toHaveClass(/warn/)
    // It has no place among those nexbooks would load by itself.
    const row = dialog(page).locator('[data-testid="release-row"]').filter({ has: page.locator('.rchip[data-doubt]') })
    await expect(row.locator('.c-rank')).toHaveText('·')
    await chip.click()
    await expect(dialog(page).locator('[data-testid="why-row"]')).toContainText('nicht zum gesuchten Buch passt')
  })

  test('with two libraries for the kind and none chosen by the operator, the person names one before anything is asked for', async ({ page }) => {
    // A test before this one may have wished the book: the wish goes, so that it can be asked for here.
    const listed = (await (await page.request.get('/api/v1/procure/wishes')).json()) as { wishes: { id: number }[] }
    for (const item of listed.wishes) await page.request.delete(`/api/v1/procure/wishes/${item.id}`, { headers: TAB })
    const sent: string[] = []
    await page.route(/\/api\/v1\/procure\/book-options/, async (route) => {
      const response = await route.fetch()
      const options = (await response.json()) as { kinds: { kind: string; state: string; wish_id: number | null; libraries: { id: number; name: string; writable: boolean; write_problem: string }[]; default_library: number | null; choose?: boolean }[] }
      for (const entry of options.kinds) {
        const first = entry.libraries[0]!
        entry.libraries = [first, { ...first, id: first.id + 1000, name: 'Zweites Regal' }]
        entry.default_library = null
        entry.choose = true
      }
      await route.fulfill({ response, json: options })
    })
    await page.route(/\/api\/v1\/procure\/wishes$/, async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      sent.push(route.request().postData() ?? '')
      await route.fulfill({ status: 422, json: { detail: { code: 'invalid_input', message: 'test' } } })
    })
    await openBook(page)
    await page.locator('.twin.missing[data-kind="audio"] [data-testid="acquire"]').click()
    const asked = page.getByRole('dialog', { name: 'Beschaffen' })
    await expect(asked.locator('[data-testid="acquire-choose"]')).toBeVisible()
    await expect(asked.locator('[data-testid="acquire-go"]')).toBeDisabled()
    expect(sent).toEqual([])
    await asked.locator('#acq-lib').selectOption({ label: 'Zweites Regal' })
    await expect(asked.locator('[data-testid="acquire-choose"]')).toHaveCount(0)
    await asked.locator('[data-testid="acquire-go"]').click()
    await expect.poll(() => sent.length).toBe(1)
    expect(JSON.parse(sent[0]!).library_id).toBeGreaterThan(1000)
  })

  test('"Vorschläge übernehmen" on the card of an indexer sends one request and says what it did', async ({ page }) => {
    const sent: string[] = []
    await page.route(/\/api\/v1\/procure\/indexers$/, async (route) => {
      const response = await route.fetch()
      const listed = (await response.json()) as { indexers: { id: number; proposals?: number }[] }
      if (sent.length === 0) for (const item of listed.indexers) item.proposals = 2
      await route.fulfill({ response, json: listed })
    })
    await page.route(/\/api\/v1\/procure\/indexers\/\d+\/proposals$/, async (route) => {
      sent.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`)
      await route.fulfill({ response: await route.fetch() })
    })
    await page.goto('/settings?tab=procure')
    const card = page.locator('[data-testid^="indexer-proposals-"]')
    await expect(card).toContainText('Bei 2 Kategorien weicht die Sprache von der Angabe des Indexers ab.')
    await card.getByRole('button', { name: 'Vorschläge übernehmen' }).click()
    await expect(page.getByText('Die Sprachen der Kategorien folgen jetzt den Angaben des Indexers.')).toBeVisible()
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatch(/^POST \/api\/v1\/procure\/indexers\/\d+\/proposals$/)
    await expect(card).toHaveCount(0)
  })
})
