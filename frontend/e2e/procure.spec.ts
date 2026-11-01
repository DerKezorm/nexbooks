/**
 * Fetching books (settings, Acquisition) in a real browser, headless, against the real backend and stand-ins for the far end
 * (`e2e/fake-procure.mjs`: an indexer, Prowlarr, SABnzbd, qBittorrent on 127.0.0.1). What the unit tests cannot see: that the way
 * out really reaches a service on the own network (private and loopback addresses are allowed on purpose), that the Prowlarr
 * sync runs as a job of the queue and its indexers appear on the page, that the keys the operator typed never come back in an
 * answer, that a change of the formats survives a reload, and that none of the pages is wider than a phone.
 */
import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-procure0' }
const FAR = process.env.E2E_PROCURE!

type Keys = { indexer: string; prowlarr: string; sab: string; qbt: string }
let keys: Keys

test.describe.configure({ mode: 'serial' })

test.beforeAll(async ({ playwright }) => {
  const context = await playwright.request.newContext()
  keys = (await (await context.get(`${FAR}/__keys`)).json()) as Keys
  await context.dispose()
})

/** Everything the tests made is taken away again, so that the next browser starts from the same page. */
test.afterEach(async ({ page }) => {
  const get = async (path: string) => (await page.request.get(path)).json()
  for (const item of ((await get('/api/v1/procure/indexers')) as { indexers: { id: number; via_prowlarr: boolean }[] }).indexers) {
    if (!item.via_prowlarr) await page.request.delete(`/api/v1/procure/indexers/${item.id}`, { headers: TAB })
  }
  if (((await get('/api/v1/procure/prowlarr')) as { connected: boolean }).connected) await page.request.delete('/api/v1/procure/prowlarr', { headers: TAB })
  for (const item of ((await get('/api/v1/procure/clients')) as { clients: { id: number }[] }).clients) await page.request.delete(`/api/v1/procure/clients/${item.id}`, { headers: TAB })
})

async function open(page: Page, sub = ''): Promise<void> {
  await page.goto(`/settings?tab=procure${sub ? `&sub=${sub}` : ''}`)
  await expect(page.getByRole('tab', { name: 'Acquisition' })).toBeVisible()
}

const dialog = (page: Page) => page.getByRole('dialog')
const field = (page: Page, label: string) => dialog(page).locator('label', { has: page.locator('span', { hasText: new RegExp(`^${label}$`) }) }).locator('input')

test.describe('the part of the settings', () => {
  test('has the five tabs of the design, the indexers first, and the tab is in the address', async ({ page }) => {
    await open(page)
    await expect(page.getByRole('tab', { name: 'Indexers' })).toHaveAttribute('aria-selected', 'true')
    for (const name of ['Download programs', 'Formats and place', 'Automation', 'Rights']) await expect(page.getByRole('tab', { name })).toBeVisible()
    await page.getByRole('tab', { name: 'Download programs' }).click()
    await expect(page).toHaveURL(/tab=procure&sub=clients/)
    await page.reload()
    await expect(page.getByRole('tab', { name: 'Download programs' })).toHaveAttribute('aria-selected', 'true')
  })
})

test.describe('an indexer', () => {
  test('is tested, offered its book categories, saved, and its key never comes back', async ({ page }) => {
    await open(page)
    await page.locator('[data-testid="indexer-add"]').click()
    await field(page, 'Name').fill('Library One')
    await field(page, 'Address').fill(`${FAR}/indexer/api`)
    await field(page, 'API key').fill(keys.indexer)
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="indexer-test"]')).toContainText('Connected. The indexer can search books (q, author, isbn) and lists 8 categories.')
    // The languages books are fetched in (German and English from the start): the mixed and the German categories are
    // chosen, the French one and the comics are offered but not chosen.
    const boxes = dialog(page).locator('[data-testid="indexer-categories"] input[type="checkbox"]')
    await expect(boxes).toHaveCount(6)
    const chosen = await boxes.evaluateAll((all) => all.map((box) => (box as HTMLInputElement).checked))
    expect(chosen.filter(Boolean)).toHaveLength(4)
    await dialog(page).getByRole('button', { name: 'Add indexer' }).click()
    const card = page.locator('[data-testid^="indexer-"]', { hasText: 'Library One' }).first()
    await expect(card).toContainText('Newznab')
    await expect(card).toContainText('ready')
    await expect(card).toContainText('Books/EBook')
    await expect(card).not.toContainText(keys.indexer)
    const listed = await (await page.request.get('/api/v1/procure/indexers')).text()
    expect(listed).not.toContain(keys.indexer)
    const stored = (await (await page.request.get('/api/v1/procure/indexers')).json()) as { indexers: { key_set: boolean; supported_params: { book: string[] } }[] }
    expect(stored.indexers[0].key_set).toBe(true)
    expect(stored.indexers[0].supported_params.book).toEqual(['q', 'author', 'isbn'])
  })

  test('says in words that the key is refused, and saves nothing', async ({ page }) => {
    await open(page)
    await page.locator('[data-testid="indexer-add"]').click()
    await field(page, 'Name').fill('Wrong key')
    await field(page, 'Address').fill(`${FAR}/indexer/api`)
    await field(page, 'API key').fill('0'.repeat(24))
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="indexer-test"]')).toContainText('The indexer refuses the API key.')
    await expect(dialog(page).locator('[data-testid="indexer-categories"]')).toHaveCount(0)
    await dialog(page).getByRole('button', { name: 'Add indexer' }).click()
    await expect(page.locator('[data-testid="indexer-local-problem"]')).toHaveText('Test the connection first.')
  })

  test('asks before it is removed', async ({ page }) => {
    const made = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'To remove', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer } })
    expect(made.ok(), await made.text()).toBe(true)
    await open(page)
    await page.getByRole('button', { name: 'Remove indexer', exact: true }).click()
    await expect(dialog(page)).toContainText('Books already loaded and the history stay.')
    await dialog(page).getByRole('button', { name: 'Cancel' }).click()
    await expect(page.locator('[data-testid^="indexer-"]', { hasText: 'To remove' }).first()).toBeVisible()
    await page.getByRole('button', { name: 'Remove indexer', exact: true }).click()
    await dialog(page).getByRole('button', { name: 'Remove indexer' }).click()
    await expect(page.locator('[data-testid^="indexer-"]', { hasText: 'To remove' })).toHaveCount(0)
  })
})

