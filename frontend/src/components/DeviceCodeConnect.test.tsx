/**
 * "Connect a device with a code": the person types the code a device without a camera shows, sees how it is named and is
 * asked, and only the answer "Connect" sends the confirmation. Then the page waits for the device and says "Connected:
 * <device>"; a confirmation can be taken back, after a question. The name of the device is text from a stranger and stays
 * text.
 */
import { act } from 'react'

import { changeLanguage } from '../i18n'
import { Page, Server } from '../test/harness'
import { eventually, idle } from '../test/wait'
import { DeviceCodeConnect } from './DeviceCodeConnect'
import { Devices } from './Devices'

const PREVIEW = {
  id: 'req-1',
  device_name: 'Living room TV',
  requested_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
}
const LOOKUP = 'POST /api/v1/devices/pair-requests/lookup'
const CONFIRM = 'POST /api/v1/devices/pair-requests/req-1/confirm'
const STATE = 'GET /api/v1/devices/pair-requests/req-1'
const WITHDRAW = 'DELETE /api/v1/devices/pair-requests/req-1'

const server = new Server()
const page = new Page()

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  server.install()
  server.answers = {}
  server.fails = {}
  server.answers[LOOKUP] = PREVIEW
  server.answers[CONFIRM] = { state: 'confirmed', device: null }
  server.answers[STATE] = { state: 'confirmed', device: null }
  server.answers[WITHDRAW] = 204
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

const sent = (key: string) => server.calls.filter((call) => `${call.method} ${call.url}` === key)
const field = () => page.dialog!.querySelector<HTMLInputElement>('input')

async function typeAndAsk(code = 'abcd-2345'): Promise<void> {
  await page.type(field(), code)
  await page.click(page.button('Weiter'))
}

test('asks for the code first and sends nothing until a code is there', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  expect(page.dialog!.getAttribute('aria-label')).toBe('Gerät mit Code verbinden')
  expect(page.text).toContain('Gib den Code ein, den das Gerät anzeigt')
  expect(field()!.placeholder).toBe('ABCD-2345')
  expect(page.button('Weiter')!.disabled).toBe(true)
  await page.type(field(), '   ')
  expect(page.button('Weiter')!.disabled).toBe(true)
  expect(server.calls).toEqual([])
})

test('sends the code as typed and then shows the device and the question, and sends nothing more yet', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk(' abcd 2345 ')
  expect(sent(LOOKUP)).toHaveLength(1)
  expect(sent(LOOKUP)[0].body).toEqual({ code: 'abcd 2345' })
  expect(page.dialog!.querySelector('[data-testid="device-name"]')!.textContent).toBe('„Living room TV“')
  expect(page.text).toContain('Gerade eben angefragt.')
  expect(page.text).toContain('Mit deinem Konto verbinden?')
  expect(page.text).toContain('Das Gerät sieht danach alles, was du in nexbooks siehst.')
  expect(page.dialog!.querySelector('[data-testid="device-warning"]')!.textContent).toContain('Bestätige nur, wenn du diesen Code gerade selbst auf dem Bildschirm')
  expect(sent(CONFIRM)).toHaveLength(0)
  expect(server.changes()).toEqual(['POST /api/v1/devices/pair-requests/lookup'])
})

test('the name of the device is text from a stranger and stays text', async () => {
  server.answers[LOOKUP] = { ...PREVIEW, device_name: '<img src=x onerror=alert(1)> TV' }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  expect(page.dialog!.querySelector('[data-testid="device-name"]')!.textContent).toBe('„<img src=x onerror=alert(1)> TV“')
  expect(page.dialog!.querySelector('img')).toBeNull()
})

test('says how long ago the device asked', async () => {
  server.answers[LOOKUP] = { ...PREVIEW, requested_at: new Date(Date.now() - 3 * 60_000 - 5_000).toISOString() }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  expect(page.text).toContain('Vor 3 Minuten angefragt.')
})

