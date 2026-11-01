/**
 * (Every wrong secret or code counts against the address like a wrong password, and all the tests share one address: so
 * the tests ask the browser's side whether a code is gone, never the app's, and the app's side is asked only with right
 * secrets and codes.)
 *
 * Connecting the iPhone app, in a real browser against the real backend: the window shows the QR code and the
 * code to type, the app is imitated by the API (it fetches with the secret of the QR code or with the code), the window
 * says "Connected: <device>", the device stands in the list and works with its token; a code nobody took is withdrawn when
 * the window closes; and the window fits a phone. The secret is read from the answer of the server, never from the page.
 */
import { expect, test, type APIRequestContext, type Page, type PlaywrightWorkerArgs } from '@playwright/test'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-pairing' }

// The tab of the devices also holds the card of Web Push. With a service worker allowed, the WebKit that Playwright brings
// stops drawing that tab (the page stands still and no click is ever "stable"); the card asks the service worker about a
// subscription, which these tests have no use for. The other engines are unaffected, but one setting serves all three.
test.use({ serviceWorkers: 'block' })

test.beforeEach(async ({ page }) => {
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
  // Devices of a run before would only crowd the list.
  for (const device of (await (await page.request.get('/api/v1/devices')).json()) as { id: string; name: string }[]) {
    if (device.name.startsWith('E2E ')) await page.request.delete(`/api/v1/devices/${device.id}`, { headers: TAB })
  }
})

// The other specs expect the language the operator was made with (English): the German test must not leave its choice behind.
test.afterEach(async ({ page }) => {
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
})

type Made = { id: string; secret: string; code: string; address: string; link: string; qr_svg: string }

/** The app: no cookie of ours, no tab header. */
async function theApp(playwright: PlaywrightWorkerArgs['playwright'], baseURL: string): Promise<APIRequestContext> {
  // The config's session would be inherited: the app has none.
  return playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
}

async function openWindow(page: Page): Promise<Made> {
  await page.goto('/account?tab=devices')
  const made = page.waitForResponse((answer) => answer.url().endsWith('/api/v1/devices/pairing') && answer.request().method() === 'POST')
  await page.getByRole('button', { name: 'Connect the iPhone app' }).click()
  const answer = await made
  expect(answer.status()).toBe(201)
  return (await answer.json()) as Made
}

