/**
 * E-readers in a real browser (headless) against the real backend with invented books: a connection for a reading app is
 * made on the account page, its secret is shown once and opens the catalogue under /opds for a program that is no browser
 * (and the password of the account does not); "Send to Kindle" on a book, on a server that cannot mail, opens Amazon's
 * page in a new tab (here answered by the test, nothing reaches the internet) and downloads the file; and a connection for
 * the sync of KOReader is made on the account page, the plugin's calls (made here by hand, with the checksum KOReader makes
 * of the file) report a place that the reader then opens at, and a place read in the browser comes back as a place KOReader
 * can go to. Against a real KOReader this has not been tried. A connection for a Kobo is made on the account page and shown
 * once as an address that holds its secret; "To the Kobo" on a book puts it on the list; a device that is no browser (the
 * calls a Kobo makes, written out by hand) is handed addresses that are all nexbooks, is told about the book, fetches its
 * file, reports a place the browser then opens at, and is told the book is gone when it is taken off. Against a real Kobo
 * this has not been tried.
 */
import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

// The tab of the devices also holds the card of Web Push; with a service worker allowed, the WebKit that Playwright brings
// stops drawing it (see pairing.spec.ts).
test.use({ serviceWorkers: 'block' })

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-ereaders' }

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(6)
}

async function forgetConnections(page: Page): Promise<void> {
  const state = (await (await page.request.get('/api/v1/ereaders')).json()) as { links: { id: number; name: string }[] }
  for (const link of state.links) if (link.name.startsWith('E2E ')) await page.request.delete(`/api/v1/ereaders/links/${link.id}`, { headers: TAB })
}

test.beforeEach(async ({ page }) => {
  await ensureLibrary(page)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
  expect((await page.request.put('/api/v1/settings', { headers: TAB, data: { ereaders_allowed: true } })).ok()).toBe(true)
  await forgetConnections(page)
})

test.afterEach(async ({ page }) => {
  await forgetConnections(page)
})

test('a connection is made, shown once, and opens the catalogue for a reading app only', async ({ page, playwright, baseURL }) => {
  await page.goto('/account?tab=devices')
  await expect(page.getByRole('heading', { name: 'E-readers' })).toBeVisible()
  await page.getByRole('button', { name: 'Connect: Catalogue for reading apps (OPDS)' }).click()
  await page.getByRole('button', { name: 'Make a connection' }).click()
  await page.getByLabel('Name of the connection').fill('E2E Living room')
  await page.getByRole('button', { name: 'Make', exact: true }).click()
  const shown = page.getByTestId('ereader-shown')
  await expect(shown).toBeVisible()
  const values = await shown.locator('input').evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))
  expect(values).toHaveLength(3)
  const [address, user, secret] = values
  expect(address).toMatch(/\/opds$/)
  expect(user).toBe('tester')
  expect(secret).toMatch(/^nbo_[A-Za-z0-9_-]{32}$/)
  await shots(page, 'ereaders-shown')
  await shown.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('ereader-shown')).toHaveCount(0)
  // Afterwards only the first characters stand on the page.
  await expect(page.getByTestId('ereader-link')).toContainText(secret.slice(0, 8))
  expect(await page.content()).not.toContain(secret)

  // The reading app: no cookie, no tab header, the name and the secret.
  const app = await playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
  const basic = (name: string, password: string) => ({ Authorization: 'Basic ' + Buffer.from(`${name}:${password}`).toString('base64') })
  const none = await app.get('/opds')
  expect(none.status()).toBe(401)
  expect(none.headers()['www-authenticate']).toContain('Basic')
  // The password of the account opens nothing here.
  expect((await app.get('/opds', { headers: basic('tester', 'e2e test operator password') })).status()).toBe(401)
  const start = await app.get('/opds', { headers: basic(user, secret) })
  expect(start.status()).toBe(200)
  expect(start.headers()['content-type']).toContain('application/atom+xml')
  expect(await start.text()).toContain('<feed')
  const news = await (await app.get('/opds/new', { headers: basic(user, secret) })).text()
  expect(news).toContain('Der Leuchtturmwaerter')
  // The recording alone is not in the catalogue.
  expect(news).not.toContain('Stimmen im Moor')
  const href = /href="(\/opds\/files\/\d+\/\d+\.epub)"/.exec(news)![1]
  const file = await app.get(href, { headers: basic(user, secret) })
  expect(file.status()).toBe(200)
  expect((await file.body()).subarray(0, 2).toString()).toBe('PK')
  await app.dispose()

  // The operator switches it off: the secret stops at once.
  expect((await page.request.put('/api/v1/settings', { headers: TAB, data: { ereaders_allowed: false } })).ok()).toBe(true)
  const off = await playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
  expect((await off.get('/opds', { headers: basic(user, secret) })).status()).toBe(403)
  await off.dispose()
})