test('"Connect" confirms with the id and the code typed, then waits for the device and says Connected', async () => {
  const closed = vi.fn()
  await page.show(<DeviceCodeConnect onClose={closed} />)
  await typeAndAsk('ABCD-2345')
  await page.click(page.button('Verbinden'))
  expect(sent(CONFIRM)).toHaveLength(1)
  expect(sent(CONFIRM)[0].body).toEqual({ code: 'ABCD-2345' })
  expect(page.dialog!.querySelector('[data-testid="device-waiting"]')!.textContent).toBe('Bestätigt. Warte auf Living room TV …')
  server.answers[STATE] = { state: 'connected', device: { id: 'd'.repeat(32), name: 'Living room TV' } }
  await later(2100)
  await eventually(() => expect(page.text).toContain('Verbunden: Living room TV'))
  expect(page.text).toContain('lässt sich dort jederzeit trennen')
  expect(page.button('Zurückziehen')).toBeUndefined()
  const asked = sent(STATE).length
  await later(10_000)
  expect(sent(STATE)).toHaveLength(asked)
  await page.click(page.button('Fertig'))
  expect(closed).toHaveBeenCalledTimes(1)
  expect(sent(WITHDRAW)).toHaveLength(0)
})

test('cancelling at the question sends no confirmation', async () => {
  const closed = vi.fn()
  await page.show(<DeviceCodeConnect onClose={closed} />)
  await typeAndAsk()
  await page.click(page.button('Abbrechen'))
  expect(closed).toHaveBeenCalledTimes(1)
  expect(sent(CONFIRM)).toHaveLength(0)
  expect(server.changes()).toEqual(['POST /api/v1/devices/pair-requests/lookup'])
})

test.each([
  ['pairing_unknown', 404, 'Diesen Code gibt es nicht oder er ist abgelaufen.'],
  ['pairing_expired', 410, 'Dieser Code ist abgelaufen.'],
  ['pairing_used', 410, 'Dieser Code wurde schon verwendet.'],
  ['pairing_code', 422, 'Ein Code hat acht Zeichen'],
  ['too_many_attempts', 429, 'Zu viele Versuche.'],
  ['too_many_codes', 429, 'Auf diesem Server wurden zu viele falsche Codes eingegeben.'],
  ['second_factor_setup_required', 403, 'Richte zuerst deinen zweiten Faktor ein.'],
])('tells in words what %s means and stays at the field', async (code, status, text) => {
  server.fails[LOOKUP] = { status, code }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  expect(page.dialog!.querySelector('[role="alert"]')!.textContent).toContain(text)
  expect(field()).toBeTruthy()
  expect(page.dialog!.querySelector('[data-testid="device-ask"]')).toBeNull()
  delete server.fails[LOOKUP]
  await typeAndAsk('WXYZ-6789')
  expect(page.dialog!.querySelector('[data-testid="device-name"]')).toBeTruthy()
})

test('a confirmation that fails because the account holds too many devices says so and can be tried again', async () => {
  server.fails[CONFIRM] = { status: 409, code: 'too_many_devices' }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  expect(page.dialog!.querySelector('[role="alert"]')!.textContent).toContain('So viele Geräte sind genug')
  expect(page.dialog!.querySelector('[data-testid="device-ask"]')).toBeTruthy()
  expect(page.dialog!.querySelector('[data-testid="device-waiting"]')).toBeNull()
  delete server.fails[CONFIRM]
  await page.click(page.button('Verbinden'))
  expect(page.dialog!.querySelector('[data-testid="device-waiting"]')).toBeTruthy()
})

test('a request that ran out while waiting says so and offers another code', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  server.answers[STATE] = { state: 'expired', device: null }
  await later(2100)
  await eventually(() => expect(page.dialog!.querySelector('[data-testid="device-expired"]')).toBeTruthy())
  expect(page.text).toContain('Der Code ist abgelaufen.')
  await page.click(page.button('Anderen Code eingeben'))
  expect(field()!.value).toBe('')
  expect(page.dialog!.querySelector('[data-testid="device-waiting"]')).toBeNull()
})