test.describe('Prowlarr', () => {
  test('is connected, read by the job of the queue, and its book indexers appear on the page', async ({ page }) => {
    await open(page)
    await page.getByRole('button', { name: 'Connect Prowlarr' }).click()
    await dialog(page).getByPlaceholder('http://prowlarr.example.com:9696').fill(`${FAR}/prowlarr`)
    await dialog(page).locator('input[type="password"]').fill(keys.prowlarr)
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="prowlarr-test"]')).toContainText('Connected, 2 indexers, 1 of them with book categories, version 2.6.5.5623.')
    await dialog(page).getByRole('button', { name: 'Save' }).click()
    await expect(page.locator('[data-testid="prowlarr-state"]')).toHaveText('connected')
    // The sync is a job: the page asks again while it waits or runs, and the indexer arrives by itself.
    const card = page.locator('[data-testid^="indexer-"]', { hasText: 'Example Books Usenet' }).first()
    await expect(card).toBeVisible({ timeout: 30_000 })
    await expect(card).toContainText('via Prowlarr')
    await expect(page.locator('[data-testid="prowlarr-line"]')).toContainText('1 indexer taken · 1 left out for lack of book categories')
    await expect(page.getByRole('button', { name: 'You remove an indexer from Prowlarr in Prowlarr' })).toBeDisabled()
    expect(await (await page.request.get('/api/v1/procure/prowlarr')).text()).not.toContain(keys.prowlarr)
  })

  test('takes its indexers with it when it is let go, after the question', async ({ page }) => {
    const saved = await page.request.put('/api/v1/procure/prowlarr', { headers: TAB, data: { url: `${FAR}/prowlarr`, api_key: keys.prowlarr, auto_add: true } })
    expect(saved.ok(), await saved.text()).toBe(true)
    await open(page)
    await expect(page.locator('[data-testid^="indexer-"]', { hasText: 'Example Books Usenet' }).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Disconnect', exact: true }).click()
    await expect(dialog(page)).toContainText('The indexers taken from Prowlarr disappear from nexbooks.')
    await dialog(page).getByRole('button', { name: 'Disconnect' }).click()
    await expect(page.locator('[data-testid="prowlarr-state"]')).toHaveText('not connected')
    await expect(page.locator('[data-testid^="indexer-"]', { hasText: 'Example Books Usenet' })).toHaveCount(0)
  })
})

