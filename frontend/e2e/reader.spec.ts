/**
 * The reader in a real browser, headless, against the real backend with invented books
 * (`backend/tools/make_reader_books.py`): opening a book from its page, turning pages with the keys, the edges and the swipe,
 * the table of contents, bookmarks, the search in a book, the look, a place that is found again after a reload and on another
 * device, books with fixed pages, from right to left, a MOBI and a PDF, books that are copy-protected, and **a hostile book**
 * that tries everything a book must not be able to do: not one script runs, not one request leaves for another host, the
 * window of the app stays where it is.
 */
import { expect, test, type Browser, type BrowserContext, type Frame, type Page } from '@playwright/test'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-reader0' }
const CANARY = `http://127.0.0.1:${process.env.E2E_CANARY_PORT ?? 18588}`

type Book = { work: number; edition: number }
const books = new Map<string, Book>()
let library = 0

async function ensureLibrary(page: Page): Promise<void> {
  if (library) return
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Reading', path: path.join(process.env.E2E_BOOKS!, 'reading'), kind: 'ebook', scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  library = ((await made.json()) as { id: number }).id
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { total: number }).total, { timeout: 90_000, intervals: [1000] })
    .toBe(16)
  const list = (await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { items: { id: number; title: string; editions: { id: number }[] }[] }
  for (const item of list.items) books.set(item.title, { work: item.id, edition: item.editions[0].id })
}

test('opens a book at its first page when the start of the text it names stands last, after the imprint', async ({ page }) => {
  // A real book named its cover page, the last of its sections, as "the start of the text": it opened at 100 %.
  await openBook(page, 'Der spaete Anfang')
  await expect.poll(() => bodyText(page)).toContain('Titelei')
  const first = await where(page)
  expect(first.chapter).toBeLessThanOrEqual(1)
  expect(first.percent).toBeLessThanOrEqual(20)
})

/** Turns the page until the text on show has `text` in it (a chapter is many pages long). */
async function turnUntil(page: Page, text: string): Promise<void> {
  for (let turns = 0; turns < 40; turns++) {
    if ((await bodyText(page)).includes(text)) return
    await page.waitForTimeout(450)
    await page.keyboard.press('ArrowRight')
  }
  expect(await bodyText(page)).toContain(text)
}

test.describe('books written the way other programs write them', () => {
  test('shows the text of a book whose package escapes the addresses and whose archive holds plain names, and turns on', async ({ page }) => {
    await openBook(page, 'Names With Commas')
    const first = await where(page)
    expect(first.total).toBe(5)
    expect(first.percent).toBeLessThanOrEqual(20)
    await turnUntil(page, 'Part One')
    await expect.poll(async () => (await where(page)).chapter).toBeGreaterThanOrEqual(2)
    // Not "100 %" because only the first part could be found: the progress is told by all five.
    expect((await where(page)).percent).toBeLessThan(60)
    await turnUntil(page, 'Part Two')
    await expect(page.locator('[data-testid="reader-status"]')).toHaveCount(0)
  })

  test('opens a book whose pages are .xml files and whose archive has an end record that counts twice', async ({ page }) => {
    await openBook(page, 'Notes From The Pier')
    await expect(page.locator('[data-testid="reader-failed"]')).toHaveCount(0)
    await turnUntil(page, 'Pier One')
    await turnUntil(page, 'Pier Two')
    await expect.poll(async () => (await where(page)).chapter).toBeGreaterThanOrEqual(2)
  })

  test('says that part of a book is missing, and does not run to 100 % over the parts there are', async ({ page }) => {
    await openBook(page, 'Parts Gone Astray')
    await expect(page.locator('[data-testid="reader-status"]')).toContainText('Part of this book is missing')
    // The third part is the last one there is; two more are named and are not there: it starts at 40 %, not at 67 %.
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('.rd-toc', { hasText: 'Missing Three' }).click()
    await expect.poll(() => bodyText(page)).toContain('Missing Three')
    await expect.poll(async () => (await where(page)).percent).toBeLessThan(60)
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThan(25)
  })
})

test.afterAll(async ({ browser }) => {
  // The books of this spec leave with it: the shelf spec counts the works it sees.
  if (!library) return
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  await context.request.delete(`/api/v1/libraries/${library}`, { headers: TAB })
  await context.close()
})

const book = (title: string): Book => {
  const found = books.get(title)
  if (!found) throw new Error(`no book ${title}`)
  return found
}

/** The frame of the book's page (blob address), inside the frame of the reader. */
function pageFrame(page: Page): Frame | undefined {
  return page.frames().find((frame) => frame.url().startsWith('blob:'))
}

async function opened(page: Page, title?: string): Promise<void> {
  await expect(page.locator('[data-testid="reader"][data-phase="open"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-testid="reader-where"]')).not.toHaveText('', { timeout: 30_000 })
  if (title) await expect(page.locator('.rd-title b')).toHaveText(title)
}

async function openBook(page: Page, title: string, query = ''): Promise<void> {
  await ensureLibrary(page)
  await page.goto(`/read/${book(title).edition}${query}`)
  await opened(page, title)
}