test('"Send to Kindle" on a server that cannot mail downloads the file and opens the page of Amazon', async ({ page, context }) => {
  // Nothing reaches the internet: the page of Amazon is answered here.
  await context.route('https://www.amazon.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Send to Kindle</title>' }))
  const works = (await (await page.request.get('/api/v1/works?per_page=100')).json()) as { items: { id: number; title: string }[] }
  const book = works.items.find((item) => item.title === 'Der Leuchtturmwaerter')!
  await page.goto(`/work/${book.id}`)
  const button = page.getByTestId('send-to-kindle')
  await expect(button).toBeVisible()
  await shots(page, 'ereaders-kindle')
  const popup = page.waitForEvent('popup')
  const download = page.waitForEvent('download')
  await button.click()
  const opened = await popup
  expect(opened.url()).toBe('https://www.amazon.com/sendtokindle')
  const file = await download
  expect(file.url()).toMatch(/\/api\/v1\/files\/\d+\/download$/)
  expect(file.suggestedFilename()).toMatch(/\.epub$/)
  await expect(page.getByTestId('kindle-said')).toContainText('Drop the file there.')
  // The recording alone has no such button.
  const audio = works.items.find((item) => item.title === 'Stimmen im Moor')!
  await page.goto(`/work/${audio.id}`)
  await expect(page.getByTestId('twin').first()).toBeVisible()
  await expect(page.getByTestId('send-to-kindle')).toHaveCount(0)
})

/** The checksum KOReader names a file by: the MD5 of up to 1 KiB at each of the offsets 0, 1 KiB, 4 KiB ... 1 GiB. */
function partialMd5(data: Buffer): string {
  const hash = createHash('md5')
  for (const offset of [0, ...Array.from({ length: 11 }, (_, step) => 1024 * 4 ** step)]) {
    const piece = data.subarray(offset, offset + 1024)
    if (piece.length === 0) break
    hash.update(piece)
  }
  return hash.digest('hex')
}

