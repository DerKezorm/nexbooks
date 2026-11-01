/**
 * The matching with the sources made visible, in a real browser against the real backend with invented books: the line
 * under the library's numbers ("140 of 202 books", the bar, the source that rests), the hint that quietly says how many books
 * are still being matched, and the quiet note on the card of a recording whose reading nothing proves, with the choice for
 * the person who looks after the library. Only the answer about the libraries is made up (a real library of a few books
 * would be matched in a moment, and the sources are off here), the rest is the server's own.
 */
import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-matching' }
const SHOTS = process.env.E2E_SHOTS ?? ''

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBe(6)
}

async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, name) })
}

// The answer about the libraries is made up by the test: no service worker may answer in its place.
test.use({ serviceWorkers: 'block' })

test.describe('the matching with the sources', () => {
  async function showMatching(page: Page, until: string): Promise<void> {
    await ensureLibrary(page)
    await page.route('**/api/v1/libraries', async (route) => {
      const response = await route.fetch()
      const libraries = (await response.json()) as { counts: Record<string, number> }[]
      await route.fulfill({
        response,
        json: libraries.map((library) => ({
          ...library,
          matching: 'running',
          counts: { ...library.counts, review: 7, entries: 202, pending: 62 },
          resting: [{ source: 'dnb', until }],
        })),
      })
    })
    await page.goto('/')
    await expect(page.locator('[data-testid="matching-row"]')).toBeVisible()
  }

  for (const [name, size] of [['on a desktop', { width: 1280, height: 800 }], ['on a phone', { width: 390, height: 844 }]] as const) {
    test(`shows how far the books are looked at, the source that rests and the hint ${name}`, async ({ page }) => {
      await page.setViewportSize(size)
      await showMatching(page, '2026-10-14T22:00:00Z')
      await expect(page.locator('[data-testid="matching-row"] .scan-text')).toContainText('Matching with the sources · 140 of 202 books')
      await expect(page.locator('[data-testid="matching-rest"]')).toContainText('DNB paused until')
      await expect(page.locator('[data-testid="review-hint"]')).toContainText('7 books need your look · 62 are still being matched')
      await expect.poll(async () => {
        return page.evaluate(() => {
          const bar = document.querySelector<HTMLElement>('[data-testid="matching-row"] .scan-bar')!
          return Math.round((bar.querySelector<HTMLElement>('i')!.getBoundingClientRect().width / bar.getBoundingClientRect().width) * 100)
        })
      }, { timeout: 5000 }).toBe(69)
      await page.evaluate(() => document.fonts.ready)
      await shot(page, `matching-${size.width}.png`)
    })
  }
})

test.describe('a recording whose reading nothing proves', () => {
  let listening = 0

  test.afterAll(async ({ browser }) => {
    // The recordings of this test leave with it: the shelf spec counts the works it sees.
    if (!listening) return
    const context = await browser.newContext({ storageState: process.env.E2E_STATE })
    await context.request.delete(`/api/v1/libraries/${listening}`, { headers: TAB })
    await context.close()
  })

  test('says so quietly on its card, and the person who looks after the library sets the reading', async ({ page }) => {
    // The recordings of the player tests (no word in their names says which reading they are).
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Listening', path: path.join(process.env.E2E_BOOKS!, 'listening'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
    listening = ((await made.json()) as { id: number }).id
    // The folder holds eight invented books (backend/tools/make_audio_books.py: the harbour with its e-book beside it,
    // seven bridges, the lantern keeper, night radio, exact parts, long chapters, a broken part and a missing part), and
    // eight is what a scan run to its end makes of them. The count is read when the scan is over (and no matching with
    // the sources runs; here the sources are off, so the books stay as they are), not when it first reaches a number: a
    // half-read folder shows other numbers on its way.
    await expect
      .poll(async () => {
        const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; state: string; matching: string }[]
        const library = mine.find((item) => item.id === listening)
        return library ? `${library.state}/${library.matching === 'running' || library.matching === 'queued' ? 'matching' : 'quiet'}` : 'none'
      }, { timeout: 25_000, intervals: [500] })
      .toBe('idle/quiet')
    const settled = (await (await page.request.get(`/api/v1/works?library=${listening}&per_page=100`)).json()) as { total: number }
    expect(settled.total).toBe(8)
    const works = (await (await page.request.get(`/api/v1/works?library=${listening}&per_page=100`)).json()) as { items: { id: number; editions: { kind: string; variant_uncertain: boolean }[] }[] }
    const withAudio = works.items.find((work) => work.editions.some((edition) => edition.kind === 'audio' && edition.variant_uncertain))
    expect(withAudio, 'a recording whose reading is not proven').toBeTruthy()
    await page.goto(`/work/${withAudio!.id}`)
    const note = page.locator('[data-testid="variant-unproven"]')
    await expect(note).toContainText('Reading not proven')
    await expect(page.locator('[data-testid="variant-pick"]')).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    // The page fades in; the picture is taken when it stands, with the cards in view.
    await expect(page.locator('section.detail.show')).toBeVisible()
    await page.locator('.twins').scrollIntoViewIfNeeded()
    await page.waitForTimeout(900)
    await shot(page, 'unproven.png')
    await page.locator('[data-testid="variant-pick"]').selectOption('abridged')
    await expect(page.locator('[data-testid="work-status"]')).toHaveText('Set.')
    await expect(page.locator('[data-testid="variant-unproven"]')).toContainText('Set by hand')
    await expect(page.locator('.twin[data-kind="audio"] h4')).toContainText('abridged')
    await page.locator('[data-testid="variant-takeback"]').click()
    await expect(page.locator('[data-testid="variant-pick"]')).toBeVisible()
  })
})