test.describe('the download programs', () => {
  test('SABnzbd is tested step by step, the category is made, and the program is saved', async ({ page }) => {
    await open(page, 'clients')
    await page.locator('[data-testid="client-add"]').click()
    await field(page, 'Address').fill(`${FAR}/sab`)
    await field(page, 'API key').fill(keys.sab)
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    const steps = dialog(page).locator('[data-testid="client-checks"] li')
    await expect(steps).toHaveCount(4)
    await expect(steps.nth(1)).toContainText('Version 4.5.1')
    await expect(steps.nth(3)).toContainText(/Category “nexbooks” (made|is there)/)
    await dialog(page).getByRole('button', { name: 'Add program' }).click()
    const card = page.locator('[data-testid^="client-"]', { hasText: 'SABnzbd' }).first()
    await expect(card).toContainText('ready')
    await expect(card).toContainText('Version 4.5.1 · category “nexbooks” made')
    expect(await (await page.request.get('/api/v1/procure/clients')).text()).not.toContain(keys.sab)
  })

  test('qBittorrent says that the login is refused and saves nothing', async ({ page }) => {
    await open(page, 'clients')
    await page.locator('[data-testid="client-add"]').click()
    await dialog(page).locator('select').selectOption('qbittorrent')
    await field(page, 'Address').fill(`${FAR}/qbt`)
    await field(page, 'User').fill('reader')
    await field(page, 'Password').fill('wrong password')
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="client-checks"]')).toContainText('The program refuses the login.')
    await expect(page.locator('[data-testid="client-checks"] li')).toHaveCount(1)
    expect(((await (await page.request.get('/api/v1/procure/clients')).json()) as { clients: unknown[] }).clients).toHaveLength(0)
  })

  test('qBittorrent with the right password is saved, and an address that is not a program is said to be none', async ({ page }) => {
    await open(page, 'clients')
    await page.locator('[data-testid="client-add"]').click()
    await dialog(page).locator('select').selectOption('qbittorrent')
    await field(page, 'Address').fill(`${FAR}/indexer`)
    await field(page, 'User').fill('reader')
    await field(page, 'Password').fill(keys.qbt)
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="client-checks"]')).toContainText('Something answers at this address, but not the chosen program.')
    await field(page, 'Address').fill(`${FAR}/qbt`)
    await dialog(page).getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('[data-testid="client-checks"] li')).toHaveCount(4)
    await dialog(page).getByRole('button', { name: 'Add program' }).click()
    await expect(page.locator('[data-testid^="client-"]', { hasText: 'qBittorrent' }).first()).toContainText('ready')
  })
})

test.describe('the formats', () => {
  test('are moved, ticked and saved at once, and stay after a reload', async ({ page }) => {
    await open(page, 'formats')
    await expect(page.locator('[data-testid="rank-ebook"] [data-testid^="rank-ebook-"] b').first()).toHaveText('EPUB')
    await page.getByRole('button', { name: 'Move AZW3 up' }).click()
    await expect(page.locator('[data-testid="formats-feedback"]')).toContainText('Saved.')
    await page.locator('input[aria-label="PDF allowed"]').check()
    await expect(page.locator('[data-testid="formats-feedback"]')).toContainText('Saved.')
    await page.reload()
    await expect(page.locator('[data-testid="rank-ebook"] [data-testid^="rank-ebook-"] b').first()).toHaveText('AZW3')
    await expect(page.locator('input[aria-label="PDF allowed"]')).toBeChecked()
    // Put back, so that the next browser starts from the same page.
    await page.getByRole('button', { name: 'Move EPUB up' }).click()
    await page.locator('input[aria-label="PDF allowed"]').uncheck()
    await expect(page.locator('[data-testid="formats-feedback"]')).toContainText('Saved.')
  })

  // The label of the goal stood under the line, right over the next format, and read as if the formats
  // below were good enough. Now one text stands above the line (good enough) and one below it (looked past).
  test('say above the goal line that it is good enough and below it that nexbooks looks on', async ({ page }) => {
    const formats = (await (await page.request.get('/api/v1/procure/formats')).json()) as Record<string, unknown> & { target: Record<string, string> }
    const keep = Object.fromEntries(Object.entries(formats).filter(([key]) => !['libraries', 'known_ebook', 'known_audio', 'known_languages', 'samples'].includes(key)))
    const goal = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: { ...keep, target: { ...formats.target, audio: 'mp3_320' } } })
    expect(goal.ok(), await goal.text()).toBe(true)
    try {
      await open(page, 'formats')
      const box = async (selector: string) => (await page.locator(selector).boundingBox())!
      const goalRow = await box('[data-testid="rank-audio-mp3_320"]')
      const above = await box('[data-testid="goal-above-audio"]')
      const rule = await box('[data-testid="goal-rule-audio"]')
      const below = await box('[data-testid="goal-below-audio"]')
      const next = await box('[data-testid="rank-audio-mp3_192"]')
      await expect(page.locator('[data-testid="goal-above-audio"]')).toHaveText('↑ good enough, goal reached')
      await expect(page.locator('[data-testid="goal-below-audio"]')).toHaveText('↓ below this nexbooks keeps looking for something better')
      // Top down: the goal, the text that it is good enough, the line, the text that nexbooks looks on, the next format.
      expect(goalRow.y + goalRow.height).toBeLessThanOrEqual(above.y + 0.5)
      expect(above.y + above.height).toBeLessThanOrEqual(rule.y + 0.5)
      expect(rule.y + rule.height).toBeLessThanOrEqual(below.y + 0.5)
      expect(below.y + below.height).toBeLessThanOrEqual(next.y + 0.5)
      await page.locator('#formats-audio').scrollIntoViewIfNeeded()
      await shots(page, 'ziel-linie', '#formats-audio')
    } finally {
      const back = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: keep })
      expect(back.ok(), await back.text()).toBe(true)
    }
  })

  test('refuse to leave nothing allowed', async ({ page }) => {
    const formats = (await (await page.request.get('/api/v1/procure/formats')).json()) as Record<string, unknown>
    const none = { ...formats, ebook: (formats.ebook as { f: string }[]).map((item) => ({ f: item.f, on: false })) }
    const refused = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: none })
    expect(refused.status()).toBe(422)
  })

  test('preview the naming of a made-up book as the scheme is typed', async ({ page }) => {
    await open(page, 'formats')
    const schema = page.locator('#formats-naming input').first()
    await schema.fill('{Author}/{Title} ({Year})')
    await expect(page.locator('[data-testid="preview-ebook"]')).toContainText('Ada Example/The Lantern Road (2019).epub')
    await schema.fill('../{Title}')
    await expect(page.locator('[data-testid="naming-problem"]')).toBeVisible()
    await schema.fill('{Author}/{Series}/{Volume} - {Title} ({Year})')
    await expect(page.locator('[data-testid="preview-ebook"]')).toContainText('Ada Example/Lantern Saga/02 - The Lantern Road (2019).epub')
  })
})