/** The numbers at the bottom: "Chapter 2 of 12 · 17 %" as {chapter, total, percent}. */
async function where(page: Page): Promise<{ text: string; chapter: number; total: number; percent: number; fraction: number }> {
  // textContent, not innerText: with the bars put away the text is still there.
  const text = ((await page.locator('[data-testid="reader-where"]').textContent()) ?? '').trim()
  const found = /(\d+) of (\d+) · (\d+) %/.exec(text)
  const fraction = Number(await page.locator('[data-testid="reader"]').getAttribute('data-fraction'))
  return { text, chapter: found ? Number(found[1]) : 0, total: found ? Number(found[2]) : 0, percent: found ? Number(found[3]) : Number((/(\d+) %/.exec(text) ?? [])[1] ?? 0), fraction }
}

/** The server has heard of the place (it is saved when the reader is left; the test waits for it before it closes the browser). */
async function savedAt(page: Page, title: string, chapter: number): Promise<void> {
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/editions/${book(title).edition}/progress`)).json()) as { chapter: number } | null)?.chapter ?? 0, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(chapter)
}

/** The text of the page on show; nothing while the page is being changed (the frame of the old one is gone). */
const bodyText = async (page: Page): Promise<string> => {
  try {
    return (await pageFrame(page)?.evaluate(() => document.body.innerText)) ?? ''
  } catch {
    return ''
  }
}

test.beforeEach(async ({ page }) => {
  await ensureLibrary(page)
  // The same look for every test.
  await page.request.put('/api/v1/me/preferences', {
    headers: TAB,
    data: { reader_font: 'fraunces', reader_size: 100, reader_line: 150, reader_margin: 'normal', reader_theme: 'light', reader_justify: false, reader_flow: 'paged' },
  })
})

test.describe('opening, turning, leaving', () => {
  test('covers the whole window: no strip is kept beside it for a scrollbar', async ({ page }) => {
    await openBook(page, 'The Long Road')
    const edge = await page.evaluate(() => {
      const element = document.elementFromPoint(window.innerWidth - 3, window.innerHeight / 2)
      return { inReader: !!element?.closest('[data-testid="reader"]'), gap: window.innerWidth - document.documentElement.clientWidth }
    })
    expect(edge.gap).toBe(0)
    expect(edge.inReader).toBe(true)
  })

  test('opens from the card of the book, shows the first page and goes back to the page of the book', async ({ page }) => {
    const { work, edition } = book('The Long Road')
    await page.goto(`/work/${work}`)
    await expect(page.locator('.twin .tw-pos')).toContainText('Not started')
    await page.locator('[data-testid="read"]').click()
    await expect(page).toHaveURL(new RegExp(`/read/${edition}$`))
    await opened(page, 'The Long Road')
    // The first chapter is on the page, in the typeface of the reader, and nothing of the app's head is around it.
    await expect.poll(() => bodyText(page)).toContain('Chapter 1: The Pier')
    // Three parts of four chapters: twelve chapters, and the first page is the first of them, not the second line of the contents.
    const first = await where(page)
    expect([first.chapter, first.total]).toEqual([1, 12])
    await expect(page.locator('header.top, .app-head')).toHaveCount(0)
    const font = await pageFrame(page)!.evaluate(() => getComputedStyle(document.body).fontFamily)
    expect(font).toContain('Fraunces Reader')
    await page.locator('[data-testid="reader-back"]').click()
    await expect(page).toHaveURL(new RegExp(`/work/${work}$`))
    await expect(page.locator('.d-title')).toHaveText('The Long Road')
  })

  test('turns pages with the arrow keys and the space bar, and back', async ({ page }) => {
    await openBook(page, 'The Long Road')
    const start = (await where(page)).percent
    // The reader turns one page at a time: a key pressed before the page has come is let go, so each waits for the last.
    const press = async (key: string) => {
      await page.waitForTimeout(450)
      await page.keyboard.press(key)
    }
    await press('ArrowRight')
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThan(start)
    const one = (await where(page)).percent
    await press('Space')
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThan(one)
    const two = (await where(page)).percent
    await press('ArrowLeft')
    await expect.poll(async () => (await where(page)).percent).toBeLessThan(two)
    await press('ArrowLeft')
    await expect.poll(async () => (await where(page)).percent).toBeLessThanOrEqual(start)
  })

  test('turns by a tap at the edge of the page, and the middle shows or hides the bars', async ({ page }) => {
    await openBook(page, 'The Long Road')
    // With a mouse the bars stay while reading; the middle of the page puts them away and brings them back.
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-chrome', 'on')
    const before = (await where(page)).percent
    const box = (await page.locator('[data-testid="reader-frame"]').boundingBox())!
    await page.mouse.click(box.x + box.width - 30, box.y + box.height / 2)
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThan(before)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-chrome', 'on')
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-chrome', 'off')
    await expect(page.locator('.rd-top')).toBeHidden()
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-chrome', 'on')
    await expect(page.locator('.rd-top')).toBeVisible()
  })

  test('opens at the saved place after a reload', async ({ page, browserName }) => {
    await openBook(page, 'The Long Road')
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('.rd-toc', { hasText: 'Chapter 7' }).click()
    // The parts are not counted: chapter 7 is the seventh chapter, whatever line of the list it is.
    await expect.poll(async () => (await where(page)).chapter).toBe(7)
    // The place is saved while reading and when the page is left: reload (which leaves it).
    const saved = (await where(page)).percent
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThanOrEqual(saved)
    const place = await where(page)
    await page.reload()
    await opened(page)
    await expect.poll(async () => (await where(page)).chapter).toBe(place.chapter)
    expect(Math.abs((await where(page)).percent - place.percent)).toBeLessThanOrEqual(1)
    // The server knows the place, and the page of the book says it.
    const { work, edition } = book('The Long Road')
    const mine = (await (await page.request.get(`/api/v1/editions/${edition}/progress`)).json()) as { chapter: number; chapter_title: string; device: string }
    expect(mine.chapter).toBe(place.chapter)
    expect(mine.device).toContain({ chromium: 'Chrome', firefox: 'Firefox', webkit: 'Safari' }[browserName] ?? 'Browser')
    await page.goto(`/work/${work}`)
    await expect(page.locator('[data-testid="twin-position"]')).toContainText(`Chapter ${place.chapter}`)
    await expect(page.locator('[data-testid="read"]')).toHaveText('Continue reading')
    await expect(page.locator('.twin .bar2 i')).toHaveAttribute('style', /width/)
  })

  test('a book that is only opened saves nothing', async ({ page }) => {
    await openBook(page, 'Das alte Boot')
    await page.waitForTimeout(6000)
    const { edition } = book('Das alte Boot')
    expect(await (await page.request.get(`/api/v1/editions/${edition}/progress`)).json()).toBeNull()
  })
})

test.describe('contents, bookmarks, search', () => {
  test('the table of contents lists the chapters in their parts and leads to one', async ({ page }) => {
    await openBook(page, 'The Long Road')
    await page.locator('[data-testid="open-contents"]').click()
    const lines = page.locator('.rd-toc')
    await expect(lines).toHaveCount(15)
    await expect(lines.nth(0)).toHaveText('Part 1')
    await expect(lines.nth(1)).toHaveText('Chapter 1: The Pier')
    await lines.nth(10).click()
    await expect(page.locator('[data-testid="reader-panel"]')).toHaveCount(0)
    // "Part 3" leads to the page its first chapter starts on.
    await expect.poll(() => bodyText(page)).toContain('Chapter 9: The Pier')
    await expect(page.locator('.rd-title span')).toHaveText(/Part 3|Chapter 9/)
    // The place is marked in the list.
    await page.locator('[data-testid="open-contents"]').click()
    await expect(page.locator('.rd-toc.on')).toHaveCount(1)
  })

  test('a bookmark is set, listed, found again and deleted only after the question', async ({ page }) => {
    await openBook(page, 'The Long Road')
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('.rd-toc', { hasText: 'Chapter 5' }).click()
    await expect.poll(async () => (await where(page)).chapter).toBe(5)
    await page.locator('[data-testid="add-bookmark"]').click()
    await expect(page.locator('[data-testid="reader-status"]')).toHaveText('Bookmark set.')
    await expect(page.locator('[data-testid="add-bookmark"]')).toHaveAttribute('aria-pressed', 'true')
    // The same place twice is the one bookmark.
    await page.locator('[data-testid="add-bookmark"]').click()
    await expect(page.locator('[data-testid="reader-status"]')).toHaveText('There is already a bookmark here.')
    await expect(page.locator('[data-testid="bookmark"]')).toHaveCount(1)
    await page.locator('[data-testid="panel-close"]').click()
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('[data-testid="tab-contents"]').click()
    await page.locator('.rd-toc', { hasText: 'Chapter 1:' }).click()
    await expect.poll(async () => (await where(page)).chapter).toBe(1)
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('[data-testid="tab-bookmarks"]').click()
    await expect(page.locator('[data-testid="bookmark"]')).toContainText('Chapter 5')
    await page.locator('[data-testid="bookmark"]').click()
    await expect.poll(async () => (await where(page)).chapter).toBe(5)
    // Deleting asks first, says it cannot be brought back, and sends nothing until the one click.
    const deletes: string[] = []
    page.on('request', (request) => request.method() === 'DELETE' && deletes.push(request.url()))
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('[data-testid="tab-bookmarks"]').click()
    await page.locator('[data-testid="bookmark-delete"]').click()
    await expect(page.getByRole('dialog', { name: 'Delete this bookmark?' })).toContainText('cannot be undone')
    await page.getByRole('button', { name: 'Cancel' }).click()
    expect(deletes).toEqual([])
    await expect(page.locator('[data-testid="bookmark"]')).toHaveCount(1)
    await page.locator('[data-testid="bookmark-delete"]').click()
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(page.locator('[data-testid="bookmark"]')).toHaveCount(0)
    expect(deletes).toHaveLength(1)
    const { edition } = book('The Long Road')
    expect(await (await page.request.get(`/api/v1/editions/${edition}/bookmarks`)).json()).toEqual([])
  })

  test('the search finds words in the book, lists them by chapter and leads to a hit', async ({ page }) => {
    await openBook(page, 'The Long Road')
    await page.keyboard.press('/')
    await expect(page.locator('[data-testid="search-input"]')).toBeFocused()
    await page.locator('[data-testid="search-input"]').fill('clockmaker')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="search-summary"]')).toContainText('hits', { timeout: 30_000 })
    const hits = page.locator('[data-testid="hit"]')
    expect(await hits.count()).toBeGreaterThan(10)
    await expect(hits.first().locator('mark')).toHaveText(/clockmaker/i)
    await hits.nth(12).click()
    await expect.poll(async () => (await where(page)).percent, { timeout: 15_000 }).toBeGreaterThan(5)
    await page.locator('[data-testid="search-input"]').fill('zzzzqq')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="search-summary"]')).toHaveText('Nothing found.', { timeout: 30_000 })
  })

  test('the bar at the bottom has a stroke for every chapter and jumps when it is moved', async ({ page }) => {
    await openBook(page, 'The Long Road')
    // A stroke for every chapter but the first (it lies at the very beginning): twelve chapters, eleven strokes, no stroke for a part.
    expect(await page.locator('.rd-track .tick').count()).toBe(11)
    await page.locator('[data-testid="reader-slider"]').fill('500')
    await expect.poll(async () => (await where(page)).percent, { timeout: 15_000 }).toBeGreaterThanOrEqual(45)
    expect((await where(page)).percent).toBeLessThanOrEqual(55)
  })
})

test.describe('the look', () => {
  test('size, typeface, colours and justified text apply at once, keep the place, and are kept for the next book', async ({ page }) => {
    await openBook(page, 'The Long Road')
    await page.locator('[data-testid="open-contents"]').click()
    await page.locator('.rd-toc', { hasText: 'Chapter 6' }).click()
    await expect.poll(async () => (await where(page)).chapter).toBe(6)
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(500)
    const before = await where(page)
    await page.locator('[data-testid="open-look"]').click()
    const sizeBefore = await pageFrame(page)!.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize))
    await page.getByRole('button', { name: 'Larger' }).click()
    await page.getByRole('button', { name: 'Larger' }).click()
    await expect(page.locator('[data-testid="look-size"]')).toHaveText('120 %')
    await expect.poll(() => pageFrame(page)!.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize))).toBeGreaterThan(sizeBefore * 1.15)
    // The place stays: the same chapter, and a place close to where it was.
    await page.waitForTimeout(800)
    const after = await where(page)
    expect(after.chapter).toBe(before.chapter)
    expect(Math.abs(after.percent - before.percent)).toBeLessThanOrEqual(3)
    await page.locator('[data-testid="look-font"]').getByRole('button', { name: 'Inter' }).click()
    await expect.poll(() => pageFrame(page)!.evaluate(() => getComputedStyle(document.body).fontFamily)).toContain('Inter Reader')
    await page.locator('[data-testid="look-theme"]').getByRole('button', { name: 'Sepia' }).click()
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-page-theme', 'sepia')
    await expect.poll(() => pageFrame(page)!.evaluate(() => getComputedStyle(document.documentElement).backgroundColor)).toBe('rgb(244, 236, 216)')
    await page.locator('[data-testid="look-theme"]').getByRole('button', { name: 'Dark' }).click()
    await expect.poll(() => pageFrame(page)!.evaluate(() => getComputedStyle(document.documentElement).backgroundColor)).toBe('rgb(27, 23, 20)')
    await page.locator('[data-testid="look-justify"]').click()
    await expect.poll(() => pageFrame(page)!.evaluate(() => getComputedStyle(document.querySelector('p')!).textAlign)).toBe('justify')
    // Kept with the account: the next book looks the same, and so does the next device.
    await page.waitForTimeout(900)
    const profile = (await (await page.request.get('/api/v1/auth/me')).json()) as { profile: Record<string, unknown> }
    expect(profile.profile).toMatchObject({ reader_font: 'inter', reader_size: 120, reader_theme: 'dark', reader_justify: true })
    await openBook(page, 'Das alte Boot')
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-page-theme', 'dark')
    expect(await pageFrame(page)!.evaluate(() => getComputedStyle(document.body).fontFamily)).toContain('Inter Reader')
  })

  test('the colours of the app are followed unless a colour was chosen', async ({ browser }) => {
    const context = await browser.newContext({ storageState: process.env.E2E_STATE, colorScheme: 'dark' })
    const page = await context.newPage()
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { reader_theme: 'auto', theme: 'system' } })
    await openBook(page, 'Das alte Boot')
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-page-theme', 'dark')
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { reader_theme: 'light' } })
    await page.reload()
    await opened(page)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-page-theme', 'light')
    await context.close()
  })

  test('a continuous text scrolls instead of turning', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { reader_flow: 'scroll' } })
    await openBook(page, 'The Long Road')
    const start = await pageFrame(page)!.evaluate(() => document.scrollingElement!.scrollHeight)
    expect(start).toBeGreaterThan(1500)
    await page.mouse.wheel(0, 900)
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThanOrEqual(1)
  })
})

/** A phone: Firefox has no mobile emulation (only touch), the others have both. */
const phone = (browser: Browser, browserName: string): Promise<BrowserContext> =>
  browser.newContext({
    storageState: process.env.E2E_STATE,
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    ...(browserName === 'firefox' ? {} : { isMobile: true, deviceScaleFactor: 2 }),
  })

test.describe('on a phone', () => {
  test('turns pages by a swipe and by a tap at the edge, with the bars out of the way', async ({ browser, browserName }) => {
    const context = await phone(browser, browserName)
    const page = await context.newPage()
    await openBook(page, 'The Long Road')
    // One column, the bars fit the width.
    const bars = await page.locator('.rd-top').boundingBox()
    expect(bars!.width).toBeLessThanOrEqual(390)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    expect(overflow).toBe(true)
    const start = (await where(page)).fraction
    // A drag with a finger can be sent to the browser from outside only in Chromium (the debugging protocol); in the other two the
    // swipe is not tried, the taps at the edges are.
    const client = browserName === 'chromium' ? await context.newCDPSession(page) : null
    const swipe = async (from: number, to: number) => {
      if (!client) return
      const y = 420
      await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from, y }] })
      for (let step = 1; step <= 8; step++) {
        await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from + ((to - from) * step) / 8, y }] })
        await page.waitForTimeout(16)
      }
      await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    }
    let two = start
    if (client) {
      await swipe(330, 60)
      await expect.poll(async () => (await where(page)).fraction).toBeGreaterThan(start)
      const one = (await where(page)).fraction
      await swipe(60, 330)
      await expect.poll(async () => (await where(page)).fraction).toBeLessThan(one)
      two = (await where(page)).fraction
    }
    // A page turn takes a moment, and a tap that comes while one is running is let go (as a key is): wait for it to end.
    await page.waitForTimeout(900)
    // One tap turns the page: in Firefox and Safari the click comes in the same breath as the end of the touch, when the library
    // is about to settle on the nearest page (the reader lets that happen first).
    await page.touchscreen.tap(370, 420)
    await expect.poll(async () => (await where(page)).fraction).toBeGreaterThan(two)
    // The middle brings the bars back.
    await page.touchscreen.tap(195, 420)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-chrome', 'on')
    await context.close()
  })

  test('the look comes as a sheet from below that fits the screen', async ({ browser, browserName }) => {
    const context = await phone(browser, browserName)
    const page = await context.newPage()
    await openBook(page, 'The Long Road')
    await page.locator('[data-testid="open-look"]').tap()
    const box = (await page.locator('[data-testid="look-panel"]').boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
    expect(box.y + box.height).toBeGreaterThan(800)
    await context.close()
  })
})

test.describe('two devices', () => {
  const device = (browser: Browser): Promise<BrowserContext> => browser.newContext({ storageState: process.env.E2E_STATE, locale: 'en-US' })

  test('a device that has not read the book opens at the place of the other, without a question', async ({ browser }) => {
    const a = await device(browser)
    const first = await a.newPage()
    await openBook(first, 'The Long Road')
    await first.locator('[data-testid="open-contents"]').click()
    await first.locator('.rd-toc', { hasText: 'Chapter 10' }).click()
    await expect.poll(async () => (await where(first)).chapter).toBe(10)
    await first.keyboard.press('ArrowRight')
    await first.waitForTimeout(500)
    const there = await where(first)
    await first.locator('[data-testid="reader-back"]').click()
    await savedAt(first, 'The Long Road', there.chapter)
    await a.close()
    const b = await device(browser)
    const second = await b.newPage()
    await openBook(second, 'The Long Road')
    await expect(second.locator('[data-testid="reader-hint"]')).toHaveCount(0)
    await expect.poll(async () => (await where(second)).chapter).toBe(there.chapter)
    await b.close()
  })

  test('a device that was further back is asked, and can jump or stay', async ({ browser }) => {
    // Device B reads the first chapters (its own place), then A reads on; B opens the book again.
    const a = await device(browser)
    const b = await device(browser)
    const pageB = await b.newPage()
    await openBook(pageB, 'The Long Road')
    // B starts at the first chapter (whatever an earlier test left on the server) and turns one page: its own place.
    await pageB.locator('[data-testid="open-contents"]').click()
    await pageB.locator('.rd-toc', { hasText: 'Chapter 1:' }).click()
    await expect.poll(async () => (await where(pageB)).chapter).toBe(1)
    await pageB.waitForTimeout(500)
    await pageB.keyboard.press('ArrowRight')
    await expect.poll(async () => (await where(pageB)).percent).toBeGreaterThan(0)
    await pageB.waitForTimeout(500)
    await pageB.locator('[data-testid="reader-back"]').click()
    await savedAt(pageB, 'The Long Road', 1)
    const pageA = await a.newPage()
    await openBook(pageA, 'The Long Road')
    // A has no copy of its own: it opens where B left, and reads on to chapter 10.
    await expect.poll(async () => (await where(pageA)).chapter).toBe(1)
    await pageA.locator('[data-testid="open-contents"]').click()
    await pageA.locator('.rd-toc', { hasText: 'Chapter 10' }).click()
    await expect.poll(async () => (await where(pageA)).chapter).toBe(10)
    await pageA.keyboard.press('ArrowRight')
    await pageA.waitForTimeout(500)
    await pageA.locator('[data-testid="reader-back"]').click()
    await savedAt(pageA, 'The Long Road', 10)
    const ahead = (await (await pageA.request.get(`/api/v1/editions/${book('The Long Road').edition}/progress`)).json()) as { chapter: number; device: string; fraction: number }
    expect(ahead.chapter).toBeGreaterThanOrEqual(10)
    await a.close()
    // B opens again: its own place, and the question.
    await pageB.goto(`/read/${book('The Long Road').edition}`)
    await opened(pageB)
    const hint = pageB.locator('[data-testid="reader-hint"]')
    await expect(hint).toBeVisible()
    await expect(hint).toContainText(`You are further on ${ahead.device}: chapter ${ahead.chapter}`)
    expect((await where(pageB)).chapter).toBeLessThan(ahead.chapter)
    // Nothing of B is saved while the question is open (not even a page turned the other way round).
    await pageB.waitForTimeout(5500)
    const kept = (await (await pageB.request.get(`/api/v1/editions/${book('The Long Road').edition}/progress`)).json()) as { chapter: number }
    expect(kept.chapter).toBe(ahead.chapter)
    await pageB.locator('[data-testid="hint-jump"]').click()
    await expect.poll(async () => (await where(pageB)).chapter, { timeout: 15_000 }).toBe(ahead.chapter)
    await expect(hint).toHaveCount(0)
    await b.close()
  })

  test('staying where this device was ends the question and lets it save its own place', async ({ browser }) => {
    const { edition } = book('The Long Road')
    const a = await device(browser)
    const pageA = await a.newPage()
    await openBook(pageA, 'The Long Road')
    await pageA.locator('[data-testid="open-contents"]').click()
    await pageA.locator('.rd-toc', { hasText: 'Chapter 10' }).click()
    await pageA.keyboard.press('ArrowRight')
    await pageA.waitForTimeout(500)
    await pageA.locator('[data-testid="reader-back"]').click()
    await savedAt(pageA, 'The Long Road', 10)
    await a.close()
    const b = await device(browser)
    const pageB = await b.newPage()
    // This device has a place of its own (an older one), kept in this browser.
    await pageB.goto('/')
    // Kept per person: the key names the account.
    const me = (await (await pageB.request.get('/api/v1/auth/me')).json()) as { id: number }
    await pageB.evaluate(([user, id]) => localStorage.setItem(`nexbooks.read.${user}.${id}`, JSON.stringify({ locator: 'epubcfi(/6/2!/4)', fraction: 0.01, at: Date.now() - 3_600_000 })), [me.id, edition])
    await pageB.goto(`/read/${edition}`)
    await opened(pageB)
    await expect(pageB.locator('[data-testid="reader-hint"]')).toBeVisible()
    await pageB.locator('[data-testid="hint-stay"]').click()
    await expect(pageB.locator('[data-testid="reader-hint"]')).toHaveCount(0)
    await pageB.keyboard.press('ArrowRight')
    await expect.poll(async () => ((await (await pageB.request.get(`/api/v1/editions/${edition}/progress`)).json()) as { chapter: number }).chapter, { timeout: 15_000 }).toBeLessThan(5)
    await b.close()
  })
})

test.describe('other formats', () => {
  test('a book with fixed pages shows them as pictures and turns them', async ({ page }) => {
    await openBook(page, 'Four Pages')
    await expect(page.locator('[data-testid="reader-where"]')).toContainText('Chapter 1 of 4')
    await page.locator('[data-testid="open-look"]').click()
    await expect(page.locator('[data-testid="look-panel"]')).toContainText('This book has fixed pages')
    await expect(page.locator('[data-testid="look-font"]')).toHaveCount(0)
    await page.keyboard.press('Escape')
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => (await where(page)).chapter).toBe(2)
  })

  test('a book that reads from right to left turns the other way', async ({ page }) => {
    await openBook(page, 'الميناء')
    const start = (await where(page)).percent
    await page.keyboard.press('ArrowLeft')
    await expect.poll(async () => (await where(page)).percent).toBeGreaterThan(start)
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => (await where(page)).percent).toBeLessThanOrEqual(start)
    await expect(page.locator('.rd-track')).toHaveAttribute('dir', 'rtl')
  })

  test('vertical text opens', async ({ page }) => {
    await openBook(page, '港の朝')
    expect(await pageFrame(page)!.evaluate(() => getComputedStyle(document.documentElement).writingMode)).toBe('vertical-rl')
  })

  test('a MOBI opens and has no table of contents to show', async ({ page }) => {
    await openBook(page, 'Kite Season')
    await expect.poll(() => bodyText(page)).toContain('Chapter')
    await page.locator('[data-testid="open-contents"]').click()
    await expect(page.locator('[data-testid="toc"]')).toContainText('This book has no table of contents.')
  })

  test('a PDF opens, turns, has its outline and finds words', async ({ page }) => {
    await openBook(page, 'field-notes')
    await expect(page.locator('[data-testid="reader-where"]')).toContainText('Page 1 of 8')
    await page.keyboard.press('ArrowRight')
    await expect(page.locator('[data-testid="reader-where"]')).toContainText('Page 2 of 8')
    await page.locator('[data-testid="open-contents"]').click()
    await expect(page.locator('.rd-toc')).toHaveText(['Start', 'Middle', 'End'])
    await page.locator('.rd-toc', { hasText: 'End' }).click()
    await expect(page.locator('[data-testid="reader-where"]')).toContainText('Page 8 of 8')
    await page.keyboard.press('/')
    await page.locator('[data-testid="search-input"]').fill('lantern')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="search-count"]')).toContainText(/\d+ of \d+/, { timeout: 20_000 })
    // The place of a PDF is kept like any other: "Seite", not "Kapitel".
    await page.waitForTimeout(500)
    const { edition } = book('field-notes')
    await page.locator('[data-testid="panel-close"]').click()
    await page.keyboard.press('ArrowLeft')
    await page.waitForTimeout(500)
    await page.goto(`/work/${book('field-notes').work}`)
    await expect(page.locator('[data-testid="twin-position"]')).toContainText('Page 7')
    const saved = (await (await page.request.get(`/api/v1/editions/${edition}/progress`)).json()) as { unit: string; locator: string }
    expect(saved).toMatchObject({ unit: 'page', locator: 'page:7' })
  })

  test('a copy-protected book is not opened, says so, and offers the file', async ({ page }) => {
    await ensureLibrary(page)
    for (const title of ['Locked Room', 'Kindle Locked', 'Kobo Locked']) {
      await page.goto(`/read/${book(title).edition}`)
      await expect(page.locator('[data-testid="reader-failed"]')).toContainText('copy-protected')
      await expect(page.locator('[data-testid="reader-failed"]')).toContainText('does not get around the protection')
      await expect(page.getByRole('link', { name: /Download the file/ })).toHaveAttribute('href', /\/api\/v1\/files\/\d+\/download/)
      await expect(page.locator('[data-testid="reader-frame"]')).toHaveCount(0)
    }
  })

  test('a book that lies about its size is stopped as it unpacks', async ({ page }) => {
    await ensureLibrary(page)
    await page.goto(`/read/${book('Heavy Reading').edition}`)
    await expect(page.locator('[data-testid="reader-failed"]')).toContainText('unpacks to far more than it says', { timeout: 30_000 })
    // The book is not shown, the page is still the app's, and the file can be downloaded.
    await expect(page.locator('[data-testid="reader-frame"]')).toBeHidden()
    await expect(page.getByRole('link', { name: /Download the file/ })).toBeVisible()
  })

  test('a format can be asked for by the address', async ({ page }) => {
    await ensureLibrary(page)
    await page.goto(`/read/${book('The Long Road').edition}?format=pdf`)
    // This edition has no PDF: the best file opens instead.
    await opened(page)
    await expect(page.locator('[data-testid="reader"]')).toHaveAttribute('data-format', 'epub')
  })
})

test.describe('a hostile book', () => {
  test('runs no script, reaches no other host, leaves the app where it is, and shows only its text', async ({ page }) => {
    await ensureLibrary(page)
    await page.request.get(`${CANARY}/__reset`)
    const own = test.info().project.use.baseURL!
    const foreign: string[] = []
    page.on('request', (request) => {
      const url = request.url()
      if (!url.startsWith(own) && !url.startsWith('blob:') && !url.startsWith('data:')) foreign.push(url)
    })
    const popups: string[] = []
    page.on('popup', (popup) => popups.push(popup.url()))
    await page.goto(`/read/${book('Hostile Pages').edition}`)
    await opened(page)
    await expect.poll(() => bodyText(page)).toContain('This chapter is made to do harm')
    await page.waitForTimeout(2500)

    // No script of the book ran: none of the marks it would have left is there, in any window.
    expect(await page.title()).not.toBe('pwned')
    expect(await page.evaluate(() => window.name)).not.toBe('pwned')
    for (const frame of page.frames()) {
      const marks = await frame.evaluate(() => ({ title: document.title, name: window.name, attr: document.documentElement.getAttribute('data-pwned') })).catch(() => null)
      expect(marks?.title).not.toBe('pwned')
      expect(marks?.name).not.toBe('pwned')
      expect(marks?.attr).toBeNull()
    }
    // The window of the app is where it was.
    expect(page.url()).toMatch(new RegExp(`/read/${book('Hostile Pages').edition}$`))
    expect(popups).toEqual([])

    // Nothing left for another host: neither what the browser saw leaving, nor what the listener received.
    expect(foreign).toEqual([])
    expect(await (await page.request.get(`${CANARY}/__hits`)).json()).toEqual([])

    // What is in the page of the book: its text, and none of the things that do harm.
    const dom = await pageFrame(page)!.evaluate(() => ({
      scripts: document.querySelectorAll('script').length,
      frames: document.querySelectorAll('iframe, frame, object, embed, form, input, button, video, audio, base').length,
      handlers: Array.from(document.querySelectorAll('*')).filter((element) => element.getAttributeNames().some((name) => name.startsWith('on'))).length,
      metaRefresh: document.querySelectorAll('meta[http-equiv="refresh"]').length,
      policy: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content') ?? '',
      external: Array.from(document.querySelectorAll('[src], link[href], image')).map((element) => element.getAttribute('src') ?? element.getAttribute('href') ?? '').filter((value) => /^(https?:|\/\/|javascript:|data:text)/i.test(value)),
      linkHrefs: Array.from(document.querySelectorAll('a')).map((anchor) => anchor.getAttribute('href')),
      bigPicture: (() => {
        const image = document.querySelector<HTMLImageElement>('img[alt^="a picture that says"]')
        return image ? { complete: image.complete, width: image.naturalWidth } : null
      })(),
    }))
    expect(dom.scripts).toBe(0)
    expect(dom.frames).toBe(0)
    expect(dom.handlers).toBe(0)
    expect(dom.metaRefresh).toBe(0)
    expect(dom.policy).toContain("script-src 'none'")
    expect(dom.policy).toContain("default-src 'none'")
    expect(dom.external).toEqual([])
    // The picture that says it has 3,600,000,000 pixels is not handed to the page.
    expect(dom.bigPicture?.width ?? 0).toBeLessThan(10)
  })

  test('a link to the web opens in a tab of its own, after a click and after the question with its address, never before, and a link of code does nothing', async ({ page }) => {
    await ensureLibrary(page)
    await page.request.get(`${CANARY}/__reset`)
    await page.goto(`/read/${book('Hostile Pages').edition}`)
    await opened(page)
    await expect.poll(() => bodyText(page)).toContain('outside link')
    expect(await (await page.request.get(`${CANARY}/__hits`)).json()).toEqual([])
    const frame = pageFrame(page)!
    // `javascript:` and `data:` links lost their address: nothing to follow.
    const hrefs = await frame.evaluate(() => Array.from(document.querySelectorAll('a')).map((anchor) => [anchor.id, anchor.getAttribute('href')]))
    expect(Object.fromEntries(hrefs)).toMatchObject({ js: null, datalink: null })
    // The link to the web: only after a click and an answer, in a new tab, which cannot reach back (`noopener`). The question
    // names the address, and "Cancel" leaves everything as it was.
    const dialog = page.getByRole('dialog')
    await frame.locator('#out').click()
    await expect(dialog.locator('[data-testid="reader-link-address"]')).toContainText(`${CANARY.replace('http://', '')}/click`)
    expect(await (await page.request.get(`${CANARY}/__hits`)).json()).toEqual([])
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
    expect(await (await page.request.get(`${CANARY}/__hits`)).json()).toEqual([])
    const popup = page.waitForEvent('popup')
    await frame.locator('#out').click()
    await dialog.getByRole('button', { name: 'Open' }).click()
    const opened_ = await popup
    expect(opened_.url()).toContain(`${CANARY}/click`)
    expect(await opened_.evaluate(() => window.opener)).toBeNull()
    expect(page.url()).toMatch(/\/read\/\d+$/)
    await opened_.close()
    // A link inside the book leads on.
    await frame.locator('#in').click()
    await expect.poll(async () => (await where(page)).chapter).toBe(2)
  })

  test('the frame that holds the book cannot be framed by anybody and fetches nothing itself', async ({ page }) => {
    await ensureLibrary(page)
    await page.goto('/')
    const headers = (await page.request.get('/readview.html')).headers()
    expect(headers['content-security-policy']).toContain("connect-src 'none'")
    expect(headers['content-security-policy']).toContain('frame-ancestors')
    expect(headers['x-frame-options']).toBe('SAMEORIGIN')
    const pdf = (await page.request.get('/pdfview.html')).headers()
    expect(pdf['content-security-policy']).toContain("connect-src 'none'")
    expect(pdf['content-security-policy']).toMatch(/^sandbox allow-scripts;/)
    // The frame itself cannot call the server: a fetch from inside it is refused by its policy, not answered.
    await openBook(page, 'The Long Road')
    const result = await page.frames().find((frame) => frame.url().endsWith('/readview.html'))!.evaluate(() => fetch('/api/v1/health').then(() => 'answered', () => 'refused'))
    expect(result).toBe('refused')
  })
})