test('shows the QR code and the code, and says Connected when the app takes the code with its secret', async ({ page, playwright, baseURL }) => {
  const made = await openWindow(page)
  const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
  await expect(dialog).toBeVisible()
  const picture = dialog.getByRole('img', { name: 'QR code for connecting the iPhone app' })
  await expect(picture).toBeVisible()
  expect(await picture.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
  await expect(dialog.getByTestId('app-code')).toHaveText(made.code)
  expect(made.code).toMatch(/^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$/)
  await expect(dialog.getByTestId('app-left')).toContainText('10 more minutes')
  expect(made.address).toBe(baseURL)
  // The page never holds the secret or the link of the code.
  expect(await page.content()).not.toContain(made.secret)
  expect(await page.content()).not.toContain('nexbooks://')
  await shots(page, 'pairing-waiting')

  const app = await theApp(playwright, baseURL!)
  const claimed = await app.post(`/api/app/1/pairing/${made.id}/claim`, { data: { secret: made.secret, device_name: 'E2E iPhone' } })
  expect(claimed.status(), await claimed.text()).toBe(200)
  const body = (await claimed.json()) as { token: string; account: { name: string }; server: { version: string } }
  expect(body.token).toMatch(/^nbd_[A-Za-z0-9_-]{43}$/)
  expect(body.account.name).toBe('tester')
  await expect(dialog.getByTestId('app-connected')).toContainText('Connected: E2E iPhone', { timeout: 15_000 })
  await shots(page, 'pairing-connected')

  // The token works on its own, the device is in the list, and revoking it ends the token.
  const bearer = { Authorization: `Bearer ${body.token}` }
  expect((await app.get('/api/v1/auth/me', { headers: bearer })).status()).toBe(200)
  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(dialog).toHaveCount(0)
  const row = page.getByTestId('device-tokens').locator('li', { hasText: 'E2E iPhone' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Remove E2E iPhone' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click()
  await expect(row).toHaveCount(0)
  expect((await app.get('/api/v1/auth/me', { headers: bearer })).status()).toBe(401)
  // The code was used: the app cannot take a second token with it (told so, because the secret is right).
  const again = await app.post(`/api/app/1/pairing/${made.id}/claim`, { data: { secret: made.secret, device_name: 'E2E again' } })
  expect(again.status()).toBe(410)
  expect(((await again.json()) as { detail: { code: string } }).detail.code).toBe('pairing_used')
  await app.dispose()
})

test('says Connected when the app takes the code that was typed', async ({ page, playwright, baseURL }) => {
  const made = await openWindow(page)
  const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
  const shown = (await dialog.getByTestId('app-code').textContent())!
  expect(shown).toBe(made.code)
  const app = await theApp(playwright, baseURL!)
  // As a person types it: in small letters and without the dash.
  const claimed = await app.post('/api/app/1/pairing/claim', { data: { code: shown.toLowerCase().replace('-', ' '), device_name: 'E2E typed' } })
  expect(claimed.status(), await claimed.text()).toBe(200)
  await expect(dialog.getByTestId('app-connected')).toContainText('Connected: E2E typed', { timeout: 15_000 })
  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('device-tokens').locator('li', { hasText: 'E2E typed' })).toBeVisible()
  const second = await app.post('/api/app/1/pairing/claim', { data: { code: shown, device_name: 'E2E twice' } })
  expect(second.status()).toBe(410)
  expect(((await second.json()) as { detail: { code: string } }).detail.code).toBe('pairing_used')
  await app.dispose()
})

test('a code nobody took is withdrawn when the window closes', async ({ page }) => {
  const made = await openWindow(page)
  const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
  const gone = page.waitForResponse((answer) => answer.url().endsWith(`/api/v1/devices/pairing/${made.id}`) && answer.request().method() === 'DELETE')
  await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
  expect((await gone).status()).toBe(204)
  expect((await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status()).toBe(404)
})

test('withdrawing on purpose asks first and sends nothing before the answer', async ({ page }) => {
  const made = await openWindow(page)
  const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
  let deletes = 0
  page.on('request', (request) => {
    if (request.method() === 'DELETE' && request.url().includes('/devices/pairing/')) deletes += 1
  })
  await dialog.getByRole('button', { name: 'Withdraw' }).click()
  const question = page.getByRole('dialog', { name: 'Withdraw the code?' })
  await expect(question).toBeVisible()
  await expect(question).toContainText('You can show a new code at any time.')
  await question.getByRole('button', { name: 'Cancel' }).click()
  await expect(question).toHaveCount(0)
  expect(deletes).toBe(0)
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Withdraw' }).click()
  await page.getByRole('dialog', { name: 'Withdraw the code?' }).getByRole('button', { name: 'Withdraw' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect.poll(() => deletes).toBe(1)
  expect((await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status()).toBe(404)
})

test('leaving the page withdraws the code, too', async ({ page }) => {
  const made = await openWindow(page)
  await expect(page.getByRole('dialog', { name: 'Connect the iPhone app' })).toBeVisible()
  await page.goto('/')
  await expect
    .poll(async () => (await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status(), { intervals: [300], timeout: 10_000 })
    .toBe(404)
})

test('the window speaks German and fits a phone', async ({ page }) => {
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/account?tab=devices')
  await page.getByRole('button', { name: 'iPhone-App verbinden' }).click()
  const dialog = page.getByRole('dialog', { name: 'iPhone-App verbinden' })
  await expect(dialog.getByTestId('app-code')).toBeVisible()
  await expect(dialog).toContainText('Der Code gilt noch 10 Minuten.')
  const box = (await dialog.boundingBox())!
  const picture = (await dialog.getByRole('img').boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(picture.x).toBeGreaterThanOrEqual(0)
  expect(picture.x + picture.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  const code = (await dialog.getByTestId('app-code').boundingBox())!
  expect(code.x + code.width).toBeLessThanOrEqual(390)
  await shots(page, 'pairing-phone')
  await dialog.getByRole('button', { name: 'Schließen', exact: true }).first().click()
})

// On the phone itself a QR code cannot be scanned: the dialog offers a button that opens the app and one that copies the
// code. The browser here only says it is an iPhone (a real one is for a person to try); the link the button opens is caught
// before the browser would leave the page.
test.describe('on an iPhone', () => {
  test.use({
    userAgent:
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
    viewport: { width: 390, height: 844 },
  })

  test('the dialog opens the app from the page memory, copies the code, and says the address is no use to a phone', async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __opened: string[]; __copied: string[] }
      w.__opened = []
      w.__copied = []
      const real = HTMLAnchorElement.prototype.click
      HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
        if (this.href.startsWith('nexbooks:')) {
          w.__opened.push(this.getAttribute('href') ?? '')
          return
        }
        real.call(this)
      }
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: async (text: string) => void w.__copied.push(text) },
        configurable: true,
      })
    })
    const made = await openWindow(page)
    const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
    await expect(dialog.getByTestId('app-code')).toHaveText(made.code)
    // The e2e server is reached as 127.0.0.1: no phone gets there.
    await expect(dialog.getByTestId('app-local')).toContainText('The iPhone cannot reach this address.')
    expect(await page.content()).not.toContain(made.secret)
    expect(await page.content()).not.toContain('nexbooks://')

    await dialog.getByRole('button', { name: 'Copy code' }).click()
    await expect(dialog.getByTestId('app-copied')).toHaveText('Code copied')
    expect(await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied)).toEqual([made.code])

    await dialog.getByRole('button', { name: 'Open in the app' }).click()
    expect(await page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toEqual([made.link])
    expect(await page.content()).not.toContain(made.secret)
    await shots(page, 'pairing-iphone')
    // The phone may now hide the page while the app opens: that must not take the code away ...
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')))
    await page.waitForTimeout(500)
    // ... the page is still there and the code was not withdrawn by going over to the app ...
    await expect(dialog).toBeVisible()
    expect((await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status()).toBe(200)
    // ... and closing the window withdraws it, as it does for anyone.
    await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
    await expect.poll(async () => (await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status(), { intervals: [300], timeout: 10_000 }).toBe(404)
  })
})

test('a computer shows the QR code and no button to open the app, but copies the code', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => undefined }, configurable: true })
  })
  const made = await openWindow(page)
  const dialog = page.getByRole('dialog', { name: 'Connect the iPhone app' })
  await expect(dialog.getByRole('button', { name: 'Open in the app' })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: 'Copy code' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
  await expect.poll(async () => (await page.request.get(`/api/v1/devices/pairing/${made.id}`)).status(), { intervals: [300], timeout: 10_000 }).toBe(404)
})
