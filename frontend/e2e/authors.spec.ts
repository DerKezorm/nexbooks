/**
 * The authors in a real browser, headless, against the real backend: the invented books of the shelf
 * (`e2e/shelf-books.json`) and invented catalogues of two authors (`e2e/catalog-seed.json`, written into the data folder by
 * `backend/tools/seed_catalog.py`): nothing asks a source. What the unit tests cannot see: that the search names the author
 * of the shelf before the books and leads to the page, that the page holds the sections, the filters and the marks against
 * the real server (the books of the shelf are "im Regal", the others are not), that "Alle fehlenden beschaffen" opens the
 * dialog without asking for anything until it is answered, that moving a work holds after a reload and goes back, that the
 * page of a book leads to its author, and that the page keeps to the width of a phone.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-authors' }
const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

type Tokens = Record<string, { token: string; works: Record<string, string> }>

async function shelfLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; path?: string; name: string }[]
  const found = mine.find((library) => library.name === 'Regal')
  if (!found) {
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
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const out = path.join(process.env.E2E_DATA_DIR!, 'catalog-tokens.json')
  if (!fs.existsSync(out)) {
    execFileSync(python, ['tools/seed_catalog.py', '--spec', path.resolve('e2e', 'catalog-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--out', out], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  }
  return JSON.parse(fs.readFileSync(out, 'utf-8')) as Tokens
}

async function lindholm(page: Page): Promise<number> {
  const found = (await (await page.request.get('/api/v1/authors', { params: { q: 'Lindholm' } })).json()) as { items: { id: number }[] }
  return found.items[0]!.id
}

const section = (page: Page, id: string) => page.locator(`[data-section="${id}"]`)

test.describe.configure({ mode: 'serial' })

test('the search names the author of the shelf before the books and leads to the page', async ({ page }) => {
  await seeded(page)
  await page.goto('/')
  await page.locator('#q').fill('lindh')
  const list = page.getByTestId('results')
  await expect(list.locator('.res-head').first()).toHaveText('Authors')
  const hit = list.getByTestId('author-hit').first()
  await expect(hit).toContainText('Jonas Lindholm')
  await expect(hit).toContainText('10 works · 2 of them on the shelf')
  await page.locator('#q').press('Enter')
  await expect(page).toHaveURL(new RegExp(`/person/${await lindholm(page)}$`))
  await expect(page.getByTestId('author-hero')).toContainText('Jonas Lindholm')
})

test('the page of an author of the shelf: the sections, the language, the marks, "im Regal" from the shelf only', async ({ page }) => {
  await seeded(page)
  await page.goto(`/person/${await lindholm(page)}`)
  // The person reads English: the two works that came out in English, and the way to all of them.
  await expect(page.getByTestId('hidden-language')).toContainText('8 books do not exist in English.')
  await page.getByRole('button', { name: 'Show all languages' }).click()
  const series = section(page, 's:Die Deich-Krimis')
  await expect(series.locator('h2')).toHaveText('Series · Die Deich-Krimis 4 · 2 on the shelf')
  await expect(series.getByTestId('work')).toHaveCount(4)
  await expect(series.locator('[data-key="nebel"] .tbadge')).toHaveText('On shelf')
  await expect(series.locator('[data-key="watt"] .tbadge')).toHaveCount(0)
  await expect(series.locator('[data-key="watt"] [data-mark="audio:avail"]')).toHaveCount(1)
  await expect(page.getByTestId('author-soon')).toContainText('Die letzte Flut')
  await expect(section(page, 'p:Jon Holm').locator('h2')).toHaveText('As Jon Holm 1')
  await expect(section(page, 'unsure').locator('h2')).toHaveText('Not placed for sure 1')
  await page.getByRole('button', { name: 'By year' }).click()
  await expect(section(page, 'd:2030')).toBeVisible()
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await expect(page.locator('.atab').first()).toBeVisible()
  await page.getByRole('button', { name: 'By kind' }).click()
  await page.getByRole('button', { name: 'Covers' }).click()
  await page.getByTestId('hide-owned').check({ force: true })
  await expect(section(page, 's:Die Deich-Krimis').getByTestId('work')).toHaveCount(2)
  await page.getByTestId('hide-owned').uncheck({ force: true })
  await shots(page, 'a1-autor-lindholm')
})

test('"Alle fehlenden beschaffen" opens the dialog of every other place and asks for nothing until it is answered', async ({ page }) => {
  await seeded(page)
  await page.goto(`/person/${await lindholm(page)}`)
  await page.getByRole('button', { name: 'Show all languages' }).click()
  const wishes: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/procure/wishes')) wishes.push(request.url())
  })
  const bulk = section(page, 's:Die Deich-Krimis').getByTestId('bulk')
  await expect(bulk).toContainText('Get all missing')
  await expect(bulk).toContainText('2')
  await bulk.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  // The dialog names the books of the section that are missing (without an indexer it says so and asks for nothing).
  await expect(dialog).toContainText('2 books')
  await expect(dialog).toContainText('Wattenstille · Die letzte Flut')
  await dialog.getByRole('button', { name: 'Close' }).last().click()
  await expect(dialog).toBeHidden()
  expect(wishes).toEqual([])
})

// The answer this test makes up must reach the page: no service worker may answer in its place.
test.describe('with an answer made up by the test', () => {
  test.use({ serviceWorkers: 'block' })

  test('a work that may be a book of the shelf says so and stays out of "Alle fehlenden beschaffen"', async ({ page }) => {
    await seeded(page)
    const id = await lindholm(page)
    const shelf = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
    const mine = shelf.items[0]!
    // The server marks a work so only with a book on the shelf at the same place of its series: the answer is made up here.
    await page.route(`**/api/v1/people/${id}/catalog`, async (route) => {
      const response = await route.fetch()
      const catalog = (await response.json()) as { works: { editions: { title: string }[]; work: unknown; maybe_have?: unknown }[] }
      const missing = catalog.works.find((work) => !work.work && work.editions.some((edition) => edition.title === 'Die letzte Flut'))
      expect(missing).toBeTruthy()
      missing!.maybe_have = mine
      await route.fulfill({ response, json: catalog })
    })
    await page.goto(`/person/${id}`)
    await page.getByRole('button', { name: 'Show all languages' }).click()
    const hint = section(page, 's:Die Deich-Krimis').getByTestId('maybe-have')
    await expect(hint).toHaveCount(1)
    await expect(hint).toHaveText(`Perhaps on your shelf already: ${mine.title}`)
    // Two were missing; the one that may be on the shelf is not asked for.
    await expect(section(page, 's:Die Deich-Krimis').getByTestId('bulk')).toContainText('1')
    await section(page, 's:Die Deich-Krimis').getByTestId('bulk').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('Wattenstille')
    await expect(dialog).not.toContainText('Die letzte Flut')
    await dialog.getByRole('button', { name: 'Close' }).last().click()
  })
})

test('moving an unsure work holds after a reload and goes back', async ({ page }) => {
  await seeded(page)
  await page.goto(`/person/${await lindholm(page)}`)
  await page.getByRole('button', { name: 'Show all languages' }).click()
  await page.getByLabel('Move “Deich. Die Graphic Novel” to').selectOption('erz')
  await expect(page.getByTestId('author-notice')).toContainText('now stands with Story collections')
  await expect(section(page, 'k:erz').getByTestId('work')).toHaveCount(2)
  await page.reload()
  await page.getByRole('button', { name: 'Show all languages' }).click()
  await expect(section(page, 'k:erz').getByTestId('work')).toHaveCount(2)
  await expect(section(page, 'unsure')).toHaveCount(0)
  const token = (await seeded(page))['Jonas Lindholm']!.token
  const back = await page.request.delete(`/api/v1/catalog/authors/${token}/works/comic/kind`, { headers: TAB })
  expect(back.ok()).toBe(true)
  await page.reload()
  await page.getByRole('button', { name: 'Show all languages' }).click()
  await expect(section(page, 'unsure').getByTestId('work')).toHaveCount(1)
})

test('the page of a book of the sources leads to its author, whose page has nothing on the shelf', async ({ page }) => {
  const tokens = await seeded(page)
  await page.goto(`/discover/book/${tokens['Edmund Halloran']!.works['soot']}`)
  await expect(page.getByTestId('foreign')).toContainText('Soot')
  await expect(page.getByTestId('about-author')).toContainText('About Edmund Halloran')
  await expect(page.getByTestId('about-author')).toContainText('6 works · none on the shelf')
  await page.getByTestId('author-link').click()
  await expect(page).toHaveURL(/\/author\//)
  await expect(page.getByTestId('author-hero')).toContainText('Edmund Halloran')
  await expect(page.getByTestId('author-alias')).toContainText('Richard Vane')
  await expect(page.locator('[data-section="k:roman"] [data-testid="work"]')).toHaveCount(4)
  await expect(page.getByTestId('soon-work')).toContainText('The Keeper of Lost Rooms')
  await shots(page, 'a1-autor-halloran')
})

test('keeps to the width of a phone', async ({ page }) => {
  await seeded(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/person/${await lindholm(page)}`)
  await page.getByRole('button', { name: 'Show all languages' }).click()
  await expect(section(page, 's:Die Deich-Krimis')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
  await page.getByRole('button', { name: 'List', exact: true }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
})

