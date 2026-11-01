/**
 * The line and the bar of a library being read (library page), in a real browser: the bar is as wide as the share of the files
 * read, measured on the screen and not read from the style. The server is asked as ever; only the answer about the libraries
 * is made to say that a big library is being read (4,753 of 20,592 files, the numbers of a measurement on a real library).
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-scanning' }

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
}

async function showReading(page: Page, done: number, total: number): Promise<void> {
  await ensureLibrary(page)
  await page.route('**/api/v1/libraries', async (route) => {
    const response = await route.fetch()
    const libraries = (await response.json()) as Record<string, unknown>[]
    await route.fulfill({ response, json: libraries.map((library) => ({ ...library, state: 'running', progress: { done, total } })) })
  })
  await page.goto('/')
  await expect(page.locator('[data-testid="scan-row"]').first()).toBeVisible()
}

async function widths(page: Page): Promise<{ track: number; fill: number }> {
  return page.evaluate(() => {
    const bar = document.querySelector<HTMLElement>('.scan-bar')!
    const fill = bar.querySelector<HTMLElement>('i')!
    return { track: bar.getBoundingClientRect().width, fill: fill.getBoundingClientRect().width }
  })
}

// The answer about the libraries is made up by the test: no service worker may answer in its place.
test.use({ serviceWorkers: 'block' })

test.describe('the library being read', () => {
  for (const [name, size] of [['on a desktop', { width: 1280, height: 800 }], ['on a phone', { width: 390, height: 844 }]] as const) {
    test(`says which library is read, and the bar is as wide as the share of the files read ${name}`, async ({ page }) => {
      await page.setViewportSize(size)
      await showReading(page, 4753, 20592)
      await expect(page.locator('.scan-text').first()).toHaveText('Reading library “Regal” · 4,753 of 20,592 files')
      // The change of the width takes a moment (a transition): measured when it is over.
      await expect.poll(async () => {
        const { track, fill } = await widths(page)
        return Math.round((fill / track) * 100)
      }, { timeout: 5000 }).toBe(23)
    })
  }

  test('the bar is empty at the start, half at half and full at the end', async ({ page }) => {
    for (const [done, share] of [[0, 0], [10, 50], [20, 100]] as const) {
      await showReading(page, done, 20)
      await expect.poll(async () => {
        const { track, fill } = await widths(page)
        return Math.round((fill / track) * 100)
      }, { timeout: 5000 }).toBe(share)
      await page.unroute('**/api/v1/libraries')
    }
  })
})
