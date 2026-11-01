/**
 * "Connect the iPhone app": the QR code and the code to type, the countdown, "Connected: <device>" when the app took it,
 * and a code nobody took goes away again (when the window closes, and on purpose after a question). The secret of the
 * code is never on the page.
 */
import { act } from 'react'

import { changeLanguage } from '../i18n'
import { copyText } from '../lib/copy'
import { Page, Server } from '../test/harness'
import { eventually, idle } from '../test/wait'
import { AppConnect } from './AppConnect'
import { Devices } from './Devices'

vi.mock('../lib/copy', () => ({ copyText: vi.fn() }))

const SECRET = 's'.repeat(43)
const MADE = {
  id: 'pair-1',
  secret: SECRET,
  code: 'ABCD-2345',
  expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
  address: 'https://books.example.com',
  link: `nexbooks://connect?address=https%3A%2F%2Fbooks.example.com&pairing=pair-1&secret=${SECRET}`,
  qr_svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1H0z"/></svg>',
}

const server = new Server()
const page = new Page()

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  server.install()
  server.answers = {}
  server.fails = {}
  server.answers['POST /api/v1/devices/pairing'] = MADE
  server.answers['GET /api/v1/devices/pairing/pair-1'] = { state: 'waiting', device: null }
  server.answers['DELETE /api/v1/devices/pairing/pair-1'] = 204
  await changeLanguage('de')
})

afterEach(async () => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  await changeLanguage('de')
})

async function later(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
  await idle()
}

const deletes = () => server.calls.filter((call) => call.method === 'DELETE' && call.url === '/api/v1/devices/pairing/pair-1')

test('shows the QR code and the code to type, for the address this browser is at, and keeps the secret off the page', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  const made = server.called('POST', '/api/v1/devices/pairing')
  expect(made?.body).toEqual({ address: window.location.origin })
  const image = page.dialog!.querySelector('img')!
  expect(image.getAttribute('alt')).toBe('QR-Code zum Verbinden der iPhone-App')
  expect(decodeURIComponent(image.getAttribute('src')!)).toContain('<svg')
  expect(page.dialog!.querySelector('[data-testid="app-code"]')!.textContent).toBe('ABCD-2345')
  expect(page.text).toContain('Der Code gilt noch 10 Minuten.')
  expect(page.text).toContain('Die App verbindet sich mit https://books.example.com.')
  expect(page.text).toContain('Schließt du dieses Fenster, gilt der Code nicht mehr.')
  expect(document.body.innerHTML).not.toContain(SECRET)
  expect(document.body.innerHTML).not.toContain('nexbooks://connect')
})

test('counts the minutes down and, when they are over, says the code ran out and offers a new one', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  await later(5 * 60_000)
  expect(page.text).toMatch(/Der Code gilt noch [45] Minuten\./)
  await later(4 * 60_000 + 30_000)
  expect(page.text).toContain('Der Code gilt noch 1 Minute.')
  await later(60_000)
  await eventually(() => expect(page.dialog!.querySelector('[data-testid="app-expired"]')).toBeTruthy())
  expect(page.dialog!.querySelector('img')).toBeNull()
  expect(page.button('Neuen Code zeigen')).toBeTruthy()
})

test('says Connected with the name of the device when the app took the code, and sends nothing when closed', async () => {
  const closed = vi.fn()
  await page.show(<AppConnect onClose={closed} />)
  server.answers['GET /api/v1/devices/pairing/pair-1'] = { state: 'connected', device: { id: 'd'.repeat(32), name: "Ada's iPhone" } }
  await later(2100)
  await eventually(() => expect(page.text).toContain("Verbunden: Ada's iPhone"))
  expect(page.text).toContain('lässt sich dort jederzeit trennen')
  expect(page.dialog!.querySelector('img')).toBeNull()
  expect(page.button('Zurückziehen')).toBeUndefined()
  await page.click(page.button('Fertig'))
  expect(closed).toHaveBeenCalledWith(true)
  page.close()
  expect(deletes()).toHaveLength(0)
})

test('stops asking once the app took the code', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  server.answers['GET /api/v1/devices/pairing/pair-1'] = { state: 'connected', device: { id: 'd'.repeat(32), name: 'iPhone' } }
  await later(2100)
  await eventually(() => expect(page.text).toContain('Verbunden: iPhone'))
  const asked = server.calls.filter((call) => call.method === 'GET' && call.url === '/api/v1/devices/pairing/pair-1').length
  await later(10_000)
  expect(server.calls.filter((call) => call.method === 'GET' && call.url === '/api/v1/devices/pairing/pair-1').length).toBe(asked)
})

