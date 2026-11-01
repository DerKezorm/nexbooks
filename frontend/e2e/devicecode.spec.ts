/**
 * (Every wrong code typed counts against the address like a wrong password, and all the tests share one address: so the
 * tests type a wrong code once, and the device's side is asked only with right secrets.)
 *
 * Connecting a device that has no camera, in a real browser against the real backend: the device is imitated
 * by the API (it asks for a code, then asks how it stands with its secret), the person types the code in the window of the
 * account page, sees the name and the question and confirms; only then the device is given its token, once, for the account
 * that confirmed. Nothing is given for a code alone, a question answered with "Cancel", or a confirmation taken back.
 */
import { expect, test, type APIRequestContext, type Page, type PlaywrightWorkerArgs } from '@playwright/test'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-devicecode' }
const NAME = 'E2E Living room TV'
// English quotation marks round the name of the device (the window says whose word it is).
const LQ = '“'
const RQ = '”'

// The tab of the devices also holds the card of Web Push; with a service worker allowed, the WebKit that Playwright brings
// stops drawing it (see pairing.spec.ts).
test.use({ serviceWorkers: 'block' })

test.beforeEach(async ({ page }) => {
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
  for (const device of (await (await page.request.get('/api/v1/devices')).json()) as { id: string; name: string }[]) {
    if (device.name.startsWith('E2E ')) await page.request.delete(`/api/v1/devices/${device.id}`, { headers: TAB })
  }
})

/** The requests a test made: the device lets each go afterwards, as its app does when its screen is left (all the tests
 * come from one address, which may have only so many codes open at once). */
const leftOpen: Asked[] = []

test.afterEach(async ({ page, playwright, baseURL }) => {
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'en' } })).ok()).toBe(true)
  const device = await theDevice(playwright, baseURL!)
  for (const each of leftOpen.splice(0)) {
    expect((await device.delete(`/api/app/1/pairing/request/${each.id}`, { headers: { 'X-Pairing-Secret': each.secret } })).status()).toBe(204)
  }
  await device.dispose()
})

type Asked = { id: string; secret: string; code: string; expires_at: string; poll_seconds: number }

/** The device: no cookie of ours, no tab header. */
async function theDevice(playwright: PlaywrightWorkerArgs['playwright'], baseURL: string): Promise<APIRequestContext> {
  return playwright.request.newContext({ baseURL, storageState: { cookies: [], origins: [] } })
}

async function ask(device: APIRequestContext, name = NAME): Promise<Asked> {
  const answer = await device.post('/api/app/1/pairing/request', { data: { device_name: name } })
  expect(answer.status(), await answer.text()).toBe(201)
  const found = (await answer.json()) as Asked
  leftOpen.push(found)
  return found
}

function poll(device: APIRequestContext, made: Asked) {
  return device.get(`/api/app/1/pairing/request/${made.id}`, { headers: { 'X-Pairing-Secret': made.secret } })
}

async function openWindow(page: Page) {
  await page.goto('/account?tab=devices')
  await page.getByRole('button', { name: 'Connect a device with a code' }).click()
  const dialog = page.getByRole('dialog', { name: 'Connect a device with a code' })
  await expect(dialog).toBeVisible()
  return dialog
}

async function typeCode(dialog: ReturnType<Page['getByRole']>, code: string) {
  await dialog.getByLabel('Code from the device').fill(code)
  await dialog.getByRole('button', { name: 'Next' }).click()
}

test('the person types the code, sees the device, confirms, and the device fetches its token once', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  expect(made.code).toMatch(/^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$/)
  expect(made.poll_seconds).toBe(3)
  expect(await (await poll(device, made)).json()).toEqual({ state: 'waiting' })

  const dialog = await openWindow(page)
  await shots(page, 'devicecode-type')
  // As a person types it from a television: small letters and a space instead of the dash.
  await typeCode(dialog, made.code.toLowerCase().replace('-', ' '))
  await expect(dialog.getByTestId('device-name')).toHaveText(`${LQ}${NAME}${RQ}`)
  await expect(dialog.getByText('Connect it to your account?')).toBeVisible()
  await expect(dialog.getByTestId('device-warning')).toContainText('Confirm only if you see this code on the screen of the device yourself')
  await shots(page, 'devicecode-ask')
  // Nothing was given for a typed code and a look: the device still waits, and there is no device in the list.
  expect(await (await poll(device, made)).json()).toEqual({ state: 'waiting' })
  await expect(page.getByTestId('device-tokens')).toHaveCount(0)

  await dialog.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(dialog.getByTestId('device-waiting')).toContainText(`Waiting for ${NAME}`)
  // The device asks: the token comes with this one answer, for the account that confirmed.
  const fetched = await poll(device, made)
  expect(fetched.status(), await fetched.text()).toBe(200)
  const body = (await fetched.json()) as { state: string; token: string; account: { name: string }; server: { version: string } }
  expect(body.state).toBe('confirmed')
  expect(body.token).toMatch(/^nbd_[A-Za-z0-9_-]{43}$/)
  expect(body.account.name).toBe('tester')
  expect(fetched.headers()['cache-control']).toBe('no-store')
  await expect(dialog.getByTestId('device-connected')).toContainText(`Connected: ${NAME}`, { timeout: 15_000 })
  await shots(page, 'devicecode-connected')
  // The page never held the secret or the token.
  const html = await page.content()
  expect(html).not.toContain(made.secret)
  expect(html).not.toContain(body.token)

  const bearer = { Authorization: `Bearer ${body.token}` }
  expect((await device.get('/api/v1/auth/me', { headers: bearer })).status()).toBe(200)
  const again = await poll(device, made)
  expect(again.status()).toBe(410)
  expect(((await again.json()) as { detail: { code: string } }).detail.code).toBe('pairing_used')
  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(dialog).toHaveCount(0)
  const row = page.getByTestId('device-tokens').locator('li', { hasText: NAME })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: `Remove ${NAME}` }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click()
  await expect(row).toHaveCount(0)
  // (That the revoked token stops is tried in pairing.spec.ts: a wrong token counts against the address that all the tests share.)
  await device.dispose()
})

