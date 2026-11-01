/**
 * Discover in a real browser, headless, against the real backend with the six invented books of the shelf
 * (`e2e/shelf-books.json`) and invented lists (`e2e/discover-seed.json`, written into the data folder by
 * `backend/tools/seed_discover.py`): nothing asks a source. What the unit tests cannot see: that the tabs and filters work
 * in a real field, that the wish list and its "Rückgängig" survive a reload, that the page of a book opens over the page it
 * came from and closes with Escape and the way back, that the ghosts of a series stand on the page of a book, and that the
 * phone keeps the page in one column with the tabs rolling sideways.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-discover' }
const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

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

/** The shelf is read and the invented lists are in the database (once); the person's choices are as at the start. */
async function discover(page: Page, at = '/discover?list=best'): Promise<void> {
  await ensureLibrary(page)
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const marker = path.join(process.env.E2E_DATA_DIR!, 'discover-seeded')
  if (!fs.existsSync(marker)) {
    execFileSync(python, ['tools/seed_discover.py', '--spec', path.resolve('e2e', 'discover-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--market', 'us'], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
    fs.writeFileSync(marker, 'x')
  }
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { discover_hide_owned: true, market: '', language: '' } })
  const list = (await (await page.request.get('/api/v1/wishlist')).json()) as { items: { token: string }[] }
  for (const item of list.items) await page.request.delete(`/api/v1/wishlist/${item.token}`, { headers: TAB })
  await page.goto(at)
  await expect(page.locator('.dgrid .dcard').first()).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}

/** A card of the list. Only the list's: once a book is on the wish list the same card stands in the section below it as well,
 * and a locator that matched both would break at "strict mode" the moment the wish list is drawn. */
const card = (page: Page, title: string) => page.getByTestId('grid').locator('.dcard', { has: page.locator('b', { hasText: new RegExp(`^${title}$`) }) })

