/**
 * My account in the interface, against a stand-in of the server: the four tabs in their order and in the address, the
 * mail address with the link it waits for (the password asked again, the old address standing until the link is
 * opened, withdrawn on request), device tokens (shown once, then only their first characters, removed after asking),
 * API keys (off until the operator allows them, the key shown once) and the choices of the look.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import type { Me } from '../api/client'
import '../i18n'
import { changeLanguage } from '../i18n'
import { idle } from '../test/wait'
import { AccountPage } from './AccountPage'

const baseMe: Me = {
  id: 1, name: 'jule', display_name: 'Jule', role: 'operator', sign_in: 'password', email: '', language: 'de',
  email_confirmed: false, email_pending: '', oidc_linked: false, two_factor: true, totp: true, passkeys: 0, two_factor_recovery_left: 8,
  avatar: null, version: '0.0.1', whats_new_seen: '0.0.1', session_stage: 'full', second_factor_required: false, mail: true,
  profile: { theme: 'system', view: 'shelf', market: '', language: '', timezone: 'Europe/Berlin', timezone_source: 'browser', notify_login: true },
}
const auth = { status: 'signedIn', me: baseMe as Me | null, setMe: vi.fn(), refresh: vi.fn(async () => undefined), signOut: vi.fn(async () => undefined) }
vi.mock('../state/auth', () => ({ useAuth: () => auth, safeNext: (next: string | null) => next ?? '/' }))

type Call = { method: string; url: string; body: Record<string, unknown> | undefined }
let calls: Call[] = []
let answers: Record<string, unknown> = {}
let fails: Record<string, { status: number; code: string }> = {}

function serve(): void {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, url, body })
      const failed = fails[`${method} ${url}`]
      if (failed) return new Response(JSON.stringify({ detail: { code: failed.code } }), { status: failed.status, headers: { 'Content-Type': 'application/json' } })
      const found = answers[`${method} ${url}`]
      if (found === 204) return new Response(null, { status: 204 })
      return new Response(JSON.stringify(found ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
}

let root: Root
let box: HTMLDivElement

async function show(at = '/account'): Promise<void> {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  box = document.createElement('div')
  document.body.appendChild(box)
  root = createRoot(box)
  await act(async () => root.render(<MemoryRouter initialEntries={[at]}><AccountPage /></MemoryRouter>))
  await idle()
}

function button(text: string, within: ParentNode = box): HTMLButtonElement | undefined {
  return [...within.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text || item.getAttribute('aria-label') === text)
}

async function click(target: HTMLElement | undefined): Promise<void> {
  expect(target).toBeTruthy()
  await act(async () => target!.click())
  await idle()
}

async function type(input: HTMLInputElement | null | undefined, value: string): Promise<void> {
  expect(input).toBeTruthy()
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!
  await act(async () => {
    setter.call(input, value)
    input!.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await idle()
}

function field(label: string, within: ParentNode = box): HTMLInputElement | null {
  const found = [...within.querySelectorAll('label')].find((item) => item.textContent?.startsWith(label))
  return found?.querySelector('input') ?? null
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = baseMe
  auth.setMe.mockClear()
  answers = {
    'GET /api/v1/auth/methods': { password: true, providers: [], passkeys: true },
    'GET /api/v1/auth/passkeys': [],
    'GET /api/v1/auth/sessions': [],
    'GET /api/v1/locales': [],
    'GET /api/v1/api-keys': { allowed: false, keys: [] },
    'GET /api/v1/devices': [],
    'GET /api/v1/push': { key: '', devices: [] },
  }
  fails = {}
  serve()
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

describe('the tabs', () => {
  it('are the four, in their order', async () => {
    await show()
    const tabs = [...box.querySelectorAll('[role="tab"]')].map((item) => item.textContent?.trim())
    expect(tabs).toEqual(['Profil', 'Sicherheit', 'Geräte und Apps', 'Aussehen'])
    expect(box.querySelector('h1')!.textContent).toBe('Mein Konto')
  })

  it('stand in the address and open on the profile without one', async () => {
    await show('/account?tab=devices')
    expect(box.querySelector('[role="tab"][aria-selected="true"]')!.textContent).toContain('Geräte und Apps')
    expect(box.textContent).toContain('Gerätetoken anlegen')
    act(() => root.unmount())
    box.remove()
    await show('/account?tab=nonsense')
    expect(box.querySelector('[role="tab"][aria-selected="true"]')!.textContent).toContain('Profil')
  })

  it('come in English too, with the same four', async () => {
    await changeLanguage('en', false)
    await show()
    expect([...box.querySelectorAll('[role="tab"]')].map((item) => item.textContent?.trim())).toEqual(['Profile', 'Security', 'Devices and apps', 'Look'])
  })
})

describe('the mail address', () => {
  it('asks for the password and waits for the link: the old address stays until it is opened', async () => {
    auth.me = { ...baseMe, email: 'old@example.com', email_confirmed: true }
    answers['PUT /api/v1/me/email'] = { ...baseMe, email: 'old@example.com', email_confirmed: true, email_pending: 'new@example.com' }
    await show()
    expect(box.textContent).toContain('old@example.com')
    expect(box.querySelector('[data-testid="mail-state"]')!.textContent).toBe('bestätigt')
    await type(field('Neue Mailadresse'), 'new@example.com')
    expect(button('Bestätigung schicken')!.disabled).toBe(true)
    await type(field('Passwort'), 'the own password')
    await click(button('Bestätigung schicken'))
    expect(calls.find((call) => call.method === 'PUT' && call.url === '/api/v1/me/email')!.body).toEqual({ email: 'new@example.com', password: 'the own password' })
    expect(auth.setMe).toHaveBeenCalled()
  })

  it('shows what waits, and withdraws it', async () => {
    auth.me = { ...baseMe, email: 'old@example.com', email_confirmed: true, email_pending: 'new@example.com' }
    answers['GET /api/v1/auth/me'] = { ...baseMe, email: 'old@example.com', email_confirmed: true }
    await show()
    const pending = box.querySelector('[data-testid="mail-pending"]')!
    expect(pending.textContent).toContain('Link an new@example.com verschickt.')
    expect(pending.textContent).toContain('Bis du ihn öffnest, bleibt old@example.com.')
    expect(field('Neue Mailadresse')).toBeNull()
    await click(button('Zurückziehen'))
    // Asked first: nothing is sent until the dialog is confirmed.
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement
    expect(dialog.textContent).toContain('new@example.com')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(button('Abbrechen', dialog))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(button('Zurückziehen'))
    await click(button('Anfrage zurückziehen', document.querySelector('[role="dialog"]') as HTMLElement))
    expect(calls.some((call) => call.method === 'DELETE' && call.url === '/api/v1/me/email/pending')).toBe(true)
  })

  it('marks an address nobody confirmed and offers the link', async () => {
    auth.me = { ...baseMe, email: 'typed@example.com', email_confirmed: false }
    await show()
    expect(box.querySelector('[data-testid="mail-state"]')!.textContent).toBe('nicht bestätigt')
    expect(button('Bestätigung schicken')).toBeTruthy()
  })

  it('says what stands in the way, in words', async () => {
    fails['PUT /api/v1/me/email'] = { status: 409, code: 'mail_off' }
    await show()
    await type(field('Neue Mailadresse'), 'new@example.com')
    await type(field('Passwort'), 'the own password')
    await click(button('Bestätigung schicken'))
    expect(box.textContent).toContain('Es ist kein Mailserver eingetragen.')
  })
})

describe('devices and apps', () => {
  const device = { id: 'a'.repeat(32), name: 'Pixel', prefix: 'nbd_AbCd', created_at: '2026-10-08T10:00:00Z', last_used_at: null }

  it('makes a device token that is shown once, and lists only its first characters after', async () => {
    answers['POST /api/v1/devices'] = { device, token: 'nbd_' + 't'.repeat(43) }
    await show('/account?tab=devices')
    await click(button('Gerätetoken anlegen'))
    expect(button('Anlegen')!.disabled).toBe(true)
    await type(field('Name des Geräts'), 'Pixel')
    answers['GET /api/v1/devices'] = [device]
    await click(button('Anlegen'))
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/v1/devices')!.body).toEqual({ name: 'Pixel' })
    const shown = box.querySelector('[data-testid="device-token-shown"]')!
    expect(shown.textContent).toContain('nbd_' + 't'.repeat(43))
    expect(shown.textContent).toContain('zeigt nexbooks ihn nicht mehr')
    await click(button('Fertig'))
    expect(box.textContent).not.toContain('t'.repeat(43))
    expect(box.querySelector('[data-testid="device-tokens"]')!.textContent).toContain('nbd_AbCd')
  })

  it('asks before a device token is removed, says it cannot be brought back, and sends nothing before the answer', async () => {
    answers['GET /api/v1/devices'] = [device]
    answers['DELETE /api/v1/devices/' + device.id] = 204
    await show('/account?tab=devices')
    await click(button('Pixel entfernen'))
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('Der Token lässt sich nicht zurückholen')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(button('Abbrechen', dialog as HTMLElement))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(button('Pixel entfernen'))
    answers['GET /api/v1/devices'] = []
    await click(button('Entfernen', document.querySelector('[role="dialog"]') as HTMLElement))
    expect(calls.some((call) => call.method === 'DELETE' && call.url === '/api/v1/devices/' + device.id)).toBe(true)
  })

  it('says through which device a television came, and that removing that device takes it along', async () => {
    const phone = { ...device, id: 'b'.repeat(32), name: "Ada's iPhone" }
    const tv = { ...device, id: 'c'.repeat(32), name: 'Living room TV', via: { id: phone.id, name: phone.name } }
    answers['GET /api/v1/devices'] = [tv, phone, device]
    answers['DELETE /api/v1/devices/' + phone.id] = 204
    await show('/account?tab=devices')
    const rows = Array.from(box.querySelectorAll('[data-testid="device-tokens"] li'))
    expect(rows[0].querySelector('[data-testid="device-via"]')!.textContent).toContain("Verbunden über ⁨Ada's iPhone⁩")
    expect(rows[1].querySelector('[data-testid="device-via"]')).toBeNull()
    expect(rows[2].querySelector('[data-testid="device-via"]')).toBeNull()
    // Removing the phone says what goes with it; removing another device says nothing of the kind.
    await click(button("Ada's iPhone entfernen"))
    let dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('Mit dem Gerät geht auch ⁨Living room TV⁩, das es verbunden hat.')
    expect(dialog.textContent).toContain('lassen sich nicht zurückholen')
    await click(button('Abbrechen', dialog as HTMLElement))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(button('Pixel entfernen'))
    dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).not.toContain('Mit dem Gerät geht')
    await click(button('Abbrechen', dialog as HTMLElement))
    await click(button("Ada's iPhone entfernen"))
    answers['GET /api/v1/devices'] = [device]
    await click(button('Entfernen', document.querySelector('[role="dialog"]') as HTMLElement))
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.url)).toEqual(['/api/v1/devices/' + phone.id])
  })

  it('names every device that goes along, in English too', async () => {
    const phone = { ...device, id: 'b'.repeat(32), name: 'Phone' }
    const tvs = ['One', 'Two'].map((name, index) => ({ ...device, id: String(index).repeat(32), name, via: { id: phone.id, name: 'Phone' } }))
    answers['GET /api/v1/devices'] = [...tvs, phone]
    await changeLanguage('en', false)
    await show('/account?tab=devices')
    expect(box.querySelector('[data-testid="device-via"]')!.textContent).toContain('Connected through')
    await click(button('Remove Phone'))
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('⁨One⁩, ⁨Two⁩, which this device connected, go with it.')
  })

  it('keeps API keys off until the operator allows them, and says where', async () => {
    await show('/account?tab=devices')
    expect(box.querySelector('[data-testid="api-keys-off"]')!.textContent).toContain('API-Schlüssel sind noch nicht freigegeben.')
    expect(box.querySelector('[data-testid="api-keys-off"] a')!.getAttribute('href')).toBe('/settings?tab=api')
  })

  it('lets the operator allow API keys right there, after asking what it means', async () => {
    answers['PUT /api/v1/settings'] = { api_keys_allowed: true }
    await show('/account?tab=devices')
    await click(button('Jetzt freigeben'))
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('Danach kann jede Person auf diesem Server unter Mein Konto Schlüssel anlegen.')
    expect(dialog.textContent).toContain('jederzeit wieder ausschalten')
    // Nothing was sent before the answer.
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    answers['GET /api/v1/api-keys'] = { allowed: true, keys: [] }
    await click(button('Freigeben', dialog))
    expect(calls.find((call) => call.method === 'PUT' && call.url === '/api/v1/settings')!.body).toEqual({ api_keys_allowed: true })
    expect(box.querySelector('[data-testid="api-keys-off"]')).toBeNull()
    expect(button('Neuer Schlüssel')).toBeTruthy()
  })

  it('asks first and sends nothing when the operator says no', async () => {
    await show('/account?tab=devices')
    await click(button('Jetzt freigeben'))
    await click(button('Abbrechen', document.querySelector('[role="dialog"]')!))
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    expect(box.querySelector('[data-testid="api-keys-off"]')).toBeTruthy()
  })

  it('offers no way to allow API keys to somebody who is not the operator', async () => {
    auth.me = { ...baseMe, role: 'member' }
    await show('/account?tab=devices')
    expect(box.querySelector('[data-testid="api-keys-off"]')!.textContent).toContain('Der Betreiber hat API-Schlüssel nicht freigegeben.')
    expect(button('Jetzt freigeben')).toBeUndefined()
  })

  it('makes a key that is shown once, with its header line, and lists only its first characters after', async () => {
    answers['GET /api/v1/api-keys'] = { allowed: true, keys: [] }
    answers['POST /api/v1/api-keys'] = {
      key: { id: 1, name: 'nexdeck', scope: 'read', prefix: 'nbk_AbCd', created_at: '2026-10-08T10:00:00Z', last_used_at: null, expires_at: null, blocked: false },
      secret: 'nbk_' + 's'.repeat(43),
    }
    await show('/account?tab=devices')
    await click(button('Neuer Schlüssel'))
    await type(field('Name'), 'nexdeck')
    answers['GET /api/v1/api-keys'] = { allowed: true, keys: [(answers['POST /api/v1/api-keys'] as { key: unknown }).key] }
    await click(button('Schlüssel anlegen'))
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/v1/api-keys')!.body).toEqual({ name: 'nexdeck', days: 365, scope: 'read' })
    const shown = box.querySelector('[data-testid="api-key-shown"]')!
    expect(shown.textContent).toContain('Authorization: Bearer nbk_' + 's'.repeat(43))
    await click(button('Fertig'))
    expect(box.textContent).not.toContain('s'.repeat(43))
    expect(box.querySelector('[data-testid="api-key"]')!.textContent).toContain('nbk_AbCd')
  })

  it('offers a year as the default lifetime, says why, and still lets the key run without an end', async () => {
    answers['GET /api/v1/api-keys'] = { allowed: true, keys: [] }
    answers['POST /api/v1/api-keys'] = {
      key: { id: 3, name: 'forever', scope: 'read', prefix: 'nbk_Ever', created_at: '2026-10-08T10:00:00Z', last_used_at: null, expires_at: null, blocked: false },
      secret: 'nbk_' + 'e'.repeat(43),
    }
    await show('/account?tab=devices')
    await click(button('Neuer Schlüssel'))
    expect(box.textContent).toContain('Ein Schlüssel, der nie abläuft, ist der, an den sich niemand mehr erinnert')
    const chosen = [...box.querySelectorAll('[role="radio"]')].filter((item) => item.getAttribute('aria-checked') === 'true').map((item) => item.textContent)
    expect(chosen).toContain('365 Tage')
    await type(field('Name'), 'forever')
    await click(button('Ohne Ablauf'))
    await click(button('Schlüssel anlegen'))
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/v1/api-keys')!.body).toEqual({ name: 'forever', days: null, scope: 'read' })
  })

})

describe('the look', () => {
  it('keeps light or dark and the view of the library with the account', async () => {
    answers['PUT /api/v1/me/preferences'] = { ...baseMe.profile, theme: 'dark' }
    await show('/account?tab=looks')
    expect(box.textContent).toContain('Hell oder dunkel')
    await click(button('Dunkel'))
    expect(calls.find((call) => call.url === '/api/v1/me/preferences')!.body).toEqual({ theme: 'dark' })
    answers['PUT /api/v1/me/preferences'] = { ...baseMe.profile, view: 'grid' }
    await click(button('Cover'))
    expect(calls.filter((call) => call.url === '/api/v1/me/preferences').at(-1)!.body).toEqual({ view: 'grid' })
    // There is one accent and no palette to choose.
    expect(box.textContent).not.toMatch(/Salbei|Terrakotta|Pflaume|Altrosa|Tinte/)
  })
})