test('withdrawing asks first, says what happens, and sends nothing before the answer', async () => {
  const closed = vi.fn()
  await page.show(<AppConnect onClose={closed} />)
  await page.click(page.button('Zurückziehen'))
  const question = page.dialog!
  expect(question.textContent).toContain('Code zurückziehen?')
  expect(question.textContent).toContain('Du kannst jederzeit einen neuen Code zeigen.')
  expect(deletes()).toHaveLength(0)
  await page.click(page.button('Abbrechen', question))
  expect(deletes()).toHaveLength(0)
  expect(closed).not.toHaveBeenCalled()
  await page.click(page.button('Zurückziehen'))
  await page.click(page.button('Zurückziehen', page.dialog!))
  expect(deletes()).toHaveLength(1)
  expect(closed).toHaveBeenCalledWith(false)
  page.close()
  expect(deletes()).toHaveLength(1)
})

test('a code nobody took is withdrawn when the window goes', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(deletes()).toHaveLength(0)
  page.close()
  await idle()
  expect(deletes()).toHaveLength(1)
})

test('a code nobody took is withdrawn when the page is left', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  await act(async () => {
    window.dispatchEvent(new Event('pagehide'))
  })
  await idle()
  expect(deletes()).toHaveLength(1)
})

test('a code made after the window went is withdrawn at once', async () => {
  let release: (() => void) | undefined
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url === '/api/v1/devices/pairing' && init?.method === 'POST') {
        await held
        return new Response(JSON.stringify(MADE), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(null, { status: 204 })
    }),
  )
  // The page waits for the held answer, so it is drawn without waiting for it. Nothing else may loop on `act` beside the
  // wait below (`Page.start`): two at once break the rendering of every later test when the fake API is slow.
  await page.start(<AppConnect onClose={() => undefined} />)
  await eventually(() => expect(calls).toContain('POST /api/v1/devices/pairing'))
  page.close()
  release!()
  await eventually(() => expect(calls).toContain('DELETE /api/v1/devices/pairing/pair-1'))
})

test('tells why no code could be made, and tries again on request', async () => {
  server.fails['POST /api/v1/devices/pairing'] = { status: 422, code: 'pairing_address' }
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.text).toContain('nexbooks weiß nicht, unter welcher Adresse es erreichbar ist.')
  expect(page.dialog!.querySelector('img')).toBeNull()
  delete server.fails['POST /api/v1/devices/pairing']
  await page.click(page.button('Neuen Code zeigen'))
  expect(page.dialog!.querySelector('[data-testid="app-code"]')!.textContent).toBe('ABCD-2345')
  expect(server.calls.filter((call) => call.method === 'POST' && call.url === '/api/v1/devices/pairing')).toHaveLength(2)
})

test('names the limit when five codes are open', async () => {
  server.fails['POST /api/v1/devices/pairing'] = { status: 429, code: 'pairing_limit', values: { max: 5 } }
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.text).toContain('Es sind schon 5 Codes offen.')
})

test('a code the server no longer knows counts as run out, and the new one replaces it', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  delete server.answers['GET /api/v1/devices/pairing/pair-1']
  server.fails['GET /api/v1/devices/pairing/pair-1'] = { status: 404, code: 'not_found' }
  await later(2100)
  await eventually(() => expect(page.dialog!.querySelector('[data-testid="app-expired"]')).toBeTruthy())
  server.answers['POST /api/v1/devices/pairing'] = { ...MADE, id: 'pair-2', code: 'WXYZ-6789' }
  await page.click(page.button('Neuen Code zeigen'))
  expect(page.dialog!.querySelector('[data-testid="app-code"]')!.textContent).toBe('WXYZ-6789')
  // The one that ran out is let go once more, in case it was still there.
  expect(deletes()).toHaveLength(1)
})

test('speaks English when the page does', async () => {
  await changeLanguage('en')
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.dialog!.getAttribute('aria-label')).toBe('Connect the iPhone app')
  expect(page.text).toContain('Or type this code in the app:')
  expect(page.text).toContain('The code is good for 10 more minutes.')
  expect(page.dialog!.querySelector('img')!.getAttribute('alt')).toBe('QR code for connecting the iPhone app')
})

test('the card of the devices has the button, opens the window, and lists again when it closes', async () => {
  server.answers['GET /api/v1/devices'] = []
  await page.show(<Devices />)
  expect(page.dialog).toBeNull()
  await page.click(page.button('iPhone-App verbinden'))
  expect(page.dialog!.getAttribute('aria-label')).toBe('iPhone-App verbinden')
  expect(server.calls.filter((call) => call.method === 'GET' && call.url === '/api/v1/devices')).toHaveLength(1)
  server.answers['GET /api/v1/devices'] = [{ id: 'd'.repeat(32), name: "Ada's iPhone", prefix: 'nbd_AbCd', created_at: '2026-10-17T10:00:00Z', last_used_at: null }]
  await page.click(page.dialog!.querySelector<HTMLButtonElement>('[data-close]'))
  expect(page.dialog).toBeNull()
  expect(page.text).toContain("Ada's iPhone")
  expect(deletes()).toHaveLength(1)
})