test('a request the server no longer knows counts as run out', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  delete server.answers[STATE]
  server.fails[STATE] = { status: 404, code: 'not_found' }
  await later(2100)
  await eventually(() => expect(page.dialog!.querySelector('[data-testid="device-expired"]')).toBeTruthy())
})

test('taking the confirmation back asks first, says what happens, and sends nothing before the answer', async () => {
  const closed = vi.fn()
  await page.show(<DeviceCodeConnect onClose={closed} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  await page.click(page.button('Zurückziehen'))
  const question = page.dialog!
  expect(question.textContent).toContain('Bestätigung zurückziehen?')
  expect(question.textContent).toContain('Das Gerät bekommt dann keinen Zugang')
  expect(question.textContent).toContain('trennst du es unter Gerätetoken')
  expect(sent(WITHDRAW)).toHaveLength(0)
  await page.click(page.button('Abbrechen', question))
  expect(sent(WITHDRAW)).toHaveLength(0)
  expect(closed).not.toHaveBeenCalled()
  await page.click(page.button('Zurückziehen'))
  await page.click(page.button('Zurückziehen', page.dialog!))
  expect(sent(WITHDRAW)).toHaveLength(1)
  expect(closed).toHaveBeenCalledTimes(1)
})

test('closing the window while waiting leaves the confirmation as it is', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  page.close()
  await idle()
  expect(sent(WITHDRAW)).toHaveLength(0)
})

test('speaks English when the page does', async () => {
  await changeLanguage('en')
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  expect(page.dialog!.getAttribute('aria-label')).toBe('Connect a device with a code')
  expect(page.text).toContain('Type the code the device shows')
  await page.type(field(), 'abcd-2345')
  await page.click(page.button('Next'))
  expect(page.text).toContain('Connect it to your account?')
  expect(page.text).toContain('Confirm only if you see this code on the screen of the device yourself right now.')
  await page.click(page.button('Connect'))
  expect(page.text).toContain('Confirmed. Waiting for Living room TV')
})

test('the card of the devices has the button, opens the window, and lists again when it closes', async () => {
  server.answers['GET /api/v1/devices'] = []
  await page.show(<Devices />)
  expect(page.dialog).toBeNull()
  await page.click(page.button('Gerät mit Code verbinden'))
  expect(page.dialog!.getAttribute('aria-label')).toBe('Gerät mit Code verbinden')
  expect(server.calls.filter((call) => call.method === 'GET' && call.url === '/api/v1/devices')).toHaveLength(1)
  server.answers['GET /api/v1/devices'] = [{ id: 'd'.repeat(32), name: 'Living room TV', prefix: 'nbd_AbCd', created_at: '2026-10-17T10:00:00Z', last_used_at: null }]
  await page.click(page.dialog!.querySelector<HTMLButtonElement>('[data-close]'))
  expect(page.dialog).toBeNull()
  expect(page.text).toContain('Living room TV')
})

// --- The name is a stranger's text next to a question: it must not look like the system speaking ---

test('the first step warns before a code is typed, in both languages', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  const warning = page.dialog!.querySelector('[data-testid="device-first-warning"]')!
  expect(warning.textContent).toBe('Gib hier nur einen Code ein, den du selbst auf deinem Fernseher siehst.')
  page.close()
  await changeLanguage('en')
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  expect(page.dialog!.querySelector('[data-testid="device-first-warning"]')!.textContent).toBe(
    'Only type a code here that you see yourself on your own television.',
  )
})

