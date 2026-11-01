/**
 * Browse in a real browser, headless, against the real backend with the invented shelves of
 * `e2e/browse-seed.json` (written into the data folder by `backend/tools/seed_browse.py`): nothing asks a source. What the unit
 * tests cannot see: that the cover lifts and its back appears beside it where it fits on the screen, that the board runs under
 * the covers, that the page keeps to one column on a phone with the back as a sheet from below, that the filters of an opened
 * shelf work in a real field, and that all of it holds in light and dark and in all three engines.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-browse' }
const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as {
    id: number
  }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', {
      headers: TAB,
      data: {
        name: 'Regal',
        path: path.join(process.env.E2E_BOOKS!, 'shelf'),
        kind: 'both',
        scan_enabled: true,
      },
    })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(
      async () =>
        (
          (await (await page.request.get('/api/v1/works/stats')).json()) as {
            works: number
          }
        ).works,
      { timeout: 90_000, intervals: [1000] },
    )
    .toBe(6)
}

/** The shelf is read and the invented shelves are in the database (once); the person's choices are as at the start. */
async function browse(page: Page, at = '/discover'): Promise<void> {
  await ensureLibrary(page)
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const marker = path.join(process.env.E2E_DATA_DIR!, 'browse-seeded')
  if (!fs.existsSync(marker)) {
    // The market follows the language of the page (English: USA, German: Germany): the shelves stand in both.
    for (const market of ['us', 'de'])
      execFileSync(python, ['tools/seed_browse.py', '--spec', path.resolve('e2e', 'browse-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--market', market], {
        cwd: path.join('..', 'backend'),
        stdio: 'ignore',
      })
    fs.writeFileSync(marker, 'x')
  } else {
    // Other specs empty the reading history (stack.spec.ts): what the person has read is written again each time.
    execFileSync(python, ['tools/seed_browse.py', '--spec', path.resolve('e2e', 'browse-seed.json'), '--data-dir', process.env.E2E_DATA_DIR!, '--market', 'us', '--history-only'], {
      cwd: path.join('..', 'backend'),
      stdio: 'ignore',
    })
  }
  await page.request.put('/api/v1/me/preferences', {
    headers: TAB,
    data: { discover_hide_owned: true, market: '', language: '' },
  })
  const list = (await (await page.request.get('/api/v1/wishlist')).json()) as {
    items: { token: string }[]
  }
  for (const item of list.items)
    await page.request.delete(`/api/v1/wishlist/${item.token}`, {
      headers: TAB,
    })
  await page.goto(at)
  await expect(page.locator('.bcard').first()).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}

const strip = (page: Page, shelf: string) => page.locator(`[data-testid="strip"][data-shelf="${shelf}"]`)
const noOverflow = (page: Page, width: number) => page.evaluate(() => document.documentElement.scrollWidth).then((value) => expect(value).toBeLessThanOrEqual(width))

test.describe('the wall', () => {
  test('is the first tab and shows thirteen tiles in three departments, with the strips of the shelves below', async ({ page }) => {
    await browse(page)
    await expect(page.locator('#dlists button')).toHaveText(['Browse', 'Best sellers', 'New releases', 'Coming soon', 'Trending now', 'For you'])
    await expect(page.locator('#dlists button.on')).toHaveText('Browse')
    await expect(page.locator('[data-testid="tile"]')).toHaveCount(13)
    await expect(page.locator('.tw-dept h3')).toHaveText(['Fiction', 'Non-fiction', 'Children and young adults'])
    await expect(page.locator('[data-testid="tile"][data-shelf="krimi"] small')).toHaveText('47 books · 8 new')
    await expect(page.locator('[data-testid="tile"][data-shelf="krimi"] .sh-c')).toHaveCount(3)
    await expect(page.locator('[data-testid="dept"]')).toHaveCount(3)
    await expect(page.locator('.spine')).toHaveCount(0)
    await expect(page.locator('[data-testid="source"]')).toContainText('Apple Books Charts and Open Library')
    await shots(page, 'stoebern-wand')
  })

  test('puts the covers on a board: the board runs under the covers and the strip rolls sideways', async ({ page }) => {
    await browse(page)
    const first = strip(page, 'krimi')
    await expect(first.locator('.subtabs button')).toHaveText(['New', 'Popular now', 'Prize winners', 'Classics'])
    await expect(first.locator('.subtabs button.on')).toHaveText('New')
    const facts = await first.locator('.brow').evaluate((row) => {
      const board = getComputedStyle(row.querySelector('.bcv')!, '::after')
      return {
        scrolls: row.scrollWidth > row.clientWidth,
        overflow: getComputedStyle(row).overflowX,
        board: board.height,
        content: board.content,
        cards: row.querySelectorAll('.bcard').length,
      }
    })
    expect(facts).toEqual({
      scrolls: true,
      overflow: 'auto',
      board: '12px',
      content: '""',
      cards: 8,
    })
    const widths = await first.locator('.bcard').evaluateAll((items) => items.slice(0, 4).map((item) => Math.round(item.getBoundingClientRect().width)))
    expect(new Set(widths).size).toBe(1)
    expect(widths[0]).toBe(148)
  })

  test('reads another section from the server when its tab is pressed', async ({ page }) => {
    await browse(page)
    const first = strip(page, 'krimi')
    const before = await first.locator('.bcard b').allTextContents()
    await first.locator('.subtabs button', { hasText: 'Classics' }).click()
    await expect(first.locator('.subtabs button.on')).toHaveText('Classics')
    await expect(first.locator('.bcard')).toHaveCount(12)
    expect(await first.locator('.bcard b').allTextContents()).not.toEqual(before)
    await first.locator('.subtabs button', { hasText: 'Prize winners' }).click()
    await expect(first.locator('.tribbon')).toHaveCount(5)
    await expect(first.locator('.bmeta').first()).toHaveText('Edgar Award')
  })

  test('makes shelves for the person from what they read, and nothing for what they have not', async ({ page }) => {
    await browse(page)
    await expect(page.locator('[data-testid="personal-because"] h3')).toContainText('Because you read “Nebel ueber dem Deich”')
    await expect(page.locator('[data-testid="personal-like"] h3')).toContainText('More like your favourite books')
    await expect(page.locator('[data-testid="personal-short"] .bmeta').first()).toContainText('pages')
    await expect(page.locator('[data-testid="personal-long"] .bmeta').first()).toContainText('h ')
    await expect(page.locator('[data-testid="personal"] .dsec-h p')).toHaveText('From your reading stack, worked out on this server. None of it leaves it.')
  })
})

test.describe('taking a cover in the hand', () => {
  test('lifts the cover after 0.3 seconds and shows its back beside it, on the screen, in the colours of the book', async ({ page }) => {
    await browse(page)
    const card = strip(page, 'krimi').locator('.bcard').nth(1)
    await card.scrollIntoViewIfNeeded()
    const hand = page.locator('[data-testid="hand"]')
    // A slow machine may let the page settle (and let go of the hand) after the first hover: hover again until it is held.
    type Box = { x: number; y: number; width: number; height: number }
    const got: { h: Box | null; c: Box | null; view: { w: number; h: number } } = { h: null, c: null, view: { w: 0, h: 0 } }
    await expect(async () => {
      await card.hover()
      await expect(hand).toBeVisible({ timeout: 4000 })
      await expect(card).toHaveClass(/held/)
      const lifted = await card.locator('.cover').evaluate((el) => getComputedStyle(el).transform)
      expect(lifted).not.toBe('none')
      const [box, cover, size] = await Promise.all([hand.boundingBox(), card.locator('.bcv').boundingBox(), page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }))])
      expect(box).not.toBeNull()
      got.h = box
      got.c = cover
      got.view = size
    }).toPass({ timeout: 20000 })
    const { h, c, view } = got
    expect(h!.width).toBeGreaterThan(300)
    expect(h!.width).toBeLessThanOrEqual(340.5)
    // Beside the cover (right of it, or left when there is no room), never over it, and inside the window.
    expect(h!.x >= c!.x + c!.width - 1 || h!.x + h!.width <= c!.x + 1).toBe(true)
    expect(h!.x).toBeGreaterThanOrEqual(11)
    expect(h!.x + h!.width).toBeLessThanOrEqual(view.w - 11)
    expect(h!.y).toBeGreaterThanOrEqual(0)
    expect(h!.y + h!.height).toBeLessThanOrEqual(view.h)
    await expect(hand.locator('.h-t')).toHaveText(await card.locator('b').innerText())
    await expect(hand.locator('.h-by button')).toBeVisible()
    await expect(hand.locator('.h-act button')).toHaveText(['Acquire', 'Save', 'View'])
    expect(await hand.evaluate((el) => getComputedStyle(el).getPropertyValue('--bc').trim())).toMatch(/^#[0-9a-f]{6}$/i)
    await shots(page, 'stoebern-hand')
  })

  test('lets go when the mouse leaves, and on Escape', async ({ page }) => {
    await browse(page)
    const card = strip(page, 'krimi').locator('.bcard').first()
    await card.hover()
    await expect(page.locator('[data-testid="hand"]')).toBeVisible({
      timeout: 4000,
    })
    await page.mouse.move(5, 5)
    await expect(page.locator('[data-testid="hand"]')).toHaveCount(0, {
      timeout: 3000,
    })
    await card.hover()
    await expect(page.locator('[data-testid="hand"]')).toBeVisible({
      timeout: 4000,
    })
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="hand"]')).toHaveCount(0)
  })

  test('opens the page of the book on a click, and puts a book on the wish list from its back', async ({ page }) => {
    await browse(page)
    const card = strip(page, 'krimi').locator('.bcard').nth(2)
    const title = await card.locator('b').innerText()
    await card.hover()
    await expect(page.locator('[data-testid="hand"]')).toBeVisible({
      timeout: 4000,
    })
    await page.locator('[data-testid="hand"] [data-wish]').click()
    await expect(page.locator('[data-testid="hand"] [data-wish]')).toHaveText('Saved')
    await page.mouse.move(5, 5)
    await expect(page.locator('[data-testid="hand"]')).toHaveCount(0, {
      timeout: 3000,
    })
    await expect(card.locator('.bbadge')).toHaveText('Saved')
    await card.click()
    await expect(page.locator('.d-title')).toHaveText(title)
    expect(page.url()).toMatch(/\/discover\/book\/[\w-]+$/)
  })

  test('is reached from the keys: the card is a button, Enter opens the page', async ({ page }) => {
    await browse(page)
    const card = strip(page, 'krimi').locator('.bcard').first()
    const title = await card.locator('b').innerText()
    await card.focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('.d-title')).toHaveText(title)
  })

  test('turns the cover over with the space bar, as a sheet that keeps the focus, and Escape puts it back', async ({ page }) => {
    await browse(page)
    const card = strip(page, 'krimi').locator('.bcard').first()
    await card.focus()
    await page.keyboard.press(' ')
    const hand = page.getByTestId('hand')
    await expect(hand).toBeVisible()
    await expect(hand).toHaveAttribute('role', 'dialog')
    expect(await hand.evaluate((node) => node.contains(document.activeElement))).toBe(true)
    await expect(page).toHaveURL(/\/discover$/)
    await page.keyboard.press('Escape')
    await expect(hand).toHaveCount(0)
  })
})