// ---- On the phone itself, and an address the phone cannot reach -----------------------------------------------------

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'

function userAgent(value: string | null): void {
  if (value === null) delete (navigator as unknown as Record<string, unknown>).userAgent
  else Object.defineProperty(navigator, 'userAgent', { value, configurable: true })
}

/** The links the page opens (a made anchor that is clicked), so that nothing really navigates. */
function opened(): string[] {
  const links: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    links.push(this.getAttribute('href') ?? '')
  })
  return links
}

afterEach(() => {
  userAgent(null)
  vi.restoreAllMocks()
})

test('on the phone itself a button opens the app, from the page memory and with no link in the markup', async () => {
  userAgent(IPHONE)
  const links = opened()
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(document.body.innerHTML).not.toContain('nexbooks://')
  expect(document.body.innerHTML).not.toContain(SECRET)
  await page.click(page.button('In der App öffnen'))
  expect(links).toEqual([MADE.link])
  expect(document.body.innerHTML).not.toContain('nexbooks://')
  expect(document.body.innerHTML).not.toContain(SECRET)
  expect(document.querySelector('a[href^="nexbooks:"]')).toBeNull()
  expect(page.text).toContain('Öffnet sich nichts, ist die App noch nicht installiert.')
})

test('a computer has no button to open the app (it has the QR code), and an iPad that calls itself a Mac has it', async () => {
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.button('In der App öffnen')).toBeUndefined()
  expect(page.text).not.toContain('Öffnet sich nichts')
  page.close()
  userAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15')
  Object.defineProperty(navigator, 'platform', { value: 'MacIntel', configurable: true })
  Object.defineProperty(navigator, 'maxTouchPoints', { value: 5, configurable: true })
  try {
    await page.show(<AppConnect onClose={() => undefined} />)
    expect(page.button('In der App öffnen')).toBeTruthy()
  } finally {
    delete (navigator as unknown as Record<string, unknown>).platform
    delete (navigator as unknown as Record<string, unknown>).maxTouchPoints
  }
})

test('after "Open in the app" the code stays when the phone hides the page, and is withdrawn when the window closes', async () => {
  userAgent(IPHONE)
  opened()
  await page.show(<AppConnect onClose={() => undefined} />)
  await page.click(page.button('In der App öffnen'))
  await act(async () => {
    window.dispatchEvent(new Event('pagehide'))
  })
  await idle()
  expect(deletes()).toHaveLength(0)
  page.close()
  await idle()
  expect(deletes()).toHaveLength(1)
})

test('copies the code, says so, and says when copying did not work', async () => {
  vi.mocked(copyText).mockResolvedValue(true)
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.dialog!.querySelector('[data-testid="app-copied"]')).toBeNull()
  await page.click(page.button('Code kopieren'))
  expect(copyText).toHaveBeenCalledWith('ABCD-2345')
  expect(page.dialog!.querySelector('[data-testid="app-copied"]')!.textContent).toBe('Code kopiert')
  vi.mocked(copyText).mockResolvedValue(false)
  await page.click(page.button('Code kopieren'))
  expect(page.dialog!.querySelector('[data-testid="app-copied"]')!.textContent).toContain('Kopieren ging nicht.')
})

test.each(['http://127.0.0.1:8580', 'http://localhost:8580', 'http://[::1]:8580', 'http://127.1.2.3', 'http://books.localhost'])(
  'warns that the phone cannot reach %s',
  async (address) => {
    server.answers['POST /api/v1/devices/pairing'] = { ...MADE, address }
    await page.show(<AppConnect onClose={() => undefined} />)
    expect(page.dialog!.querySelector('[data-testid="app-local"]')!.textContent).toBe(
      'Das iPhone erreicht diese Adresse nicht. Trag in den Einstellungen die Adresse nach außen ein.',
    )
  },
)

test.each(['https://books.example.com', 'http://192.168.1.20:8580', 'http://books.example.com:8580', 'https://127.example.com'])(
  'does not warn about %s',
  async (address) => {
    server.answers['POST /api/v1/devices/pairing'] = { ...MADE, address }
    await page.show(<AppConnect onClose={() => undefined} />)
    expect(page.dialog!.querySelector('[data-testid="app-local"]')).toBeNull()
  },
)

test('speaks English on the phone too', async () => {
  userAgent(IPHONE)
  await changeLanguage('en')
  server.answers['POST /api/v1/devices/pairing'] = { ...MADE, address: 'http://localhost:8580' }
  await page.show(<AppConnect onClose={() => undefined} />)
  expect(page.button('Open in the app')).toBeTruthy()
  expect(page.button('Copy code')).toBeTruthy()
  expect(page.text).toContain('The iPhone cannot reach this address. Enter the address from outside in the settings.')
})
