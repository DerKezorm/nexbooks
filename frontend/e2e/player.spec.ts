/**
 * The player of recordings in a real browser, headless, against the real backend with invented recordings
 * (`backend/tools/make_audio_books.py`: silence a browser really plays). Opening from the card of a book, playing, jumping across
 * the border of two files, a reload that goes on where it was, a second device that is told, the small player that plays on
 * while the person moves between the pages, the sleep timer with the clock of the page moved by the test, the lock screen
 * (Media Session), a phone, light and dark, the speed and the bookmarks with a note, and a format the browser does not play.
 *
 * **WebKit plays nothing here.** Playwright's WebKit for Windows fetches a recording through Windows Media Foundation, without the
 * cookies of the page (measured: the request comes as `NSPlayer/12 WMFSDK`, with no `Cookie`), so every recording of a signed-in
 * person answers 401 there. What does not need sound runs in WebKit too (the honest message for Ogg, the faces of the player); what
 * needs sound is skipped in WebKit with this reason, and counts as not tested there. Safari on a Mac or an iPhone loads media with
 * the cookies of the page.
 */
import { expect, test, type Browser, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-player00' }
const WEBKIT_SILENT = 'Playwright WebKit on Windows loads media without cookies (Media Foundation): no recording of a signed-in person plays there'

type Book = { work: number; edition: number; files: number[] }
const books = new Map<string, Book>()
let library = 0

async function ensureLibrary(page: Page): Promise<void> {
  if (library) return
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Listening', path: path.join(process.env.E2E_BOOKS!, 'listening'), kind: 'both', scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  library = ((await made.json()) as { id: number }).id
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { total: number }).total, { timeout: 90_000, intervals: [1000] })
    .toBe(8)
  const list = (await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { items: { id: number; title: string; editions: { id: number; kind: string }[] }[] }
  for (const item of list.items) {
    const audio = item.editions.find((edition) => edition.kind === 'audio')!
    const full = (await (await page.request.get(`/api/v1/editions/${audio.id}`)).json()) as { edition: { files: { id: number }[] } }
    books.set(item.title, { work: item.id, edition: audio.id, files: full.edition.files.map((file) => file.id) })
  }
}

test.afterAll(async ({ browser }) => {
  // The recordings of this spec leave with it: the shelf spec counts the works it sees.
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

/** Puts the person at a place of a recording, as the newest report of all (a device of the test). */
async function placeAt(page: Page, title: string, seconds: number): Promise<void> {
  const answer = await page.request.put(`/api/v1/editions/${book(title).edition}/progress`, {
    headers: TAB,
    data: { locator: `time:${seconds.toFixed(3)}`, fraction: 0, unit: 'time', chapter: 1, chapter_count: 1, device_key: 'e2e-setup', device: '' },
  })
  expect(answer.ok(), await answer.text()).toBe(true)
}

async function serverPlace(page: Page, title: string): Promise<number | null> {
  const place = (await (await page.request.get(`/api/v1/editions/${book(title).edition}/progress`)).json()) as { position: number } | null
  return place?.position ?? null
}

const full = (page: Page) => page.locator('[data-testid="full-player"]')
const mini = (page: Page) => page.locator('[data-testid="mini-player"]')
const position = async (page: Page, which = full(page)) => Number(await which.getAttribute('data-position'))

/** Opens the page of the book and presses "Listen" on the card of the recording: the big player opens and plays. */
async function listen(page: Page, title: string): Promise<void> {
  await page.goto(`/work/${book(title).work}`)
  await page.locator('[data-testid="listen"]').click()
  await expect(full(page)).toBeVisible()
  await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
}

/** Which file the element that plays holds, and where in it. */
const playing = (page: Page) =>
  page.evaluate(() => {
    const element = [...document.querySelectorAll<HTMLAudioElement>('[data-testid="player-audio"]')].find((audio) => !audio.paused)
    return element ? { src: element.getAttribute('src') ?? '', time: element.currentTime } : null
  })

test.beforeEach(async ({ page }) => {
  await ensureLibrary(page)
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { player_speed: 100, player_back: 15, player_ahead: 30, player_rewind: true, theme: 'light' } })
})

