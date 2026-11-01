/**
 * The reading stack, "Finished", the list, the authors and tidying in a real browser, headless, against the real
 * backend with invented books (`backend/tools/make_author_books.py`): a library laid out by author with photos in two of the
 * authors' folders, and a few loose files to tidy. What the unit tests cannot see: that the towers drop and a press on a
 * lying book opens it, that a double click on "Finished" makes one entry, that the list keeps its order across a reload and
 * holds on a phone, that the photos in the authors' folders really arrive as pictures, and that tidying moves the files on
 * the disk, as the preview said, and puts them back.
 */
import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-stack00' }

type Brief = { id: number; title: string; editions: { id: number; kind: 'ebook' | 'audio' }[] }
const libraries: number[] = []

async function library(page: Page, name: string, folder: string, works: number): Promise<number> {
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name, path: path.join(process.env.E2E_BOOKS!, 'authors', folder), kind: 'both', scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  const id = ((await made.json()) as { id: number }).id
  libraries.push(id)
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats', { params: { library: id } })).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBe(works)
  return id
}

async function books(page: Page, id: number): Promise<Record<string, Brief>> {
  const list = (await (await page.request.get('/api/v1/works', { params: { library: id, per_page: 200 } })).json()) as { items: Brief[] }
  return Object.fromEntries(list.items.map((work) => [work.title, work]))
}

const edition = (work: Brief, kind: 'ebook' | 'audio' = 'ebook') => work.editions.find((e) => e.kind === kind)!.id

async function entries(page: Page): Promise<{ id: number; edition_id: number; finished_on: string }[]> {
  return (await (await page.request.get('/api/v1/history', { params: { limit: 200 } })).json()) as { id: number; edition_id: number; finished_on: string }[]
}

async function clearHistory(page: Page): Promise<void> {
  for (const entry of await entries(page)) await page.request.delete(`/api/v1/history/${entry.id}`, { headers: TAB })
}

let authors = 0
let shelf: Record<string, Brief> = {}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  const page = await context.newPage()
  authors = await library(page, 'Authors', 'authors', 6)
  shelf = await books(page, authors)
  // The photos in the authors' folders are read by a job that follows the scan.
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/authors', { params: { q: 'Feldkamp' } })).json()) as { items: { photo_url: string | null }[] }).items[0]?.photo_url ?? null, { timeout: 60_000, intervals: [1000] })
    .not.toBeNull()
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf', library_section: 'books', list_sort: 'shelf', list_dir: 'asc', authors_sort: 'name' } })
  await context.close()
})

test.afterAll(async ({ browser }) => {
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  const page = await context.newPage()
  await clearHistory(page)
  for (const id of libraries) await page.request.delete(`/api/v1/libraries/${id}`, { headers: TAB })
  await page.request.put('/api/v1/organize/settings', { headers: TAB, data: { enabled: false } })
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf', library_section: 'books', list_sort: 'shelf', list_dir: 'asc' } })
  await context.close()
})