test.describe('the lists', () => {
  test('stand as covers with the tabs, the market, the filters and the numbers of the design', async ({ page }) => {
    await discover(page)
    await expect(page.locator('#dlists button')).toHaveText(['Browse', 'Best sellers', 'New releases', 'Coming soon', 'Trending now', 'For you'])
    await expect(page.locator('#market button')).toHaveText(['Germany', 'USA', 'UK', 'France'])
    await expect(page.locator('.spine')).toHaveCount(0)
    // The two books the person has (the first and the fifth are on the shelf) are hidden from the start.
    await expect(page.locator('.dsec-h p')).toContainText('hidden because they are on your shelf already')
    await expect(page.locator('.dgrid .rk')).toHaveText(['2', '3', '4'])
    // The first three places are in the accent colour, the others in the text colour.
    const colours = await page.locator('.dgrid .rk').evaluateAll((items) => items.map((item) => getComputedStyle(item).backgroundColor))
    expect(colours[0]).toBe(colours[1])
    expect(colours[0]).toBe(colours[2])
    await expect(page.locator('[data-testid="source"]')).toContainText('Source: Apple Books Charts and Audible')
  })

  test('send the filters and keep the switch and the language in the profile after a reload', async ({ page }) => {
    await discover(page)
    await page.locator('#hideOwned').evaluate((el: HTMLInputElement) => el.click())
    await expect(page.locator('.dgrid .dcard')).toHaveCount(6)
    await expect(page.locator('.dsec-h p')).toContainText('of them on your shelf')
    await page.reload()
    await expect(page.locator('.dgrid .dcard').first()).toBeVisible()
    await expect(page.locator('#hideOwned')).not.toBeChecked()
    await page.locator('#dfmt [data-f="audio"]').click()
    await expect(page).toHaveURL(/format=audio/)
    await expect(page.locator('[data-testid="source"]')).toContainText('Source: Audible')
    await expect(page.locator('.dgrid .rk')).toHaveText(['1', '2', '3'])
    await page.locator('#dlang [data-lang="en"]').click()
    await expect(page.locator('.dgrid .dcard')).toHaveCount(1)
    await page.locator('#dgenres [data-dg="krimi"]').click()
    await expect(page.locator('[data-testid="nothing"]')).toBeVisible()
  })

  test('print the day on new and on coming books, made from the shelves of Browse while Audible is off', async ({ page }) => {
    await discover(page)
    // Audible is off from the start: the new and the coming books are those of the themed shelves (e2e/browse-seed.json).
    const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
    const marker = path.join(process.env.E2E_DATA_DIR!, 'browse-seeded-us')
    if (!fs.existsSync(marker)) {
      execFileSync(python, ['tools/seed_browse.py', '--spec', path.resolve('e2e', 'browse-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--market', 'us'], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
      fs.writeFileSync(marker, 'x')
    }
    await page.locator('#dlists [data-l="new"]').click()
    await expect(page.locator('.dgrid .when').first()).toContainText('out')
    await expect(page.locator('.dgrid .rk')).toHaveCount(0)
    await expect(page.locator('[data-testid="source"]')).toContainText('Apple Books Charts')
    await page.locator('#dlists [data-l="soon"]').click()
    await expect(page.locator('.dgrid .when')).toHaveCount(1)
    await expect(page.locator('.dgrid .dcard b')).toHaveText(['Die kommende Flut'])
    await expect(page).toHaveURL(/list=soon/)
  })
})

test.describe('the wish list', () => {
  test('marks a book, counts it on the tab, keeps it over a reload, and takes it off with "Undo"', async ({ page }) => {
    await discover(page)
    const salt = card(page, 'Die Salzwiese')
    await salt.locator('button.badge').click()
    await expect(salt.locator('button.badge')).toHaveText('Saved')
    await expect(page.locator('.wishsec h2')).toContainText('Your wish list')
    await page.reload()
    await expect(page.locator('.wishsec .dcard')).toHaveCount(1)
    await page.locator('.wishsec .dcard button.badge').click()
    await expect(page.locator('.wishsec')).toHaveCount(0)
    await expect(page.locator('[data-testid="wish-undo"]')).toContainText('is off the wish list')
    await page.getByRole('button', { name: 'Undo' }).click()
    await expect(page.locator('.wishsec')).toBeVisible()
  })
})

test.describe('the page of a book', () => {
  test('opens over the list at an address of its own, closes with Escape and the way back, and shows the same after a reload', async ({ page }) => {
    await discover(page)
    await page.evaluate(() => window.scrollTo(0, 300))
    const before = await page.evaluate(() => window.scrollY)
    await card(page, 'Die Salzwiese').locator('.dopen').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await expect(page.locator('.flip')).toBeHidden({ timeout: 4000 })
    expect(page.url()).toMatch(/\/discover\/book\/[\w-]+$/)
    await expect(page.locator('.d-title')).toHaveText('Die Salzwiese')
    await expect(page.locator('.twin')).toHaveCount(2)
    await expect(page.locator('.twin.missing')).toContainText('No audiobook known')
    await expect(page.locator('[data-testid="notmine"]')).toContainText('Not in your library')
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe('/discover')
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThanOrEqual(2)
    await card(page, 'Die Salzwiese').locator('.dopen').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await page.goBack()
    await expect(page.locator('.detail')).toHaveCount(0)
    await card(page, 'Die Salzwiese').locator('.dopen').click()
    await page.reload()
    await expect(page.locator('.d-title')).toHaveText('Die Salzwiese')
  })

  test('wishes a book from its page, and a book on the shelf opens as the book of the shelf', async ({ page }) => {
    await discover(page)
    await card(page, 'Orbit Null').locator('.dopen').click()
    await expect(page.locator('.d-title')).toHaveText('Orbit Null')
    await expect(page.locator('.twin')).toHaveCount(2)
    await page.locator('.want [data-wish]').click()
    await expect(page.locator('.want [data-wish]')).toHaveText('Saved')
    await page.keyboard.press('Escape')
    await expect(page.locator('.wishsec .dcard')).toHaveCount(1)
    await page.locator('#hideOwned').evaluate((el: HTMLInputElement) => el.click())
    await card(page, 'Der Leuchtturmwaerter').locator('.dopen').click()
    await expect(page.locator('.d-title')).toHaveText('Der Leuchtturmwaerter')
    expect(page.url()).toMatch(/\/work\/\d+$/)
  })

  test('is reached from the keys: the cover is a button, Enter opens it', async ({ page }) => {
    await discover(page)
    const open = card(page, 'Die Salzwiese').locator('.dopen')
    await open.focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('.d-title')).toHaveText('Die Salzwiese')
  })
})

test.describe('for you', () => {
  // Reads the shelf as it was seeded, and all browsers share one data folder: the specs of the first one change it.
  test.skip(({ browserName }) => browserName !== 'chromium', 'in the first browser only')

  test('shows the gap in the series, and the ghosts stand on the page of the book of the series', async ({ page }) => {
    await discover(page)
    await page.locator('#dlists [data-l="foryou"]').click()
    const gap = page.locator('[data-testid="gap"]')
    await expect(gap).toHaveCount(1)
    await expect(gap.locator('h3')).toHaveText('Die Deich-Krimis')
    await expect(gap.locator('.gmeta')).toContainText('2 of 3 published volumes on your shelf')
    await expect(gap.locator('.dcard')).toHaveCount(4)
    await expect(gap.locator('.dcard.missing')).toHaveCount(2)
    await expect(gap.locator('.dcard.missing.soon')).toHaveCount(1)
    await expect(page.locator('[data-testid="author"] h3')).toContainText('Mara Feldkamp')
    await expect(page.locator('[data-testid="author"] .dcard')).toHaveCount(1)
    // The page of a book of the series: the volumes it lacks stand as ghosts.
    await page.goto('/')
    await page.locator('.spine[aria-label^="Nebel"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    const ghosts = page.locator('.mini-shelf .spine.ghost')
    await expect(ghosts).toHaveCount(2)
    await expect(ghosts.nth(0).locator('.a')).toHaveText('MISSING')
    await expect(ghosts.nth(1).locator('.a')).toHaveText('SOON')
    await ghosts.nth(0).click()
    await expect(page.locator('.d-title')).toHaveText('Kalter Strom')
    expect(page.url()).toMatch(/\/discover\/book\//)
  })
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('keeps the page in one column and rolls the tabs and the genres sideways', async ({ page }) => {
    await discover(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    const rolls = await page.evaluate(() => {
      const one = (selector: string) => {
        const el = document.querySelector<HTMLElement>(selector)!
        return { scrolls: el.scrollWidth > el.clientWidth, overflow: getComputedStyle(el).overflowX, wrap: getComputedStyle(el).flexWrap }
      }
      return { tabs: one('#dlists'), genres: one('#dgenres') }
    })
    expect(rolls.tabs).toEqual({ scrolls: true, overflow: 'auto', wrap: 'nowrap' })
    expect(rolls.genres).toEqual({ scrolls: true, overflow: 'auto', wrap: 'nowrap' })
    const widths = await page.locator('.dgrid .dcard').evaluateAll((items) => items.slice(0, 6).map((item) => Math.round(item.getBoundingClientRect().width)))
    expect(Math.min(...widths)).toBeGreaterThanOrEqual(108)
    await page.locator('#dlists [data-l="foryou"]').evaluate((el: HTMLElement) => el.click())
    await expect(page.locator('[data-testid="gap"]')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })
})

test.describe('light and dark', () => {
  for (const scheme of ['light', 'dark'] as const) {
    test(`keep the accent colour of the first places in ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
      await page.addInitScript((value) => localStorage.setItem('nexbooks.theme', value), scheme)
      await discover(page)
      const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())
      const first = await page.locator('.dgrid .rk').first().evaluate((el) => getComputedStyle(el).backgroundColor)
      expect(accent).toBe(scheme === 'dark' ? '#d98591' : '#8e3b46')
      expect(first).toBe(scheme === 'dark' ? 'rgb(217, 133, 145)' : 'rgb(142, 59, 70)')
    })
  }
})