test.describe('one shelf opened', () => {
  test('has the filters of the design and keeps them in the address', async ({ page }) => {
    await browse(page, '/discover/shelf/bio')
    await expect(page.locator('h2.shp-t')).toHaveText('Biographies')
    await expect(page.locator('.eyebrow')).toHaveText('Non-fiction')
    await expect(page.locator('.dgenres button')).toHaveText(['All topics', 'Music', 'Politics', 'Science', 'Art', 'Sport', 'Adventure'].map((name) => new RegExp(`^${name}$`)))
    const before = await page.locator('.bgrid .bcard').count()
    await page.locator('[data-filter="section"][data-value="award"]').click()
    await expect(page).toHaveURL(/section=award/)
    await expect(page.locator('.bgrid .bcard')).toHaveCount(6)
    await expect(page.locator('.bgrid .bmeta').first()).toHaveText(/Pulitzer Prize|National Book Award/)
    await page.locator('[data-filter="section"][data-value=""]').click()
    await expect(page.locator('.bgrid .bcard')).toHaveCount(before)
    await page.locator('[data-filter="years"][data-value="old"]').click()
    await expect(page).toHaveURL(/years=old/)
    await expect(page.locator('.bgrid .bcard')).not.toHaveCount(before)
    expect(await page.locator('.bgrid .bcard').count()).toBeLessThan(before)
    await page.reload()
    await expect(page.locator('[data-filter="years"][data-value="old"]')).toHaveClass(/on/)
    await shots(page, 'stoebern-regal')
  })

  test('shows a page at a time and the rest on "Show more"', async ({ page }) => {
    await browse(page, '/discover/shelf/romane')
    await expect(page.locator('.bgrid .bcard')).toHaveCount(60)
    await page.locator('[data-testid="more"]').click()
    await expect(page.locator('.bgrid .bcard')).toHaveCount(70)
    await expect(page.locator('[data-testid="more"]')).toHaveCount(0)
  })

  test('opens from the tile of the wall and from the name of its strip, and goes back with the link', async ({ page }) => {
    await browse(page)
    await page.locator('[data-testid="tile"][data-shelf="natur"]').click()
    await expect(page.locator('h2.shp-t')).toHaveText('Nature & Science')
    await page.locator('.back-link').click()
    await expect(page.locator('[data-testid="tile"]')).toHaveCount(13)
    await strip(page, 'jugend').locator('.sec-link').click()
    await expect(page.locator('h2.shp-t')).toHaveText('Young Adult Books')
  })

  test('says that a shelf of that name does not exist', async ({ page }) => {
    await browse(page, '/discover')
    await page.goto('/discover/shelf/nosuchshelf')
    await expect(page.locator('[data-testid="unknown-shelf"]')).toHaveText('There is no such shelf.')
  })
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('keeps the page in one column, rolls the tiles sideways, and shows the back of a book as a sheet at the first tap', async ({ page }) => {
    await page.addInitScript(() => {
      const real = window.matchMedia.bind(window)
      window.matchMedia = (query: string) => (query.includes('hover: none') ? ({ ...real(query), matches: true, media: query } as MediaQueryList) : real(query))
    })
    await browse(page)
    await noOverflow(page, 390)
    const tiles = await page
      .locator('.tw-grid')
      .first()
      .evaluate((el) => ({
        scrolls: el.scrollWidth > el.clientWidth,
        overflow: getComputedStyle(el).overflowX,
      }))
    expect(tiles).toEqual({ scrolls: true, overflow: 'auto' })
    const widths = await strip(page, 'krimi')
      .locator('.bcard')
      .evaluateAll((items) => items.slice(0, 3).map((item) => Math.round(item.getBoundingClientRect().width)))
    expect(widths).toEqual([112, 112, 112])
    await expect(page.locator('.browse-intro')).toContainText('Tap a cover')
    const card = strip(page, 'krimi').locator('.bcard').nth(1)
    await card.scrollIntoViewIfNeeded()
    await card.tap()
    const sheet = page.locator('[data-testid="hand"].sheet')
    await expect(sheet).toBeVisible()
    await expect(sheet).toHaveAttribute('role', 'dialog')
    const box = await sheet.boundingBox()
    // The whole width of the window (a scrollbar of the page, when there is one, is not part of it), at the bottom.
    const size = await page.evaluate(() => ({
      w: document.documentElement.clientWidth,
      h: window.innerHeight,
    }))
    expect(box!.x).toBe(0)
    expect(Math.round(box!.width)).toBe(size.w)
    // The sheet slides up by 30 pixels when it opens: measure the bottom when it has come to rest.
    await expect
      .poll(async () => {
        const rest = await sheet.boundingBox()
        return Math.round(rest!.y + rest!.height)
      })
      .toBe(size.h)
    expect(box!.height).toBeLessThanOrEqual(844 * 0.82 + 1)
    await expect(page.locator('.h-cv .cover')).toBeVisible()
    await shots(page, 'stoebern-blatt')
    await page.locator('[data-testid="hand-close"]').tap()
    await expect(sheet).toHaveCount(0)
    await card.tap()
    await page.locator('[data-testid="hand"] .h-act button', { hasText: 'View' }).tap()
    await expect(page.locator('.d-title')).toBeVisible()
  })

  test('keeps an opened shelf in one column', async ({ page }) => {
    await browse(page, '/discover/shelf/krimi')
    await noOverflow(page, 390)
    const widths = await page.locator('.bgrid .bcard').evaluateAll((items) => items.slice(0, 3).map((item) => Math.round(item.getBoundingClientRect().width)))
    expect(Math.min(...widths)).toBeGreaterThanOrEqual(100)
    await shots(page, 'stoebern-regal-handy')
  })
})