test.describe('the reading stack', () => {
  test.beforeEach(async ({ page }) => {
    await clearHistory(page)
    for (const [title, kind, day] of [['Alpha', 'ebook', '2024-02-10'], ['Beta', 'ebook', '2024-05-01'], ['Alpha', 'audio', '2025-03-03'], ['Delta Station', 'ebook', '2025-06-01']] as const) {
      const made = await page.request.post(`/api/v1/editions/${edition(shelf[title]!, kind)}/finished`, { headers: TAB, data: { finished_on: day } })
      expect(made.ok(), await made.text()).toBe(true)
    }
  })

  test('stands as one tower for each year with the recap of the last year that is over', async ({ page }) => {
    await page.goto('/stack')
    await expect(page.locator('.lying')).toHaveCount(4)
    await expect(page.locator('.year')).toHaveCount(2)
    await expect(page.locator('[data-testid="rc-books"]')).toContainText('2')
    await expect(page.locator('[data-testid="rc-books"]')).toContainText('Books in 2025')
    // A book lies as thick as its pages say, within the bounds of the design (a recording heard of a book that has pages is as
    // thick as the book; only a recording alone is 13 pixels, and the library has none), and the recording wears its ring.
    const thickness = await page.locator('.lying').evaluateAll((items) => items.map((item) => ({ audio: item.classList.contains('audio'), height: item.getBoundingClientRect().height })))
    for (const one of thickness) {
      expect(one.height).toBeGreaterThanOrEqual(8)
      expect(one.height).toBeLessThanOrEqual(22)
    }
    expect(thickness.filter((one) => one.audio)).toHaveLength(1)
    await page.getByTestId('year-2024').click()
    await expect(page.locator('[data-testid="rc-books"]')).toContainText('Books in 2024')
    // The year of the present is "so far".
    expect(await page.locator('.year h3 small').count()).toBeLessThanOrEqual(1)
  })

  test('drops the books one after the other, and not at all with reduced motion', async ({ page }) => {
    await page.goto('/stack')
    await page.locator('.lying').first().waitFor()
    const drops = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('.lying')].map((item) => item.getAnimations().map((a) => ({ name: (a as CSSAnimation).animationName, delay: a.effect?.getTiming().delay }))))
    expect(drops.some((list) => list.some((a) => a.name === 'drop'))).toBe(true)
    // One after the other: in each year the n-th book starts 28 ms after the one before.
    const delays = await page.evaluate(() => [...document.querySelectorAll('.year')].map((year) => [...year.querySelectorAll<HTMLElement>('.lying')]
      .map((item) => Number(item.getAnimations().find((a) => (a as CSSAnimation).animationName === 'drop')?.effect?.getTiming().delay))))
    expect(delays.some((list) => list.length > 1)).toBe(true)
    for (const list of delays) list.forEach((delay, index) => expect(delay).toBe(index * 28))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.reload()
    await page.locator('.lying').first().waitFor()
    const calm = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('.lying')].map((item) => item.getAnimations().filter((a) => a.playState === 'running' && Number(a.effect?.getTiming().duration) > 5).length))
    expect(calm.every((n) => n === 0)).toBe(true)
  })

  test('opens a book when a lying book is pressed, shows a tip over it, and Escape leaves the stack where it was', async ({ page }) => {
    await page.goto('/stack')
    const lying = page.locator('.lying').first()
    await lying.hover()
    await expect(page.locator('[data-testid="spine-tip"]')).toBeVisible()
    await lying.click()
    await expect(page.locator('.detail.show')).toBeVisible()
    expect(page.url()).toMatch(/\/work\/\d+$/)
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    expect(page.url()).toMatch(/\/stack$/)
  })

  test('is empty with a sentence that says how a book comes onto it', async ({ page }) => {
    await clearHistory(page)
    await page.goto('/stack')
    await expect(page.locator('[data-testid="stack-empty"]')).toContainText('Your stack will grow here once you have read or listened to books')
    await expect(page.locator('[data-testid="stack-hint"]')).toContainText('counts as finished when you read or listen to the end')
  })

  test('keeps to the width of a phone, the towers rolling sideways', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/stack')
    await page.locator('.lying').first().waitFor()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
    expect(await page.locator('#stacks').evaluate((box) => getComputedStyle(box).overflowX)).toBe('auto')
  })
})