test('a connection for the sync of KOReader takes a report that the reader opens at, and gives the place read in the browser back', async ({ page, playwright, baseURL }) => {
  await page.goto('/account?tab=devices')
  await page.getByRole('button', { name: 'Connect: Reading progress for KOReader' }).click()
  await page.getByRole('button', { name: 'Make a connection' }).click()
  await page.getByLabel('Name of the connection').fill('E2E Kobo')
  await page.getByRole('button', { name: 'Make', exact: true }).click()
  const shown = page.getByTestId('ereader-shown')
  await expect(shown).toBeVisible()
  const [address, user, secret] = await shown.locator('input').evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))
  expect(address).toMatch(/\/kosync$/)
  expect(user).toBe('tester')
  expect(secret).toMatch(/^nbs_[A-Za-z0-9_-]{32}$/)
  await expect(shown).toContainText('Custom sync server')
  await shots(page, 'ereaders-koreader')
  await shown.getByRole('button', { name: 'Done' }).click()

  // The book: a real EPUB (a copy of one of the reader's books, in a library of its own that is taken away again), and the
  // checksum KOReader would make of its file.
  const folder = path.join(process.env.E2E_BOOKS!, 'koreader')
  fs.mkdirSync(folder, { recursive: true })
  const copy = path.join(folder, 'the-long-road.epub')
  fs.copyFileSync(path.join(process.env.E2E_BOOKS!, 'reading', 'the-long-road.epub'), copy)
  const hourAgo = new Date(Date.now() - 3600_000)
  fs.utimesSync(copy, hourAgo, hourAgo)
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'KOReader', path: folder, kind: 'ebook', scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  const library = ((await made.json()) as { id: number }).id
  try {
    await expect
      .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${library}&per_page=10`)).json()) as { total: number }).total, { timeout: 60_000, intervals: [500] })
      .toBe(1)
    const works = (await (await page.request.get(`/api/v1/works?library=${library}&per_page=10`)).json()) as { items: { id: number }[] }
    const detail = (await (await page.request.get(`/api/v1/works/${works.items[0].id}`)).json()) as { editions: { id: number; kind: string; files: { id: number; format: string }[] }[] }
    const edition = detail.editions.find((item) => item.kind === 'ebook')!
    const file = edition.files.find((item) => item.format === 'epub')!
    const document = partialMd5(fs.readFileSync(copy))
    expect(Buffer.from(await (await page.request.get(`/api/v1/files/${file.id}/download`)).body()).equals(fs.readFileSync(copy))).toBe(true)
    // The plugin: no cookie, no tab header, the name and the MD5 of the password.
    const plugin = await playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
    const key = createHash('md5').update(secret).digest('hex')
    const headers = { 'x-auth-user': user, 'x-auth-key': key, accept: 'application/vnd.koreader.v1+json' }
    expect((await plugin.get('/kosync/users/auth', { headers })).status()).toBe(200)
    expect((await plugin.get('/kosync/users/auth', { headers: { ...headers, 'x-auth-key': createHash('md5').update('wrong').digest('hex') } })).status()).toBe(401)
    const register = await plugin.post('/kosync/users/create', { data: { username: 'newcomer', password: key } })
    expect(register.status()).toBe(402)
    expect(((await register.json()) as { message: string }).message).toContain('Accounts are made in nexbooks')
    const pointer = '/body/DocFragment[2]/body/div/p[3]/text().0'
    const sent = await plugin.put('/kosync/syncs/progress', { headers, data: { document, progress: pointer, percentage: 0.5, device: 'Kobo Libra 2', device_id: 'e2e0kobo' } })
    expect(sent.status()).toBe(200)
    const back = (await (await plugin.get(`/kosync/syncs/progress/${document}`, { headers })).json()) as Record<string, unknown>
    expect(back).toMatchObject({ document, progress: pointer, percentage: 0.5, device: 'Kobo Libra 2', device_id: 'e2e0kobo' })
    const mine = (await (await page.request.get(`/api/v1/editions/${edition.id}/progress`)).json()) as { locator: string; fraction: number; device: string }
    expect(mine).toMatchObject({ locator: '', fraction: 0.5, device: 'Kobo Libra 2' })

    // The browser has no place of its own for this book: it opens where KOReader was, by the share of the book.
    await page.goto(`/read/${edition.id}`)
    await expect(page.locator('[data-testid="reader"][data-phase="open"]')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('[data-testid="reader-where"]')).not.toHaveText('', { timeout: 30_000 })
    const opened = Number(await page.locator('[data-testid="reader"]').getAttribute('data-fraction'))
    expect(opened).toBeGreaterThan(0.3)
    expect(opened).toBeLessThan(0.7)
    await shots(page, 'ereaders-koreader-reader')

    // The browser reads on; the place goes to the server when the reader is left, and KOReader is told the part of the book.
    await page.waitForTimeout(500)
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => Number(await page.locator('[data-testid="reader"]').getAttribute('data-fraction'))).toBeGreaterThan(opened)
    await page.waitForTimeout(500)
    await page.locator('[data-testid="reader-back"]').click()
    await expect
      .poll(async () => ((await (await plugin.get(`/kosync/syncs/progress/${document}`, { headers })).json()) as { device?: string }).device ?? '', { timeout: 15_000 })
      .not.toBe('Kobo Libra 2')
    const told = (await (await plugin.get(`/kosync/syncs/progress/${document}`, { headers })).json()) as { progress: string; percentage: number }
    expect(told.progress).toMatch(/^\/body\/DocFragment\[\d+\]$/)
    expect(told.percentage).toBeGreaterThan(0.3)
    await plugin.dispose()
  } finally {
    await page.request.delete(`/api/v1/libraries/${library}`, { headers: TAB })
    fs.rmSync(folder, { recursive: true, force: true })
  }
})

test('a connection for a Kobo is made, a book is put on the list with the button, and a device that is no browser fetches it', async ({ page, playwright, baseURL }) => {
  await page.goto('/account?tab=devices')
  await page.getByRole('button', { name: 'Connect: Kobo' }).click()
  await page.getByRole('button', { name: 'Make a connection' }).click()
  await page.getByLabel('Name of the connection').fill('E2E Kobo Libra')
  await page.getByRole('button', { name: 'Make', exact: true }).click()
  const shown = page.getByTestId('ereader-shown')
  await expect(shown).toBeVisible()
  const [address, line] = await shown.locator('input').evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))
  expect(address).toMatch(/\/kobo\/nbe_[A-Za-z0-9_-]{32}$/)
  expect(line).toBe(`api_endpoint=${address}`)
  await expect(shown).toContainText('[OneStoreServices]')
  await shots(page, 'ereaders-kobo')
  await shown.getByRole('button', { name: 'Done' }).click()
  const root = new URL(address).pathname
  const secret = root.split('/')[2]
  expect(await page.content()).not.toContain(secret)

  // The book: the button puts it on the list (nothing is pushed anywhere), and says so.
  const works = (await (await page.request.get('/api/v1/works?per_page=100')).json()) as { items: { id: number; title: string }[] }
  const bookWork = works.items.find((item) => item.title === 'Der Leuchtturmwaerter')!
  await page.goto(`/work/${bookWork.id}`)
  const button = page.getByTestId('send-to-kobo')
  await expect(button).toBeVisible()
  await expect(button).toHaveText('To the Kobo')
  await button.click()
  await expect(page.getByTestId('kobo-said')).toContainText('The Kobo fetches the book at its next sync.')
  await expect(button).toHaveText('Take off the Kobo')
  await shots(page, 'ereaders-kobo-button')
  // The recording alone has no such button.
  const audio = works.items.find((item) => item.title === 'Stimmen im Moor')!
  await page.goto(`/work/${audio.id}`)
  await expect(page.getByTestId('twin').first()).toBeVisible()
  await expect(page.getByTestId('send-to-kobo')).toHaveCount(0)

  // The Kobo: no cookie, no tab header, only the address.
  const kobo = await playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
  expect((await kobo.get(`/kobo/nbe_${'x'.repeat(32)}/v1/initialization`)).status()).toBe(401)
  const init = (await (await kobo.get(`${root}/v1/initialization`)).json()) as { Resources: Record<string, unknown> }
  expect(init.Resources.library_sync).toContain(root)
  expect(JSON.stringify(init.Resources)).not.toContain('storeapi.kobo.com')
  const first = await kobo.get(`${root}/v1/library/sync`)
  expect(first.status()).toBe(200)
  const entries = (await first.json()) as { NewEntitlement: { BookEntitlement: { Id: string }; BookMetadata: { Title: string; DownloadUrls: { Url: string }[] } } }[]
  expect(entries.map((entry) => entry.NewEntitlement.BookMetadata.Title)).toEqual(['Der Leuchtturmwaerter'])
  const name = entries[0].NewEntitlement.BookEntitlement.Id
  const token = first.headers()['x-kobo-synctoken']
  expect(first.headers()['x-kobo-sync']).toBeUndefined()
  const fileAddress = new URL(entries[0].NewEntitlement.BookMetadata.DownloadUrls[0].Url).pathname
  const file = await kobo.get(fileAddress)
  expect(file.status()).toBe(200)
  expect((await file.body()).subarray(0, 2).toString()).toBe('PK')
  const cover = await kobo.get(`${root}/image/${name}/200/300/false/image.jpg`)
  expect([200, 404]).toContain(cover.status())
  if (cover.status() === 200) expect(cover.headers()['content-type']).toBe('image/jpeg')
  // Taken: nothing more is told.
  expect(((await (await kobo.get(`${root}/v1/library/sync`, { headers: { 'x-kobo-synctoken': token } })).json()) as unknown[]).length).toBe(0)

  // The place read on the Kobo is the place the browser opens at.
  const state = { ReadingStates: [{ EntitlementId: name, CurrentBookmark: { ProgressPercent: 50, ContentSourceProgressPercent: 10, Location: { Value: 'kobo.3.1', Type: 'KoboSpan', Source: 'OEBPS/ch3.xhtml' } }, StatusInfo: { Status: 'Reading' } }] }
  expect((await kobo.put(`${root}/v1/library/${name}/state`, { data: state })).status()).toBe(200)
  const detail = (await (await page.request.get(`/api/v1/works/${bookWork.id}`)).json()) as { editions: { id: number; kind: string }[] }
  const edition = detail.editions.find((item) => item.kind === 'ebook')!
  const mine = (await (await page.request.get(`/api/v1/editions/${edition.id}/progress`)).json()) as { locator: string; fraction: number; device: string }
  expect(mine).toMatchObject({ locator: '', fraction: 0.5, device: 'E2E Kobo Libra' })
  const back = (await (await kobo.get(`${root}/v1/library/${name}/state`)).json()) as { CurrentBookmark: { ProgressPercent: number; Location: { Value: string } } }[]
  expect(back[0].CurrentBookmark.ProgressPercent).toBe(50)
  expect(back[0].CurrentBookmark.Location.Value).toBe('kobo.3.1')

  // Taking the book off asks first; then the Kobo is told it is gone.
  await page.goto(`/work/${bookWork.id}`)
  await page.getByTestId('send-to-kobo').click()
  await expect(page.getByRole('dialog', { name: 'Take it off the Kobo?' })).toContainText('You can put it on the Kobo again at any time.')
  expect(((await (await kobo.get(`${root}/v1/library/sync`, { headers: { 'x-kobo-synctoken': token } })).json()) as unknown[]).length).toBe(0)
  await page.getByRole('dialog', { name: 'Take it off the Kobo?' }).getByRole('button', { name: 'Take off the Kobo' }).click()
  await expect(page.getByTestId('kobo-said')).toContainText('The Kobo removes the book at its next sync.')
  const gone = (await (await kobo.get(`${root}/v1/library/sync`, { headers: { 'x-kobo-synctoken': token } })).json()) as { ChangedEntitlement: { BookEntitlement: { Id: string; IsRemoved: boolean } } }[]
  expect(gone.map((entry) => [entry.ChangedEntitlement.BookEntitlement.Id, entry.ChangedEntitlement.BookEntitlement.IsRemoved])).toEqual([[name, true]])
  expect((await kobo.get(`${root}/v1/library/${name}/metadata`)).status()).toBe(404)
  expect((await kobo.get(fileAddress)).status()).toBe(404)

  // The operator's switch stops it at once.
  expect((await page.request.put('/api/v1/settings', { headers: TAB, data: { ereaders_allowed: false } })).ok()).toBe(true)
  expect((await kobo.get(`${root}/v1/initialization`)).status()).toBe(403)
  await kobo.dispose()
})