test.describe('light and dark', () => {
  for (const scheme of ['light', 'dark'] as const) {
    test(`draw the back of a book and the board in ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
      await page.addInitScript((value) => localStorage.setItem('nexbooks.theme', value), scheme)
      await browse(page)
      const card = strip(page, 'krimi').locator('.bcard').first()
      await card.hover()
      const hand = page.locator('[data-testid="hand"]')
      await expect(hand).toBeVisible({ timeout: 4000 })
      // The brightness of the text and of the page as the screen shows it (the browser turns any colour to red, green, blue).
      const look = await hand.evaluate((el) => {
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = 1
        const context = canvas.getContext('2d', { willReadFrequently: true })!
        const lum = (css: string) => {
          context.clearRect(0, 0, 1, 1)
          context.fillStyle = css
          context.fillRect(0, 0, 1, 1)
          const [r, g, b] = context.getImageData(0, 0, 1, 1).data
          return (0.2126 * r! + 0.7152 * g! + 0.0722 * b!) / 255
        }
        return {
          text: lum(getComputedStyle(el.querySelector('.h-t')!).color),
          page: lum(getComputedStyle(el).backgroundColor),
        }
      })
      // Text is darker than the page by day and lighter by night.
      expect(look.text < look.page).toBe(scheme === 'light')
      const board = await strip(page, 'krimi')
        .locator('.bcv')
        .first()
        .evaluate((el) => getComputedStyle(el, '::after').backgroundImage)
      expect(board).toContain('linear-gradient')
    })
  }
})

test.describe('in German', () => {
  test('has its words', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('nexbooks.language', 'de'))
    await browse(page)
    await page.request.put('/api/v1/me/language', {
      headers: TAB,
      data: { language: 'de' },
    })
    await page.reload()
    await expect(page.locator('.tw-dept h3')).toHaveText(['Belletristik', 'Sachbuch', 'Kinder und Jugend'])
    await expect(page.locator('[data-testid="tile"][data-shelf="krimi"] b')).toHaveText('Krimi & Thriller')
    await page.request.put('/api/v1/me/language', {
      headers: TAB,
      data: { language: 'en' },
    })
  })
})