test('the name stands under "Das Gerät nennt sich", in quotation marks, as one line of body text and never as a heading', async () => {
  server.answers[LOOKUP] = { ...PREVIEW, device_name: 'Dein Konto wurde gesperrt. Jetzt bestätigen! Dein Konto wurde gesperrt. Jetzt' }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  const ask = page.dialog!.querySelector('[data-testid="device-ask"]')!
  expect(ask.textContent).toContain('Das Gerät nennt sich')
  const name = ask.querySelector<HTMLElement>('[data-testid="device-name"]')!
  expect(name.tagName).toBe('P')
  expect(name.getAttribute('dir')).toBe('auto') // an Arabic or Hebrew name sets its own direction
  expect(name.closest('h1, h2, h3, h4, h5, h6')).toBeNull()
  expect(name.textContent!.startsWith('„') && name.textContent!.endsWith('“')).toBe(true)
  // The size of the text around it, not a headline: no display font, no size above the base one.
  expect(name.className).toContain('text-base')
  expect(name.className).not.toMatch(/font-display|text-(xl|2xl|3xl|4xl)/)
  // One line that is cut off, so a long name cannot cover the question below it.
  expect(name.className).toContain('overflow-hidden')
  expect(name.className).toContain('whitespace-nowrap')
  expect(name.className).toContain('text-ellipsis')
  expect(name.title).toBe(server.answers[LOOKUP] && (server.answers[LOOKUP] as { device_name: string }).device_name)
  expect(name.compareDocumentPosition(ask.querySelector('[data-testid="device-warning"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('the name in English stands in English quotation marks under "The device calls itself"', async () => {
  await changeLanguage('en')
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await page.type(field(), 'abcd-2345')
  await page.click(page.button('Next'))
  expect(page.text).toContain('The device calls itself')
  expect(page.dialog!.querySelector('[data-testid="device-name"]')!.textContent).toBe('“Living room TV”')
})

// --- The keyboard: the focus stays in the window and lands on the next thing after a step ---

test('after "Weiter" the focus is on the main button of the question, not on the page', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  expect(document.activeElement).toBe(field())
  await typeAndAsk()
  expect(page.dialog!.querySelector('[data-testid="device-ask"]')).toBeTruthy()
  expect(document.activeElement).toBe(page.button('Verbinden'))
  await page.click(page.button('Verbinden'))
  await eventually(() => expect(page.dialog!.contains(document.activeElement)).toBe(true))
  expect(document.activeElement).not.toBe(document.body)
})

test('after a code that does not exist the focus stays at the field', async () => {
  server.fails[LOOKUP] = { status: 404, code: 'pairing_unknown' }
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  expect(document.activeElement).not.toBe(document.body)
  expect(page.dialog!.contains(document.activeElement)).toBe(true)
})

test('after "Anderen Code eingeben" the focus goes to the field again', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  await page.click(page.button('Verbinden'))
  server.answers[STATE] = { state: 'expired', device: null }
  await later(2100)
  await eventually(() => expect(page.dialog!.querySelector('[data-testid="device-expired"]')).toBeTruthy())
  await page.click(page.button('Anderen Code eingeben'))
  expect(document.activeElement).toBe(field())
})

function press(key: string, options: KeyboardEventInit = {}, on: Element = document.activeElement!): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options })
  on.dispatchEvent(event)
  return event
}

test('the focus cannot leave the window with Tab or Shift+Tab', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  const inside = Array.from(page.dialog!.querySelectorAll<HTMLElement>('button:not(:disabled), input'))
  const first = inside[0]
  const last = inside[inside.length - 1]
  last.focus()
  const forward = press('Tab')
  expect(forward.defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(first)
  const backward = press('Tab', { shiftKey: true })
  expect(backward.defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(last)
  // In the middle the browser moves the focus itself.
  inside[1].focus()
  expect(press('Tab').defaultPrevented).toBe(false)
  // From the page behind it comes back in.
  ;(document.activeElement as HTMLElement).blur()
  expect(document.activeElement).toBe(document.body)
  expect(press('Tab', {}, document.body).defaultPrevented).toBe(true)
  expect(page.dialog!.contains(document.activeElement)).toBe(true)
})

test('a key held down does not press the main button again and again', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  const connect = page.button('Verbinden')!
  expect(document.activeElement).toBe(connect)
  expect(press('Enter', { repeat: true }).defaultPrevented).toBe(true)
  expect(press(' ', { repeat: true }).defaultPrevented).toBe(true)
  expect(press('Enter').defaultPrevented).toBe(false)
  expect(sent(CONFIRM)).toHaveLength(0)
})