test.describe('the automation', () => {
  test('switches off, dims the rest, and keeps its state after a reload', async ({ page }) => {
    await open(page, 'automation')
    await page.locator('#auto-main [role="switch"]').click()
    await expect(page.locator('[data-testid="auto-rest"]')).toHaveAttribute('aria-disabled', 'true')
    await page.reload()
    await expect(page.locator('#auto-main [role="switch"]')).toHaveAttribute('aria-checked', 'false')
    await page.locator('#auto-main [role="switch"]').click()
    await expect(page.locator('[data-testid="auto-rest"]')).toHaveAttribute('aria-disabled', 'false')
  })
})

test.describe('the rights', () => {
  test('give a member the right to acquire in a library, and take the access away after the question', async ({ page, playwright }) => {
    const folder = path.join(process.env.E2E_BOOKS!, `rights-${test.info().project.name}`)
    fs.mkdirSync(folder, { recursive: true })
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: `Rights ${test.info().project.name}`, path: folder, kind: 'both', scan_enabled: false } })
    expect(made.ok(), await made.text()).toBe(true)
    const library = ((await made.json()) as { id: number }).id
    const invite = await page.request.post('/api/v1/invites', { data: { days: 1 }, headers: TAB })
    const token = new URL(((await invite.json()) as { link: string }).link).pathname.split('/').pop()!
    const outside = await playwright.request.newContext({ baseURL: test.info().project.use.baseURL })
    const name = `member${test.info().project.name}`
    const joined = await outside.post(`/api/v1/invite/${token}`, { data: { name, password: 'e2e member password long' }, headers: TAB })
    expect(joined.ok(), await joined.text()).toBe(true)
    await outside.dispose()
    try {
      await open(page, 'rights')
      await page.getByRole('tab', { name: `Rights ${test.info().project.name}` }).click()
      const row = page.locator(`[data-testid="right-${name}"]`)
      await expect(row.getByRole('radio', { name: 'Acquire' })).toHaveAttribute('aria-checked', 'false')
      await row.getByRole('radio', { name: 'Acquire' }).click()
      await expect(row.getByRole('radio', { name: 'Acquire' })).toHaveAttribute('aria-checked', 'true')
      const admin = (await (await page.request.get('/api/v1/libraries/admin')).json()) as { id: number; members: { name: string; right: string }[] }[]
      expect(admin.find((item) => item.id === library)!.members.find((member) => member.name === name)!.right).toBe('acquire')
      await row.getByRole('button', { name: new RegExp('Take away access') }).click()
      await expect(dialog(page)).toContainText('Progress and wish list stay.')
      await dialog(page).getByRole('button', { name: 'Take away access' }).click()
      await expect(row.getByRole('radio', { name: 'Acquire' })).toHaveAttribute('aria-checked', 'false')
    } finally {
      await page.request.delete(`/api/v1/libraries/${library}`, { headers: TAB })
    }
  })
})

test.describe('on a phone', () => {
  for (const sub of ['indexers', 'clients', 'formats', 'automation', 'rights']) {
    test(`the ${sub} page is not wider than the screen`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 })
      await open(page, sub === 'indexers' ? '' : sub)
      await expect(page.locator('.view h1').first()).toBeVisible()
      await page.waitForTimeout(300)
      const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
      expect(widths.scroll).toBeLessThanOrEqual(widths.client)
    })
  }
})