test('the covers have one size, and the marks of a row stand on one line', async ({ page }) => {
  await seeded(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/person/${await lindholm(page)}`)
  const tiles = page.locator('[data-section]').first().locator('.atile')
  await expect(tiles).not.toHaveCount(0)
  const measured = await tiles.evaluateAll((items) =>
    items.map((item) => ({
      cover: Math.round(item.querySelector('.cover')!.getBoundingClientRect().height),
      left: Math.round(item.getBoundingClientRect().left),
      top: Math.round(item.getBoundingClientRect().top),
      marks: Math.round(item.querySelector('.fps')!.getBoundingClientRect().top),
    })),
  )
  expect(new Set(measured.map((item) => item.cover)).size).toBe(1)
  const firstRow = measured.filter((item) => item.top === measured[0]!.top)
  expect(firstRow.length).toBeGreaterThan(1)
  expect(new Set(firstRow.map((item) => item.marks)).size).toBe(1)
})

test('two authors joined by hand are one, shown under the name that stays, and a split puts them back', async ({ page }) => {
  await seeded(page)
  const mine = await lindholm(page)
  const everyone = (await (await page.request.get('/api/v1/authors', { params: { per_page: 50 } })).json()) as { items: { id: number; name: string }[] }
  const other = everyone.items.find((item) => item.id !== mine)!
  await page.goto(`/person/${mine}`)
  await page.getByTestId('merge-open').click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Name of the other author').fill(other.name.slice(0, 5))
  await dialog.getByTestId('merge-found').getByRole('button', { name: new RegExp(other.name) }).click()
  await expect(dialog).toContainText('Which name stays?')
  // Nothing is joined before the last click.
  expect(((await (await page.request.get('/api/v1/authors', { params: { per_page: 50 } })).json()) as { total: number }).total).toBe(everyone.items.length)
  await dialog.getByRole('button', { name: 'Join', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('are one author now.')
  await expect(page.getByTestId('author-hero')).toContainText('Jonas Lindholm')
  await expect(page.getByTestId('pen-names')).toContainText(other.name)
  expect(((await (await page.request.get('/api/v1/authors', { params: { per_page: 50 } })).json()) as { total: number }).total).toBe(everyone.items.length - 1)
  // The way back: the split on the same page.
  await page.getByRole('button', { name: new RegExp(`^Split “${other.name}”`) }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Split', exact: true }).click()
  await expect(page.getByTestId('pen-names')).toBeHidden()
  expect(((await (await page.request.get('/api/v1/authors', { params: { per_page: 50 } })).json()) as { total: number }).total).toBe(everyone.items.length)
})

test('pictures in German (only with E2E_SHOTS)', async ({ page }) => {
  const folder = process.env.E2E_SHOTS
  test.skip(!folder || test.info().project.name !== 'chromium', 'pictures only when asked for, in Chromium')
  const tokens = await seeded(page)
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })
  try {
    for (const [name, at] of [['lindholm', `/person/${await lindholm(page)}`], ['halloran', `/author/${tokens['Edmund Halloran']!.token}`]] as const) {
      for (const width of [1440, 390]) {
        for (const colorScheme of ['light', 'dark'] as const) {
          await page.setViewportSize({ width, height: width > 400 ? 900 : 844 })
          await page.emulateMedia({ colorScheme })
          await page.goto(at)
          await expect(page.getByTestId('author-hero')).toBeVisible()
          await page.evaluate(() => document.fonts.ready)
          await page.waitForTimeout(300)
          await page.screenshot({ path: path.join(folder!, `a1-de-${name}-${width}-${colorScheme === 'light' ? 'hell' : 'dunkel'}.png`), fullPage: true })
        }
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ colorScheme: 'light' })
    await page.goto('/')
    await page.locator('#q').fill('lindh')
    await expect(page.getByTestId('author-hit').first()).toBeVisible()
    await page.screenshot({ path: path.join(folder!, 'a1-de-suche-1440.png') })
    await page.goto(`/discover/book/${tokens['Edmund Halloran']!.works['soot']}`)
    await expect(page.getByTestId('about-author')).toBeVisible()
    await page.getByTestId('about-author').scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(folder!, 'a1-de-buchseite-1440.png') })
  } finally {
    await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
    await page.emulateMedia({ colorScheme: 'light' })
  }
})