test.each([
  ['de', 'Noch nie benutzt', 'Zuletzt benutzt'],
  ['en', 'Never used', 'Last used'],
])('the list says "%s: never used" for a device whose token nobody has used, and the time for one that was', async (language, never, last) => {
  // A television whose answer with the token was lost stands in the list and was never used: that is how the person finds it.
  await changeLanguage(language)
  server.answers['GET /api/v1/devices'] = [
    { id: 'a'.repeat(32), name: 'Lost TV', prefix: 'nbd_AbCd', created_at: '2026-10-17T10:00:00Z', last_used_at: null },
    { id: 'b'.repeat(32), name: 'Pixel', prefix: 'nbd_EfGh', created_at: '2026-10-17T10:00:00Z', last_used_at: '2026-10-17T11:00:00Z' },
  ]
  await page.show(<Devices />)
  const rows = Array.from(page.dialog ? [] : document.querySelectorAll('[data-testid="device-tokens"] li'))
  expect(rows).toHaveLength(2)
  expect(rows[0].textContent).toContain('Lost TV')
  expect(rows[0].textContent).toContain(never)
  expect(rows[1].textContent).toContain(last)
  expect(rows[1].textContent).not.toContain(never)
})

// --- Two clicks at once ---

test('two clicks on "Verbinden" before the page has drawn the waiting button send one confirmation', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  const connect = page.button('Verbinden')!
  await act(async () => {
    connect.click()
    connect.click()
  })
  expect(sent(CONFIRM)).toHaveLength(1)
})

test('two clicks on "Weiter" or two Enters in the field send one question', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await page.type(field(), 'abcd-2345')
  const next = page.button('Weiter')!
  await act(async () => {
    next.click()
    next.click()
    field()!.form!.requestSubmit()
  })
  expect(sent(LOOKUP)).toHaveLength(1)
})

test('"Fertig" and the cross close the window once each, whatever happened inside', async () => {
  const closed = vi.fn()
  await page.show(<DeviceCodeConnect onClose={closed} />)
  await page.click(page.dialog!.querySelector<HTMLButtonElement>('[data-close]'))
  expect(closed).toHaveBeenCalledTimes(1)
})

test('a click on text in the window is no loss of the focus that the window makes good', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  const panel = page.dialog!
  const touch = () => panel.appendChild(document.createElement('span'))
  // The control the focus was on goes (a step) and the focus is on the page: the window brings it back in.
  field()!.blur()
  expect(document.activeElement).toBe(document.body)
  await act(async () => {
    touch()
  })
  await eventually(() => expect(panel.contains(document.activeElement)).toBe(true))
  // The person clicked on text that cannot take the focus: the focus on the page is what they chose.
  await act(async () => {
    panel.querySelector('h2')!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    ;(document.activeElement as HTMLElement).blur()
    touch()
  })
  await idle()
  expect(document.activeElement).toBe(document.body)
})

test('a busy button that lost the focus does not hand it to "Abbrechen" while it waits, and the window takes it back after', async () => {
  await page.show(<DeviceCodeConnect onClose={() => undefined} />)
  await typeAndAsk()
  const connect = page.button('Verbinden')!
  expect(document.activeElement).toBe(connect)
  // What a browser does with a focused button that turns disabled: the focus is on the page.
  await act(async () => {
    connect.blur()
    connect.disabled = true
    panelTouch()
  })
  await idle()
  expect(document.activeElement).toBe(document.body)
  await act(async () => {
    connect.disabled = false
    panelTouch()
  })
  await eventually(() => expect(page.dialog!.contains(document.activeElement)).toBe(true))
  expect(document.activeElement).not.toBe(document.body)
})

function panelTouch(): void {
  page.dialog!.appendChild(document.createElement('span'))
}
