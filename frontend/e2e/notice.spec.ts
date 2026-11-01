/**
 * The banner "nexbooks 0.1.0 is here" in a real browser, measured on the screen: it stands in the column of the view, its left
 * edge under the left edge of the title and its right edge where the content of the view ends, above the title, on a desktop
 * and on a phone, on every page that has a view. (A first look found it stuck to the edge of the window and not as wide as the
 * column: the box was bounded by a width of its own, not by the column.) The answer about the account is made to say that the
 * version was not read yet; the server is asked as ever.
 */
import { expect, test, type Page } from '@playwright/test'

// The answer about the account is made up by the test: no service worker may answer in its place.
test.use({ serviceWorkers: 'block' })

async function unread(page: Page): Promise<void> {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ response, json: { ...(await response.json()), whats_new_seen: '' } })
  })
}

type Boxes = { banner: DOMRect; title: DOMRect; view: DOMRect; content: { left: number; right: number }; page: { scrollWidth: number; clientWidth: number } }

async function measure(page: Page): Promise<Boxes> {
  return page.evaluate(() => {
    const rect = (element: Element) => element.getBoundingClientRect().toJSON()
    const banner = document.querySelector('[data-testid="whats-new-banner"]')!
    const view = document.querySelector('.view')!
    const style = getComputedStyle(view)
    const box = rect(view)
    return {
      banner: rect(banner),
      title: rect(view.querySelector('h1')!),
      view: box,
      content: { left: box.left + parseFloat(style.paddingLeft), right: box.right - parseFloat(style.paddingRight) },
      page: { scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth },
    } as Boxes
  })
}

const SIZES = [
  ['a desktop', { width: 1440, height: 900 }],
  ['a laptop', { width: 1024, height: 768 }],
  ['a phone', { width: 390, height: 844 }],
] as const

test.describe('the banner of the new version', () => {
  for (const [name, size] of SIZES) {
    for (const route of ['/', '/discover', '/stack', '/settings']) {
      test(`stands in the column of the view on ${route} on ${name}`, async ({ page }) => {
        await page.setViewportSize(size)
        await unread(page)
        await page.goto(route)
        await expect(page.locator('[data-testid="whats-new-banner"]')).toBeVisible()
        await expect(page.locator('.view h1').first()).toBeVisible()
        const { banner, title, content, page: widths } = await measure(page)
        // Left edge under the left edge of the title; right edge where the content of the view ends (1 pixel for rounding).
        expect(Math.abs(banner.left - title.left)).toBeLessThanOrEqual(1)
        expect(Math.abs(banner.right - content.right)).toBeLessThanOrEqual(1)
        // Above the title, not over it, and not stuck to the head of the app.
        expect(banner.bottom).toBeLessThanOrEqual(title.top)
        expect(banner.top).toBeGreaterThan(40)
        expect(widths.scrollWidth).toBeLessThanOrEqual(widths.clientWidth)
      })
    }
  }

  test('keeps the same column when a note is shown below the title (the review line of a library)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await unread(page)
    await page.goto('/')
    await expect(page.locator('[data-testid="whats-new-banner"]')).toBeVisible()
    const banner = await page.locator('[data-testid="whats-new-banner"]').boundingBox()
    const lead = await page.locator('.view .lead').first().boundingBox()
    expect(Math.abs(banner!.x - lead!.x)).toBeLessThanOrEqual(1)
  })

  test('opens the text and goes away when it is put away', async ({ page }) => {
    await unread(page)
    await page.goto('/')
    await page.locator('[data-testid="whats-new-banner"] button', { hasText: "What's new?" }).click()
    await expect(page.locator('[data-testid="whats-new-window"]')).toBeVisible()
    await page.locator('[data-testid="whats-new-window"] button', { hasText: 'Got it' }).first().click()
    await expect(page.locator('[data-testid="whats-new-banner"]')).toHaveCount(0)
  })
})
