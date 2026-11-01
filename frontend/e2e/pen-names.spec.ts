/**
 * Splitting another name off a person, in a real browser against the real backend with invented books: the page of the
 * author names the other name with a button, the question says that it cannot be taken back here, a cancelled question
 * sends nothing, and the answered one sends one request. Only the person's other name is made up (the invented shelf has
 * no pen name, and making one takes the sources); the rest is the server's own.
 */
import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-pen-names' }
const SHOTS = process.env.E2E_SHOTS ?? ''

// The answer about the person is made up by the test: no service worker may answer in its place.
test.use({ serviceWorkers: 'block' })

async function personWithBooks(page: Page): Promise<number> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(6)
  const works = (await (await page.request.get('/api/v1/works?per_page=100')).json()) as { items: { authors: { id: number; name: string }[] }[] }
  const author = works.items.flatMap((work) => work.authors).find((person) => person.name === 'Jonas Lindholm')
  expect(author).toBeTruthy()
  return author!.id
}

for (const [name, size] of [['on a desktop', { width: 1280, height: 800 }], ['on a phone', { width: 390, height: 844 }]] as const) {
  test(`asks before it splits another name off an author, and sends one request ${name}`, async ({ page }) => {
    await page.setViewportSize(size)
    const id = await personWithBooks(page)
    const sent: string[] = []
    // The page asks with the language of the person (`?language=`), so the address ends there or in a query.
    await page.route(new RegExp(`/api/v1/people/${id}(\\?.*)?$`), async (route) => {
      const response = await route.fetch()
      const person = (await response.json()) as Record<string, unknown>
      await route.fulfill({ response, json: { ...person, aliases: ['Nele Beispielname'], pen_names: [{ id: 7, name: 'Nele Beispielname' }], can_split: true } })
    })
    await page.route(`**/api/v1/people/${id}/split`, async (route) => {
      sent.push(`${route.request().method()} ${route.request().postData()}`)
      const response = await route.fetch({ url: route.request().url().replace('/split', ''), method: 'GET', postData: undefined })
      const person = (await response.json()) as Record<string, unknown>
      await route.fulfill({ json: { ...person, aliases: [], pen_names: [], can_split: false } })
    })
    await page.goto(`/person/${id}`)
    const names = page.locator('[data-testid="pen-names"]')
    await expect(names).toContainText('Also writes as Nele Beispielname.')
    const button = page.getByRole('button', { name: 'Split “Nele Beispielname” from Jonas Lindholm' })
    await expect(button).toBeVisible()
    const box = await names.boundingBox()
    expect(box!.x + box!.width).toBeLessThanOrEqual(size.width)
    await button.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('This cannot be undone here.')
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true })
      await page.evaluate(() => document.fonts.ready)
      await page.screenshot({ path: path.join(SHOTS, `split-${size.width}.png`) })
    }
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()
    expect(sent).toEqual([])
    await button.click()
    await page.getByRole('dialog').getByRole('button', { name: 'Split', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('“Nele Beispielname” is a person of their own now.')
    await expect(names).toBeHidden()
    expect(sent).toEqual(['POST {"alias_id":7}'])
  })
}