test.describe('"Finished" by hand', () => {
  test.beforeEach(async ({ page }) => {
    await clearHistory(page)
  })

  test('enters a book once however fast it is pressed, shows the day, and takes it back after a question', async ({ page }) => {
    const work = shelf['Epsilon']!
    await page.goto(`/work/${work.id}`)
    const mark = page.getByTestId('finish-mark')
    await expect(mark).toBeVisible()
    // The page sends one request for a double press (the server would keep one entry either way; this is the page's own guard).
    const sent: string[] = []
    page.on('request', (request) => { if (request.method() === 'POST' && /\/editions\/\d+\/finished$/.test(request.url())) sent.push(request.url()) })
    await mark.dblclick()
    await expect(page.getByTestId('finish-done')).toContainText('Finished on')
    expect(sent).toHaveLength(1)
    expect((await entries(page)).filter((e) => e.edition_id === edition(work))).toHaveLength(1)
    // "Not finished" asks first: Cancel changes nothing, the other button takes the entry away.
    await page.getByTestId('finish-undo').click()
    await expect(page.getByRole('dialog', { name: 'Mark as not finished?' })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    expect((await entries(page)).length).toBe(1)
    await page.getByTestId('finish-undo').click()
    await page.getByRole('dialog', { name: 'Mark as not finished?' }).getByRole('button', { name: 'Not finished' }).click()
    await expect(page.getByTestId('finish-mark')).toBeVisible()
    expect((await entries(page)).length).toBe(0)
  })

  test('takes a day in the past, and refuses one in the future', async ({ page }) => {
    const work = shelf['Zeta']!
    await page.goto(`/work/${work.id}`)
    await page.getByTestId('finish-other').click()
    const future = new Date(Date.now() + 5 * 86_400_000).toISOString().slice(0, 10)
    expect(await page.getByTestId('finish-date').getAttribute('max')).not.toBe(future)
    await page.getByTestId('finish-date').fill('2022-07-09')
    await page.getByTestId('finish-date-ok').click()
    await expect(page.getByTestId('finish-done')).toContainText('July 9, 2022')
    const refused = await page.request.post(`/api/v1/editions/${edition(work)}/finished`, { headers: TAB, data: { finished_on: future } })
    expect(refused.status()).toBe(422)
  })

  test('is a button of the reader, which enters the book too', async ({ page }) => {
    const work = shelf['Delta Station']!
    await page.goto(`/read/${edition(work)}`)
    const button = page.getByTestId('reader-finish')
    await expect(button).toBeVisible({ timeout: 30_000 })
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
    expect((await entries(page)).filter((e) => e.edition_id === edition(work))).toHaveLength(1)
  })

  test('is a button of the big player, which enters the recording', async ({ page }) => {
    const work = shelf['Alpha']!
    await page.goto(`/work/${work.id}`)
    await page.getByTestId('listen').click()
    const button = page.getByTestId('player-finish')
    await expect(button).toBeVisible()
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
    expect((await entries(page)).filter((e) => e.edition_id === edition(work, 'audio'))).toHaveLength(1)
  })
})

test.describe('the list', () => {
  test('puts the rows in order by a column, keeps the choice across a reload, and shows the matching to the one who looks after the library', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf', library_section: 'books', list_sort: 'shelf', list_dir: 'asc' } })
    await page.goto('/')
    await page.locator('#look [data-look="list"]').click()
    await expect(page.locator('[data-testid="list"] tbody tr[data-work]').first()).toBeVisible()
    const titles = () => page.locator('[data-testid="list"] tbody tr[data-work] .lt-title span').allTextContents()
    await page.locator('thead button[data-sort="title"]').click()
    const ascending = await titles()
    expect(ascending).toEqual([...ascending].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base', numeric: true })))
    await expect(page.locator('thead th.col-title')).toHaveAttribute('aria-sort', 'ascending')
    await page.locator('thead button[data-sort="title"]').click()
    expect(await titles()).toEqual([...ascending].reverse())
    await page.reload()
    await expect(page.locator('thead th.col-title')).toHaveAttribute('aria-sort', 'descending')
    expect(await titles()).toEqual([...ascending].reverse())
    // The person created these libraries: they look after them, so the matching and the library are there.
    await expect(page.locator('[data-testid="list-match"]').first()).toBeVisible()
    await expect(page.locator('thead')).toContainText('Matching')
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf' } })
  })

  test('holds on a phone with the title, the author and where the person is, and no sideways scrolling', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'list', library_section: 'books', list_sort: 'shelf', list_dir: 'asc' } })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await expect(page.locator('[data-testid="list"] tbody tr[data-work]').first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
    await expect(page.locator('.list-table .col-author').first()).toBeHidden()
    await expect(page.locator('.list-table .col-mine').first()).toBeVisible()
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf' } })
  })

  test('shows the chips of the editions whole at the width of a laptop, with the columns of the one who looks after the library', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'list', library_section: 'books', list_sort: 'shelf', list_dir: 'asc' } })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')
    await expect(page.locator('[data-testid="list"] tbody tr[data-work]').first()).toBeVisible()
    await expect(page.locator('[data-testid="list-match"]').first()).toBeVisible()
    const cut = await page.evaluate(() => {
      const found: string[] = []
      for (const row of document.querySelectorAll('.list-table tbody tr[data-work]')) {
        const box = row.getBoundingClientRect()
        const cell = row.querySelector('.col-editions')?.getBoundingClientRect()
        for (const chip of row.querySelectorAll('.chip')) {
          const at = chip.getBoundingClientRect()
          const text = chip.querySelector('.chip-text') as HTMLElement | null
          if (cell && at.right > cell.right + 1) found.push(`outside: ${chip.textContent}`)
          if (at.bottom > box.bottom + 1 || at.top < box.top - 1) found.push(`over the row: ${chip.textContent}`)
          if (text && text.scrollWidth > text.clientWidth + 1) found.push(`cut: ${chip.textContent}`)
        }
      }
      return found
    })
    expect(cut).toEqual([])
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf' } })
  })

  test('opens a book when a row is pressed', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'list', library_section: 'books' } })
    await page.goto('/')
    await page.locator('[data-testid="list"] tbody tr[data-work]').first().click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf' } })
  })
})

