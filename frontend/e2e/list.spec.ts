/**
 * The list of the library in a real browser, headless, with ninety invented books (`e2e/list-books.json`): long and short
 * titles, long names, series and up to four editions. Only the rows in sight are drawn; with an automatic table layout the
 * columns were measured afresh for every set of rows, so they jumped while scrolling, and a table grown past its frame
 * was cut off at the right. What a unit test cannot see: the widths of the columns while scrolling, and that the table
 * stays inside its frame at every width.
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-list0000' }
const BOOKS = 90

let library = 0

async function ensureLibrary(page: Page): Promise<void> {
  if (!library) {
    const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; name: string }[]
    library = mine.find((item) => item.name === 'Liste')?.id ?? 0
  }
  if (!library) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Liste', path: path.join(process.env.E2E_BOOKS!, 'list'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
    library = ((await made.json()) as { id: number }).id
  }
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { total: number }).total, { timeout: 120_000, intervals: [1000] })
    .toBe(BOOKS)
}

// The shelf and other tests count the books of the whole instance: the ninety go again.
test.afterAll(async ({ browser }) => {
  if (!library) return
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  await context.request.delete(`/api/v1/libraries/${library}`, { headers: TAB })
  await context.close()
  library = 0
})

async function list(page: Page): Promise<void> {
  await ensureLibrary(page)
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'list' } })
  await page.goto('/')
  // Right after the ninety are read in, the page may still be putting the first page of books together.
  await expect(page.locator('.list-table tbody tr[data-work]').first()).toBeVisible({ timeout: 30_000 })
  await page.evaluate(() => document.fonts.ready)
}

/** The widths of the columns that are shown, in the order of the head. */
function widths(page: Page): Promise<number[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('.list-table thead th')]
      .filter((th) => getComputedStyle(th).display !== 'none')
      .map((th) => Math.round(th.getBoundingClientRect().width)),
  )
}

/** How far the table and any cell reach past what holds them, in pixels (0 when everything fits). */
function overflow(page: Page): Promise<{ table: number; cells: string[] }> {
  return page.evaluate(() => {
    const wrap = document.querySelector('[data-testid="list"]') as HTMLElement
    const table = wrap.querySelector('table') as HTMLElement
    const cells = [...table.querySelectorAll('tbody tr[data-work] > *')]
      .filter((cell) => getComputedStyle(cell).display !== 'none')
      .filter((cell) => cell.getBoundingClientRect().right > wrap.getBoundingClientRect().right + 1)
      .map((cell) => cell.className)
    return { table: Math.max(0, table.getBoundingClientRect().width - wrap.clientWidth), cells }
  })
}

/** How much of its frame the table leaves empty at the right, and whether the title column has room for words. */
function fill(page: Page): Promise<{ gap: number; titleRoom: boolean }> {
  return page.evaluate(() => {
    const wrap = document.querySelector('[data-testid="list"]') as HTMLElement
    const table = wrap.querySelector('table') as HTMLElement
    const title = table.querySelector('thead th.col-title') as HTMLElement
    const phone = window.innerWidth <= 820
    return { gap: Math.max(0, Math.round(wrap.clientWidth - table.getBoundingClientRect().width)), titleRoom: title.getBoundingClientRect().width >= (phone ? 150 : 240) }
  })
}

test.describe('the list', () => {
  // The first test waits for the ninety books to be read in.
  test.describe.configure({ mode: 'serial', timeout: 180_000 })
  for (const width of [1920, 1600, 1440, 1280, 1024, 900, 390]) {
    test(`keeps its columns while scrolling and stays inside its frame at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await list(page)
      const first = await widths(page)
      expect(first.length).toBeGreaterThanOrEqual(3)
      // The table fills its frame, and the title has room for its words: no empty strip at the right, no title of one
      // letter per line (columns made by a spanning spacer row took the room once).
      expect(await fill(page)).toEqual({ gap: 0, titleRoom: true })
      expect(await overflow(page)).toEqual({ table: 0, cells: [] })
      // Down the whole list in steps of a screen: other rows, other titles, other chips; the columns stay as they were.
      const height = await page.evaluate(() => document.documentElement.scrollHeight)
      for (let top = 600; top < height; top += 600) {
        await page.evaluate((y) => window.scrollTo(0, y), top)
        await page.waitForTimeout(80)
        expect(await widths(page), `at ${top} px`).toEqual(first)
        expect(await overflow(page), `at ${top} px`).toEqual({ table: 0, cells: [] })
        expect(await fill(page), `at ${top} px`).toEqual({ gap: 0, titleRoom: true })
      }
    })
  }

  // A hit of the search did nothing while the library was shown as a list. The page looked for the
  // row of the book, and the list draws only the rows in sight; a book far down had no row to be found.
  test('opens a hit of the search whose row is far down the list, not drawn yet', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await list(page)
    await page.evaluate(() => window.scrollTo(0, 0))
    const row = page.locator('.list-table tbody tr[data-work]', { hasText: 'Glut 87' })
    await expect(row).toHaveCount(0)
    await page.fill('#q', 'glut 87')
    const hit = page.locator('.results .res', { hasText: 'Glut 87' }).first()
    await expect(hit).toBeVisible()
    await hit.click()
    await expect(page.locator('.detail.show')).toBeVisible({ timeout: 8000 })
    await expect(page.locator('.d-title')).toHaveText('Glut 87')
    // Under the book the list stands at the row of the book.
    await expect(row).toHaveCount(1)
    await shots(page, 'liste-suchtreffer')
  })
})