test.describe('playing', () => {
  test('the big player covers the whole window: no strip is kept beside it for a scrollbar', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 0)
    await listen(page, 'The Quiet Harbour')
    const edge = await page.evaluate(() => {
      const element = document.elementFromPoint(window.innerWidth - 3, window.innerHeight / 2)
      return { inPlayer: !!element?.closest('[data-testid="full-player"]'), gap: window.innerWidth - document.documentElement.clientWidth }
    })
    expect(edge.gap).toBe(0)
    expect(edge.inPlayer).toBe(true)
  })

  test('opens from the card of the book, plays, and says the chapter, the time and what is left', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 0)
    await page.goto(`/work/${book('The Quiet Harbour').work}`)
    await expect(page.locator('.twin[data-kind="audio"] [data-testid="listen"]')).toHaveText('Listen on')
    await page.locator('[data-testid="listen"]').click()
    await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 1 of 3')
    await expect(page.locator('[data-testid="player-chapter"]')).toHaveText('Arrival')
    await expect.poll(() => position(page), { timeout: 10_000 }).toBeGreaterThan(1.5)
    await expect(page.locator('[data-testid="player-book-left"]')).toContainText(/left · \d+ %/)
    // The way back of the browser folds the big player up; the small one plays on over the page of the book.
    await page.goBack()
    await expect(full(page)).toHaveCount(0)
    await expect(mini(page)).toHaveAttribute('data-playing', 'yes')
    await expect(page.locator('.twin[data-kind="audio"] [data-testid="twin-position"]')).toContainText(/of 1 min · \d+ %/)
  })

  test('jumps across the border of two files and plays on from one file into the next', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'Seven Bridges', 0)
    await listen(page, 'Seven Bridges')
    const [first, second, third] = book('Seven Bridges').files
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${first}/content`)
    // To the third part and back into the second, by the chapters (one per file).
    await page.locator('[data-testid="player-chapter-ahead"]').click()
    await page.locator('[data-testid="player-chapter-ahead"]').click()
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${third}/content`)
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 3 of 3')
    await page.locator('[data-testid="player-chapter-back"]').click()
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${second}/content`)
    // Near the end of the second part, and on by itself into the third.
    await page.locator('[data-testid="player-scrub"]').fill('13')
    await expect.poll(() => position(page), { timeout: 10_000 }).toBeGreaterThan(27.5)
    await expect.poll(async () => (await playing(page))?.src, { timeout: 15_000 }).toBe(`/api/v1/files/${third}/content`)
    await expect.poll(() => position(page), { timeout: 10_000 }).toBeGreaterThan(30.2)
    expect(await position(page)).toBeLessThan(40)
  })

  test('a chapter over several files is one chapter: the list, the count and the jumps across the files', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'Long Chapters', 8)
    await listen(page, 'Long Chapters')
    const files = book('Long Chapters').files
    expect(files).toHaveLength(6)
    // Six files, three chapters: the second file of "Opening" is chapter 1 still.
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 1 of 3')
    await expect(page.locator('[data-testid="player-chapter"]')).toHaveText('Opening')
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[1]}/content`)
    await page.locator('[data-testid="open-chapters"]').click()
    const items = page.locator('[data-testid="chapter-item"]')
    await expect(items).toHaveCount(3)
    await expect(items.nth(0)).toContainText('Opening')
    await expect(items.nth(1)).toContainText('Crossing')
    await expect(items.nth(2)).toContainText('Finale')
    await page.keyboard.press('Escape')
    // Chapter 2 begins where the third file begins, chapter 3 where the sixth does.
    await page.locator('[data-testid="player-chapter-ahead"]').click()
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 2 of 3')
    await expect(page.locator('[data-testid="player-chapter"]')).toHaveText('Crossing')
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[2]}/content`)
    await page.locator('[data-testid="player-chapter-ahead"]').click()
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 3 of 3')
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[5]}/content`)
    // Back: to the start of the chapter before, which lies three files down.
    await page.locator('[data-testid="player-chapter-back"]').click()
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 2 of 3')
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[2]}/content`)
  })

  test('a reload goes on where it was, and the place is saved when it is paused', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 30)
    await listen(page, 'The Quiet Harbour')
    await expect.poll(() => position(page), { timeout: 10_000 }).toBeGreaterThan(32)
    const saving = page.waitForResponse((answer) => answer.url().endsWith(`/editions/${book('The Quiet Harbour').edition}/progress`) && answer.request().method() === 'PUT')
    await page.locator('[data-testid="player-play"]').click()
    await saving
    const paused = await position(page)
    expect(Math.abs((await serverPlace(page, 'The Quiet Harbour'))! - paused)).toBeLessThan(0.6)
    await page.reload()
    // After the reload the player is there again, at the place, paused (a browser plays nothing by itself).
    const back = full(page).or(mini(page))
    await expect(back).toBeVisible({ timeout: 15_000 })
    await expect(back).toHaveAttribute('data-playing', 'no')
    await expect.poll(() => position(page, back)).toBeGreaterThan(paused - 0.6)
    expect(await position(page, back)).toBeLessThan(paused + 0.6)
  })

  test('another device that is further along is named, and a jump leads there', async ({ browser, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    const device = (): ReturnType<Browser['newContext']> => browser.newContext({ storageState: process.env.E2E_STATE, locale: 'en-US' })
    const tablet = await (await device()).newPage()
    await placeAt(tablet, 'The Quiet Harbour', 2)
    await listen(tablet, 'The Quiet Harbour')
    await expect.poll(() => position(tablet), { timeout: 10_000 }).toBeGreaterThan(4)
    await tablet.locator('[data-testid="player-play"]').click()
    await expect.poll(() => serverPlace(tablet, 'The Quiet Harbour')).toBeGreaterThan(4)
    // The tablet folds its player up: the small one stays, paused.
    await tablet.goBack()
    await expect(mini(tablet)).toHaveAttribute('data-playing', 'no')
    // The phone listens on, from the tablet's place, much further.
    const phone = await (await device()).newPage()
    await listen(phone, 'The Quiet Harbour')
    await phone.locator('[data-testid="player-scrub"]').fill('17')
    await expect.poll(() => position(phone), { timeout: 10_000 }).toBeGreaterThan(17.5)
    await phone.locator('[data-testid="player-play"]').click()
    await expect.poll(() => serverPlace(phone, 'The Quiet Harbour')).toBeGreaterThan(17)
    // Back on the tablet, after a reload: its own place first, and the question. "Listen on" shows it instead of playing over it.
    await tablet.reload()
    await expect(mini(tablet)).toBeVisible()
    await tablet.locator('[data-testid="listen"]').click()
    const hint = tablet.locator('[data-testid="player-hint"]')
    await expect(hint).toContainText(/You are further along on .+: at 0:1\d\. Jump there\?/)
    await expect(full(tablet)).toHaveAttribute('data-playing', 'no')
    await tablet.locator('[data-testid="player-hint-jump"]').click()
    await expect(hint).toHaveCount(0)
    await expect.poll(() => position(tablet)).toBeGreaterThan(17)
    await tablet.context().close()
    await phone.context().close()
  })

  test('the small player plays on while the person moves between the pages of the app', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Lantern Keeper', 60)
    await listen(page, 'The Lantern Keeper')
    await page.locator('[data-testid="player-collapse"]').click()
    await expect(mini(page)).toHaveAttribute('data-playing', 'yes')
    // From the page of the book to the shelf, and through the areas of the head.
    await page.locator('.d-back').click()
    await expect(page.locator('.detail')).toHaveCount(0)
    const before = await playing(page)
    for (const area of ['Discover', 'Reading stack', 'Library']) {
      await page.locator('nav.tabs a', { hasText: area }).click()
      await expect(mini(page)).toHaveAttribute('data-playing', 'yes')
    }
    const after = await playing(page)
    // The same element with the same file, further on: nothing was loaded again.
    expect(after?.src).toBe(before?.src)
    expect(after!.time).toBeGreaterThan(before!.time)
    // From the small player into the big one, and closed.
    await page.locator('[data-testid="mini-open"]').click()
    await expect(full(page)).toHaveAttribute('data-playing', 'yes')
    await page.locator('[data-testid="player-close"]').click()
    await expect(full(page)).toHaveCount(0)
    await expect(mini(page)).toHaveCount(0)
    expect(await playing(page)).toBeNull()
  })

  test('keys: the space bar pauses and plays, the arrows jump back and ahead', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Lantern Keeper', 600)
    await listen(page, 'The Lantern Keeper')
    await full(page).focus()
    await page.keyboard.press('ArrowRight')
    await expect.poll(() => position(page)).toBeGreaterThan(629)
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => position(page)).toBeLessThan(625)
    await page.keyboard.press('Space')
    await expect(full(page)).toHaveAttribute('data-playing', 'no')
    await page.keyboard.press('Space')
    await expect(full(page)).toHaveAttribute('data-playing', 'yes')
    // Escape closes a panel first, then folds the player up, and leaves the page of the book under it where it is.
    await page.locator('[data-testid="open-chapters"]').click()
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="panel-chapters"]')).toHaveCount(0)
    await expect(full(page)).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(full(page)).toHaveCount(0)
    await expect(mini(page)).toBeVisible()
    await expect(page.locator('.detail .d-title')).toHaveText('The Lantern Keeper')
  })
})

test.describe('the sleep timer', () => {
  test('stops after the minutes chosen, counted on the clock of the page', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await page.clock.install()
    await placeAt(page, 'The Lantern Keeper', 120)
    await listen(page, 'The Lantern Keeper')
    await page.locator('[data-testid="open-sleep"]').click()
    await page.locator('[data-testid="sleep-5"]').click()
    await expect(page.locator('[data-testid="sleep-label"]')).toHaveText(/^[45]:\d\d$/)
    await page.clock.runFor(4 * 60_000)
    await expect(full(page)).toHaveAttribute('data-playing', 'yes')
    await expect(page.locator('[data-testid="sleep-label"]')).toHaveText(/^[01]:\d\d$/)
    // The minute that is left, and a little more (the sentence of the player stays a few seconds of the page's clock).
    await page.clock.runFor(61_000)
    await expect(full(page)).toHaveAttribute('data-playing', 'no')
    await expect(page.locator('[data-testid="player-said"]')).toHaveText('The sleep timer stopped playback.')
    await expect(page.locator('[data-testid="sleep-label"]')).toHaveText('Sleep timer')
    // The sound is back at full for the next time.
    expect(await page.evaluate(() => [...document.querySelectorAll<HTMLAudioElement>('[data-testid="player-audio"]')].map((audio) => audio.volume))).toEqual([1, 1])
  })

  test('stops at the end of the chapter, at its end', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 16)
    await listen(page, 'The Quiet Harbour')
    await page.locator('[data-testid="open-sleep"]').click()
    await page.locator('[data-testid="sleep-chapter"]').click()
    await expect(page.locator('[data-testid="sleep-label"]')).toHaveText('End of chapter')
    await expect(full(page)).toHaveAttribute('data-playing', 'no', { timeout: 15_000 })
    const stopped = await position(page)
    expect(stopped).toBeGreaterThan(19.5)
    expect(stopped).toBeLessThan(20.6)
  })
})

test.describe('around the player', () => {
  test('the lock screen knows the book, the chapter, the cover of nexbooks itself and the place', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 22)
    await listen(page, 'The Quiet Harbour')
    const session = await page.evaluate(() => {
      const meta = navigator.mediaSession.metadata
      return { title: meta?.title, artist: meta?.artist, album: meta?.album, artwork: meta?.artwork.map((art) => art.src), state: navigator.mediaSession.playbackState, origin: location.origin }
    })
    expect(session).toMatchObject({ title: 'The Pier', artist: 'Ada Feldkamp', album: 'The Quiet Harbour', state: 'playing' })
    expect(session.artwork).toHaveLength(1)
    expect(session.artwork![0].startsWith(`${session.origin}/api/v1/works/`)).toBe(true)
  })

  test('the speed is kept with the account and the time left counts with it', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Lantern Keeper', 0)
    await listen(page, 'The Lantern Keeper')
    const left = async () => (await page.locator('[data-testid="player-book-left"]').textContent()) ?? ''
    await expect(page.locator('[data-testid="player-book-left"]')).toContainText('1 h 0 min left')
    await page.locator('[data-testid="open-speed"]').click()
    await page.locator('[data-testid="speed-2"]').click()
    await expect(page.locator('[data-testid="speed-value"]')).toHaveText('2.0×')
    await expect.poll(left).toContain('30 min left')
    expect(await page.evaluate(() => [...document.querySelectorAll<HTMLAudioElement>('[data-testid="player-audio"]')][0].playbackRate)).toBe(2)
    await expect.poll(async () => ((await (await page.request.get('/api/v1/auth/me')).json()) as { profile: { player_speed: number } }).profile.player_speed).toBe(200)
    await page.locator('[data-testid="speed-slider"]').fill('1.35')
    await expect(page.locator('[data-testid="speed-value"]')).toHaveText('1.35×')
  })

  test('a bookmark with a note, and deleting it asks first', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 41)
    await listen(page, 'The Quiet Harbour')
    await page.locator('[data-testid="open-marks"]').click()
    await page.locator('[data-testid="add-mark"]').click()
    // A second press at the same place gives the one mark.
    await page.locator('[data-testid="add-mark"]').click()
    await expect(page.locator('[data-testid="mark-item"]')).toHaveCount(1)
    await page.locator('[data-testid="mark-note-input"]').fill('the lighthouse keeper speaks')
    await page.locator('[data-testid="mark-note-save"]').click()
    await expect(page.locator('[data-testid="mark-note"]')).toHaveText('the lighthouse keeper speaks')
    const marks = (await (await page.request.get(`/api/v1/editions/${book('The Quiet Harbour').edition}/bookmarks`)).json()) as { note: string; chapter_title: string }[]
    expect(marks.map((mark) => [mark.note, mark.chapter_title])).toEqual([['the lighthouse keeper speaks', 'Night']])
    await page.locator('[data-testid="mark-delete"]').click()
    const dialog = page.getByRole('dialog', { name: 'Delete the bookmark?' })
    await expect(dialog).toContainText('cannot be undone')
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.locator('[data-testid="mark-item"]')).toHaveCount(1)
    await page.locator('[data-testid="mark-delete"]').click()
    await page.getByRole('dialog', { name: 'Delete the bookmark?' }).getByRole('button', { name: 'Delete' }).click()
    await expect(page.locator('[data-testid="mark-item"]')).toHaveCount(0)
  })

  test('a format the browser does not play is said, with the file to download', async ({ page, browserName }) => {
    await placeAt(page, 'Night Radio', 0)
    await page.goto(`/work/${book('Night Radio').work}`)
    await page.locator('[data-testid="listen"]').click()
    if (browserName === 'webkit') {
      // WebKit knows no Ogg: said at once, without asking for the file.
      const failed = page.locator('[data-testid="player-failed"]')
      await expect(failed).toHaveAttribute('data-reason', 'unsupported')
      await expect(failed).toContainText('This browser does not play OGG')
      await expect(page.locator('[data-testid="player-download"]')).toHaveAttribute('href', `/api/v1/files/${book('Night Radio').files[0]}/download`)
      await expect(page.locator('[data-testid="player-play"]')).toBeDisabled()
    } else {
      await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
      await expect(page.locator('[data-testid="player-failed"]')).toHaveCount(0)
    }
  })

  test('on a phone of 390 points everything fits, in light and in dark', async ({ browser, browserName }) => {
    for (const colorScheme of ['light', 'dark'] as const) {
      const context = await browser.newContext({
        storageState: process.env.E2E_STATE,
        viewport: { width: 390, height: 844 },
        colorScheme,
        hasTouch: true,
        ...(browserName === 'firefox' ? {} : { isMobile: true, deviceScaleFactor: 2 }),
      })
      const page = await context.newPage()
      await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { theme: 'system' } })
      await placeAt(page, 'The Lantern Keeper', 900)
      await page.goto(`/work/${book('The Lantern Keeper').work}`)
      await page.locator('[data-testid="listen"]').click()
      await expect(full(page)).toBeVisible()
      await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText(/Chapter \d+ of 38/)
      // Nothing wider than the phone, the controls inside it, large enough for a thumb.
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
      for (const id of ['player-back', 'player-play', 'player-ahead']) {
        const box = (await page.locator(`[data-testid="${id}"]`).boundingBox())!
        expect(box.x).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width).toBeLessThanOrEqual(390)
        expect(box.width).toBeGreaterThanOrEqual(44)
      }
      // The colour of the ground, as the browser paints it (a canvas reads any colour of CSS).
      const light = await full(page).evaluate((element) => {
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = 1
        const context = canvas.getContext('2d')!
        context.fillStyle = getComputedStyle(element).backgroundColor
        context.fillRect(0, 0, 1, 1)
        const [r, g, b] = context.getImageData(0, 0, 1, 1).data
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
      })
      if (colorScheme === 'dark') expect(light).toBeLessThan(0.35)
      else expect(light).toBeGreaterThan(0.6)
      // The chapters come from below and lead somewhere.
      await page.locator('[data-testid="open-chapters"]').click()
      const panel = page.locator('[data-testid="panel-chapters"]')
      await expect(panel).toBeVisible()
      expect(((await panel.boundingBox())!.y)).toBeGreaterThan(100)
      await expect(page.locator('[data-testid="chapter-item"]')).toHaveCount(38)
      await context.close()
    }
  })
})

/** A part of a recording spoiled for the length of one test: damaged (the same size of nothing but "A"), or deleted. Put back
 * afterwards, so that the next browser's run of this file finds the books as they were made. */
async function withSpoiledPart<T>(title: string, how: 'damage' | 'delete', run: () => Promise<T>): Promise<T> {
  const file = path.join(process.env.E2E_BOOKS!, 'listening', title, '02.mp3')
  const original = fs.readFileSync(file)
  const { atime, mtime } = fs.statSync(file)
  if (how === 'delete') fs.unlinkSync(file)
  else fs.writeFileSync(file, Buffer.alloc(original.length, 0x41))
  try {
    return await run()
  } finally {
    // Back as it was, with its old time: a file changed a moment ago is left out of the next scan (its quiet time, a
    // minute), and the next library made of this folder (the same spec in the next browser) would lack the part.
    fs.writeFileSync(file, original)
    fs.utimesSync(file, atime, mtime)
  }
}

const rectsOverlap = (a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

test.describe('chapters on the border of a file', () => {
  test('a chapter that begins where a file begins starts that file at its beginning, also after a pause', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    const files = book('Exact Parts').files
    expect(files).toHaveLength(80)
    await placeAt(page, 'Exact Parts', 0)
    await listen(page, 'Exact Parts')
    // Paused, then to chapter 4: it begins at 3.9 s, where the fourth file begins (three times 1.3 s is 3.9000000000000004 in
    // floating point, the server counts 3900 ms), and the way down to it must not pass through the end of the third file.
    await page.locator('[data-testid="player-play"]').click()
    await expect(full(page)).toHaveAttribute('data-playing', 'no')
    for (let n = 0; n < 3; n++) await page.locator('[data-testid="player-chapter-ahead"]').click()
    await expect(page.locator('[data-testid="player-chapter-of"]')).toHaveText('Chapter 4 of 80')
    expect(await position(page)).toBeCloseTo(3.9, 1)
    await page.locator('[data-testid="player-play"]').click()
    await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
    // The fourth file plays from its beginning. It lasts 1.3 s: on a loaded machine the fifth may already play (rightly),
    // and between two files a moment may pass in which none plays; asked until one does. Never the third.
    const next = [`/api/v1/files/${files[3]}/content`, `/api/v1/files/${files[4]}/content`]
    await expect.poll(async () => (await playing(page))?.src ?? '', { timeout: 10_000 }).toMatch(/\/content$/)
    expect(next).toContain((await playing(page))?.src ?? next[0])
    // The place never goes back into the third file (the old way: 2.6 s).
    const seen: number[] = []
    for (let n = 0; n < 10; n++) {
      seen.push(await position(page))
      await page.waitForTimeout(100)
    }
    expect(Math.min(...seen)).toBeGreaterThan(3.85)
    // And by the list of the chapters, one of the later ones that the old sums got wrong (chapter 27).
    await page.locator('[data-testid="open-chapters"]').click()
    await page.locator('[data-testid="chapter-item"]').nth(26).click()
    await page.keyboard.press('Escape')
    await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[26]}/content`)
    expect(await position(page)).toBeGreaterThan(33.7)
    expect(await position(page)).toBeLessThan(36)
  })

  test('a damaged part is named, and "Skip this part" goes on with the next', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    const files = book('Broken Part').files
    await withSpoiledPart('Broken Part', 'damage', async () => {
      await placeAt(page, 'Broken Part', 4.5)
      await listen(page, 'Broken Part')
      const failed = page.locator('[data-testid="player-failed"]')
      await expect(failed).toBeVisible({ timeout: 25_000 })
      await expect(failed).toHaveAttribute('data-reason', 'broken')
      await expect(page.locator('[data-testid="player-failed-part"]')).toHaveText('Part 2 of 3')
      await expect(page.locator('[data-testid="player-play"]')).toBeDisabled()
      await page.locator('[data-testid="player-skip-part"]').click()
      await expect(failed).toHaveCount(0)
      await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
      await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[2]}/content`)
      expect(await position(page)).toBeGreaterThan(11.9)
    })
  })

  test('after a missing part a jump to another chapter plays on, and the message is gone', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    const files = book('Missing Part').files
    await withSpoiledPart('Missing Part', 'delete', async () => {
      await placeAt(page, 'Missing Part', 4.5)
      await listen(page, 'Missing Part')
      const failed = page.locator('[data-testid="player-failed"]')
      await expect(failed).toBeVisible({ timeout: 25_000 })
      await expect(page.locator('[data-testid="player-failed-part"]')).toHaveText('Part 2 of 3')
      // To the chapter before (the first): the failure goes, the first file plays from its beginning.
      await page.locator('[data-testid="player-chapter-back"]').click()
      await expect(failed).toHaveCount(0)
      await expect(full(page)).toHaveAttribute('data-playing', 'yes', { timeout: 15_000 })
      await expect.poll(async () => (await playing(page))?.src, { timeout: 10_000 }).toBe(`/api/v1/files/${files[0]}/content`)
      expect(await position(page)).toBeLessThan(3)
    })
  })

  test('a double click on "Listen" makes one step in the history, so that one way back folds the player up', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Quiet Harbour', 0)
    await page.goto(`/work/${book('The Quiet Harbour').work}`)
    const before = await page.evaluate(() => window.history.length)
    await page.locator('[data-testid="listen"]').dblclick()
    await expect(full(page)).toBeVisible()
    expect(await page.evaluate(() => window.history.length)).toBe(before + 1)
    await page.goBack()
    await expect(full(page)).toHaveCount(0)
    await expect(page.locator('.detail .d-title')).toHaveText('The Quiet Harbour')
  })

  test('the arrow keys on a slider move the slider, they do not jump', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Lantern Keeper', 600)
    await listen(page, 'The Lantern Keeper')
    const scrub = page.locator('[data-testid="player-scrub"]')
    await scrub.focus()
    const before = await position(page)
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(700)
    // The bar moved by one step (a second), not by the jump ahead (30 seconds).
    expect(await position(page)).toBeLessThan(before + 10)
    await page.locator('[data-testid="open-speed"]').click()
    await page.locator('[data-testid="speed-slider"]').focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.locator('[data-testid="speed-value"]')).toHaveText('1.05×')
    expect(await position(page)).toBeLessThan(before + 15)
  })

  test('while it plays the place is sent about every five seconds', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    await placeAt(page, 'The Lantern Keeper', 100)
    await listen(page, 'The Lantern Keeper')
    // No pause, no leaving: the server has the place of the playing (the old pace was ten seconds).
    await expect.poll(async () => (await serverPlace(page, 'The Lantern Keeper')) ?? 0, { timeout: 8_500, intervals: [500] }).toBeGreaterThan(103)
    await expect(full(page)).toHaveAttribute('data-playing', 'yes')
  })

  test('on a phone the sentence of the player does not cover the notes of the bookmarks', async ({ browser, browserName }) => {
    test.skip(browserName === 'webkit', WEBKIT_SILENT)
    const context = await browser.newContext({
      storageState: process.env.E2E_STATE,
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      ...(browserName === 'firefox' ? {} : { isMobile: true, deviceScaleFactor: 2 }),
    })
    const page = await context.newPage()
    await placeAt(page, 'The Quiet Harbour', 30)
    await listen(page, 'The Quiet Harbour')
    // Paused, so that the place stands still and a second press falls on the mark.
    await page.locator('[data-testid="player-play"]').click()
    await expect(full(page)).toHaveAttribute('data-playing', 'no')
    await page.locator('[data-testid="open-marks"]').click()
    await page.locator('[data-testid="add-mark"]').click()
    await page.locator('[data-testid="mark-note-input"]').fill('the lighthouse keeper speaks')
    await page.locator('[data-testid="mark-note-save"]').click()
    await expect(page.locator('[data-testid="mark-note"]')).toBeVisible()
    // A second press at the same place says so in a sentence: it must not stand on the text of the marks.
    await page.locator('[data-testid="add-mark"]').click()
    const said = page.locator('[data-testid="player-said"]')
    await expect(said).toBeVisible()
    const toast = (await said.boundingBox())!
    // (A second press opens the note of the mark for writing: the field stands where the text of the note did.)
    for (const selector of ['[data-testid="mark-item"]', '[data-testid="add-mark"]', '[data-testid="mark-note-input"]']) {
      const box = await page.locator(selector).first().boundingBox()
      expect(box, `${selector} is on the page`).not.toBeNull()
      expect(rectsOverlap(toast, box!), `${selector} is covered by the sentence`).toBe(false)
    }
    await context.close()
  })

  test('WebKit, which cannot tell a damaged file from one it could not fetch, does not call it damaged', async ({ page, browserName }) => {
    test.skip(browserName !== 'webkit', 'Only WebKit says this')
    await placeAt(page, 'The Quiet Harbour', 0)
    await page.goto(`/work/${book('The Quiet Harbour').work}`)
    await page.locator('[data-testid="listen"]').click()
    const failed = page.locator('[data-testid="player-failed"]')
    await expect(failed).toBeVisible({ timeout: 25_000 })
    await expect(failed).toHaveAttribute('data-reason', 'browser')
    await expect(failed).toContainText('This browser does not play this file')
    await expect(failed).not.toContainText('damaged')
    await expect(page.locator('[data-testid="player-download"]')).toHaveAttribute('href', `/api/v1/files/${book('The Quiet Harbour').files[0]}/download`)
  })
})