test('"Cancel" at the question gives the device nothing', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  const dialog = await openWindow(page)
  await typeCode(dialog, made.code)
  await expect(dialog.getByTestId('device-name')).toHaveText(`${LQ}${NAME}${RQ}`)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)
  expect(await (await poll(device, made)).json()).toEqual({ state: 'waiting' })
  expect(await (await page.request.get('/api/v1/devices')).json()).toEqual([])
  await device.dispose()
})

test('a confirmation taken back asks first and leaves the device with nothing', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  const dialog = await openWindow(page)
  await typeCode(dialog, made.code)
  await dialog.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(dialog.getByTestId('device-waiting')).toBeVisible()
  let deletes = 0
  page.on('request', (request) => {
    if (request.method() === 'DELETE' && request.url().includes('/devices/pair-requests/')) deletes += 1
  })
  await dialog.getByRole('button', { name: 'Withdraw' }).click()
  const question = page.getByRole('dialog', { name: 'Withdraw the confirmation?' })
  await expect(question).toBeVisible()
  await expect(question).toContainText('The device then gets no access')
  await question.getByRole('button', { name: 'Cancel' }).click()
  await expect(question).toHaveCount(0)
  expect(deletes).toBe(0)
  await dialog.getByRole('button', { name: 'Withdraw' }).click()
  await page.getByRole('dialog', { name: 'Withdraw the confirmation?' }).getByRole('button', { name: 'Withdraw' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect.poll(() => deletes).toBe(1)
  expect(await (await poll(device, made)).json()).toEqual({ state: 'expired' })
  expect(await (await page.request.get('/api/v1/devices')).json()).toEqual([])
  await device.dispose()
})

test('a code nobody made is told so in words, and nothing changes', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  const dialog = await openWindow(page)
  await typeCode(dialog, 'ZZZZ-2222')
  await expect(dialog.getByRole('alert')).toContainText('This code does not exist or has run out.')
  await expect(dialog.getByLabel('Code from the device')).toBeVisible()
  // The right code still works after one mistake.
  await typeCode(dialog, made.code)
  await expect(dialog.getByTestId('device-name')).toHaveText(`${LQ}${NAME}${RQ}`)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await device.dispose()
})

test('a name made of markup stays text on the page', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device, 'E2E <b id="bold">TV</b>')
  const dialog = await openWindow(page)
  await typeCode(dialog, made.code)
  await expect(dialog.getByTestId('device-name')).toHaveText(`${LQ}E2E <b id="bold">TV</b>${RQ}`)
  await expect(page.locator('#bold')).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await device.dispose()
})

test('the window speaks German and fits a phone', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/account?tab=devices')
  await page.getByRole('button', { name: 'Gerät mit Code verbinden' }).click()
  const dialog = page.getByRole('dialog', { name: 'Gerät mit Code verbinden' })
  await expect(dialog).toContainText('Gib den Code ein, den das Gerät anzeigt')
  await dialog.getByLabel('Code vom Gerät').fill(made.code)
  await dialog.getByRole('button', { name: 'Weiter' }).click()
  await expect(dialog.getByText('Mit deinem Konto verbinden?')).toBeVisible()
  await expect(dialog.getByTestId('device-warning')).toContainText('Bestätige nur, wenn du diesen Code gerade selbst')
  const box = (await dialog.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  const name = (await dialog.getByTestId('device-name').boundingBox())!
  expect(name.x + name.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await shots(page, 'devicecode-phone')
  await dialog.getByRole('button', { name: 'Abbrechen' }).click()
  await device.dispose()
})

test('the first step warns, and a name that tries to speak for the system is a quoted line of plain text', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const talk = 'E2E Your account was locked. Confirm now! Your account was locked. Confirm now!'
  const made = await ask(device, talk)
  const dialog = await openWindow(page)
  await expect(dialog.getByTestId('device-first-warning')).toHaveText('Only type a code here that you see yourself on your own television.')
  await shots(page, 'devicecode-first')
  await typeCode(dialog, made.code)
  await expect(dialog.getByText('The device calls itself')).toBeVisible()
  const name = dialog.getByTestId('device-name')
  await expect(name).toHaveText(`${LQ}${talk}${RQ}`)
  // One line of the size of the text around it, cut off at its end: never taller than a line, and not a heading.
  const look = await name.evaluate((element) => {
    const style = getComputedStyle(element)
    return { height: element.getBoundingClientRect().height, line: parseFloat(style.lineHeight), size: parseFloat(style.fontSize), tag: element.tagName }
  })
  expect(look.tag).toBe('P')
  expect(look.size).toBeLessThanOrEqual(16)
  expect(look.height).toBeLessThanOrEqual(look.line + 1)
  expect(await dialog.locator('h1, h2, h3').count()).toBe(1) // only the title of the window
  await shots(page, 'devicecode-name-long')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await device.dispose()
})