test.describe('the authors', () => {
  test('stand as round photos from the authors folders, a circle with initials for the others, and lead to their page', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf', library_section: 'authors', authors_sort: 'name' } })
    await page.goto('/')
    await expect(page.locator('.author').first()).toBeVisible()
    const ada = page.locator('.author', { hasText: 'Ada Feldkamp' })
    await expect(ada.locator('.counts')).toContainText('3 works')
    await expect(ada.locator('.counts')).toContainText('1 audiobook')
    // The photo in the folder really arrived as a picture.
    await expect.poll(async () => ada.locator('img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
    await expect(page.locator('.author', { hasText: 'Ben Hollister' }).locator('img')).toBeVisible()
    await expect(page.locator('.author', { hasText: 'Cy Dorn' }).getByTestId('author-initials')).toHaveText('CD')
    // Round: the photo is as wide as it is high and its corners are half of it.
    const box = await ada.locator('.a-photo').evaluate((photo) => ({ width: photo.getBoundingClientRect().width, height: photo.getBoundingClientRect().height, radius: getComputedStyle(photo).borderTopLeftRadius }))
    expect(Math.round(box.width)).toBe(Math.round(box.height))
    expect(box.radius).toMatch(/50%|\d+px/)
    await page.getByTestId('author-search').fill('hollis')
    await expect(page.locator('.author')).toHaveCount(1)
    await page.getByTestId('author-search').fill('')
    await ada.click()
    await expect(page.getByTestId('author-hero')).toContainText('Ada Feldkamp')
    await expect(page.getByTestId('author-hero').locator('img')).toBeVisible()
    // A photo of the library's own has no credit line, and the series stand as a small shelf.
    await expect(page.getByTestId('photo-credit')).toHaveCount(0)
    await expect(page.locator('[data-section="s:Harbour Days"] [data-testid="work"]')).toHaveCount(3)
    await expect(page.locator('[data-section="s:Harbour Days"] h2')).toContainText('Harbour Days')
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { library_section: 'books' } })
  })

  test('keep to the width of a phone', async ({ page }) => {
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { library_section: 'authors' } })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await expect(page.locator('.author').first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
    await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { library_section: 'books' } })
  })
})

