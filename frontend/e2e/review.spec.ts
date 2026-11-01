/**
 * The review list over every library a person looks after: the note on the library page
 * counts the books of all libraries, and "Prüfen" opens one list of all of them, each book with its library; a library
 * can be chosen above. The answers about the libraries and the entries are made up by the test (the sources are off
 * here, so the real matching would leave nothing to look at); the page and its way through the browser are the real
 * ones.
 */
import { expect, test, type Page } from '@playwright/test'

test.use({ serviceWorkers: 'block' })

function library(id: number, name: string, review: number) {
  return {
    id, name, kind: 'both', path: `/books/${name}`, my_right: 'manage', state: 'idle', scan_enabled: true, scanned_at: null, report: null,
    counts: { ebooks: 3, audiobooks: 0, files: 3, bytes: 1000, problems: 0, review, entries: 3, pending: 0 },
    progress: { done: 0, total: 0 }, matching: 'idle', resting: [],
  }
}

function entry(id: number, libraryId: number, title: string) {
  return {
    id, edition_id: id, work_id: id, library_id: libraryId, state: 'maybe', reason: 'last_name_only', reason_text: '', kind: 'ebook', title,
    subtitle: '', authors: ['Mara Lindqvist'], language: 'de', year: null, series: '', series_pos: null, file: `Lindqvist/${title}.epub`, cover_url: null,
    gap: null, proposals: [],
  }
}

const ENTRIES = [entry(1, 1, 'Der stille Obstgarten'), entry(2, 1, 'Die Uhrmacherin'), entry(3, 2, 'Glas und Asche')]

async function fake(page: Page): Promise<void> {
  await page.route('**/api/v1/libraries', (route) => route.fulfill({ json: [library(1, 'Bücher', 2), library(2, 'Hörbücher', 1)] }))
  await page.route('**/api/v1/review?*', (route) => route.fulfill({ json: { total: ENTRIES.length, items: ENTRIES } }))
  await page.route(/\/api\/v1\/libraries\/(\d+)\/review/, (route) => {
    const id = Number(/libraries\/(\d+)\/review/.exec(route.request().url())![1])
    const items = ENTRIES.filter((item) => item.library_id === id)
    return route.fulfill({ json: { total: items.length, items } })
  })
}

test.describe('the review list', () => {
  test('lists the books of every library from the note on, each with its library, and narrows to one', async ({ page }) => {
    await fake(page)
    await page.goto('/')
    const hint = page.locator('[data-testid="review-hint"]')
    await expect(hint).toContainText('3 books need your look')
    await hint.click()
    await expect(page).toHaveURL(/\/review$/)
    const cards = page.locator('[data-testid="review-item"]')
    await expect(cards).toHaveCount(3)
    await expect(page.locator('[data-testid="review-library"]')).toHaveText(['Bücher', 'Bücher', 'Hörbücher'])
    const all = page.locator('.seg button[data-library="all"]')
    await expect(all).toHaveAttribute('aria-pressed', 'true')
    await expect(all).toContainText('3')
    await page.locator('.seg button', { hasText: 'Hörbücher' }).click()
    await expect(cards).toHaveCount(1)
    await expect(cards.first()).toContainText('Glas und Asche')
    await expect(page.locator('[data-testid="review-library"]')).toHaveCount(0)
    await all.click()
    await expect(cards).toHaveCount(3)
  })
})