test('the keyboard: Enter asks, the focus lands on "Connect", Enter connects, and Tab never leaves the window', async ({ page, playwright, baseURL }) => {
  const device = await theDevice(playwright, baseURL!)
  const made = await ask(device)
  const dialog = await openWindow(page)
  await expect(dialog.getByLabel('Code from the device')).toBeFocused()
  await dialog.getByLabel('Code from the device').fill(made.code)
  await page.keyboard.press('Enter')
  await expect(dialog.getByTestId('device-ask')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Connect', exact: true })).toBeFocused()
  // Tab goes round the window: "Connect", "Cancel", the cross, and "Connect" again; never the page behind.
  const seen: string[] = []
  for (let step = 0; step < 4; step += 1) {
    await page.keyboard.press('Tab')
    seen.push(await page.evaluate(() => `${document.activeElement?.tagName}:${document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.textContent}`))
    expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true)
  }
  expect(seen.some((one) => one.includes('Connect'))).toBe(true)
  await page.keyboard.press('Shift+Tab')
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true)
  await dialog.getByRole('button', { name: 'Connect', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(dialog.getByTestId('device-waiting')).toBeVisible()
  // After the step the focus is not on the page behind the window.
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true)
  await dialog.getByRole('button', { name: 'Close' }).first().click()
  await device.dispose()
})

test('a television the phone connected says so in the list, and removing the phone takes it along', async ({ page, playwright, baseURL }) => {
  // The phone: a device token made in this browser (as the QR code would give one), and no cookie of ours.
  const made = await page.request.post('/api/v1/devices', { headers: TAB, data: { name: 'E2E Phone' } })
  expect(made.status(), await made.text()).toBe(201)
  const phoneToken = ((await made.json()) as { token: string }).token
  const phone = await playwright.request.newContext({
    baseURL, storageState: { cookies: [], origins: [] }, extraHTTPHeaders: { Authorization: `Bearer ${phoneToken}` },
  })
  const device = await theDevice(playwright, baseURL!)
  const asked = await ask(device)
  // The phone looks first: the name the server keeps, and nothing is decided.
  const looked = await phone.post('/api/app/1/pairing/request/preview', { data: { id: asked.id, code: asked.code } })
  expect(looked.status(), await looked.text()).toBe(200)
  expect(((await looked.json()) as { device_name: string; state: string })).toMatchObject({ state: 'waiting', device_name: NAME })
  expect(await (await poll(device, asked)).json()).toEqual({ state: 'waiting' })
  expect((await phone.post('/api/app/1/pairing/request/confirm', { data: { id: asked.id, code: asked.code } })).status()).toBe(200)
  const fetched = (await (await poll(device, asked)).json()) as { token: string }
  const television = await playwright.request.newContext({
    baseURL, storageState: { cookies: [], origins: [] }, extraHTTPHeaders: { Authorization: `Bearer ${fetched.token}` },
  })
  expect((await television.get('/api/app/1/me')).status()).toBe(200)
  await page.goto('/account?tab=devices')
  const row = page.getByTestId('device-tokens').locator('li', { hasText: NAME })
  await expect(row.getByTestId('device-via')).toContainText('Connected through')
  await expect(row.getByTestId('device-via')).toContainText('E2E Phone')
  await expect(page.getByTestId('device-tokens').locator('li', { hasText: 'E2E Phone' }).first()).toBeVisible()
  await shots(page, 'devicecode-via')
  await page.getByRole('button', { name: 'Remove E2E Phone' }).click()
  const question = page.getByRole('dialog')
  await expect(question).toContainText('which this device connected, goes with it')
  await expect(question).toContainText(NAME)
  await question.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByTestId('device-tokens')).toHaveCount(0)
  // Both are gone from the list of live devices. (Their tokens are not tried here: every test comes from one address,
  // and a revoked token counts against it like a wrong password; the backend tests ask them.)
  expect(await (await page.request.get('/api/v1/devices')).json()).toEqual([])
  await Promise.all([phone.dispose(), television.dispose(), device.dispose()])
})