test.describe('tidying', () => {
  test('is off from the start, moves the files as the preview said, and puts them back', async ({ page }) => {
    const tidy = await library(page, 'Tidy', 'tidy', 4)
    const root = path.join(process.env.E2E_BOOKS!, 'authors', 'tidy')
    const where = (relative: string) => path.join(root, ...relative.split('/'))
    expect((await (await page.request.get('/api/v1/organize/settings')).json()) as { enabled: boolean }).toEqual({ enabled: false })
    await page.goto('/settings?tab=organize')
    const toggle = page.getByRole('switch', { name: 'Offer tidying' })
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await expect(page.locator('#organize-run')).toHaveCount(0)
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    await page.locator('#organize-run select').selectOption(String(tidy))
    await expect(page.getByTestId('org-writable')).toContainText('nexbooks may write')
    await page.getByRole('button', { name: 'Preview' }).click()
    await expect(page.getByTestId('org-summary')).toContainText('4 books will move, 8 files')
    const planned = page.getByTestId('org-planned')
    await expect(planned).toContainText('Mara Lindqvist/Orchard Days/01 - The Quiet Orchard (2011).epub')
    await expect(planned).toContainText('Mara Lindqvist/Orchard Days/02.5 - The Loud Orchard (2013).epub')
    await expect(planned).toContainText('Ben Hollister/Seven Bridges/01.mp3')
    // The preview wrote nothing.
    expect(fs.existsSync(where('Mara Lindqvist - The Quiet Orchard.epub'))).toBe(true)
    expect(fs.existsSync(where('Mara Lindqvist'))).toBe(false)
    // The run asks first, and Cancel starts nothing.
    await page.getByRole('button', { name: 'Start tidying' }).click()
    const ask = page.getByRole('dialog', { name: 'Tidy this library?' })
    await expect(ask).toContainText('Nothing is overwritten or deleted')
    await ask.getByRole('button', { name: 'Cancel' }).click()
    expect(fs.existsSync(where('Mara Lindqvist'))).toBe(false)
    await page.getByRole('button', { name: 'Start tidying' }).click()
    await page.getByRole('dialog', { name: 'Tidy this library?' }).getByRole('button', { name: 'Tidy' }).click()
    await expect(page.getByTestId('org-run')).toContainText('Done:', { timeout: 60_000 })
    expect(fs.existsSync(where('Mara Lindqvist/Orchard Days/01 - The Quiet Orchard (2011).epub'))).toBe(true)
    expect(fs.existsSync(where('Mara Lindqvist/Orchard Days/01 - The Quiet Orchard (2011).pdf'))).toBe(true)
    expect(fs.existsSync(where('Mara Lindqvist/Orchard Days/cover.jpg'))).toBe(true)
    expect(fs.existsSync(where('Ben Hollister/Seven Bridges/02.mp3'))).toBe(true)
    expect(fs.existsSync(where('Mara Lindqvist - The Quiet Orchard.epub'))).toBe(false)
    expect(fs.existsSync(where('single'))).toBe(false)
    // The books are still the same books: the shelf has them all and the next reading finds nothing new.
    await expect
      .poll(async () => ((await (await page.request.get('/api/v1/works/stats', { params: { library: tidy } })).json()) as { works: number }).works, { timeout: 30_000 })
      .toBe(4)
    // Back.
    await page.getByRole('button', { name: 'Undo' }).click()
    await page.getByRole('dialog', { name: 'Undo the tidying?' }).getByRole('button', { name: 'Put back' }).click()
    await expect(page.getByTestId('org-run')).toContainText('Put back: everything lies where it lay.', { timeout: 60_000 })
    expect(fs.existsSync(where('Mara Lindqvist - The Quiet Orchard.epub'))).toBe(true)
    expect(fs.existsSync(where('Second Orchard/second.epub'))).toBe(true)
    expect(fs.existsSync(where('Audio/Seven Bridges/01.mp3'))).toBe(true)
    expect(fs.existsSync(where('Mara Lindqvist'))).toBe(false)
  })
})
