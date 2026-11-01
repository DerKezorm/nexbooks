/**
 * Deleting an account under Settings, Accounts, against a stand-in of the server: the dialog says what goes and that it
 * cannot be brought back, the name has to be typed as it is, the operator's password is asked, and what is sent is
 * exactly that.
 */
import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server } from '../../test/harness'
import { AccountsCard } from './ServerCards'

const me: Me = {
  id: 1, name: 'jule', display_name: 'Jule', role: 'operator', sign_in: 'password', email: '', language: 'de', email_confirmed: false,
  email_pending: '', oidc_linked: false, two_factor: true, totp: true, passkeys: 0, two_factor_recovery_left: 8, avatar: null, version: '0.0.1',
  whats_new_seen: '0.0.1', session_stage: 'full', profile: { theme: 'system', view: 'shelf', market: '', language: '', timezone: 'Europe/Berlin', timezone_source: 'browser', notify_login: true },
}
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), refresh: vi.fn(async () => undefined), signOut: vi.fn(async () => undefined) }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()
const row = (over: Record<string, unknown>) => ({ ...me, locked: false, has_password: true, created_at: '2026-10-01T10:00:00Z', last_seen_at: null, ...over })

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  server.answers = {
    'GET /api/v1/accounts': [row({}), row({ id: 2, name: 'tom', display_name: 'Tom', role: 'member' }), row({ id: 3, name: 'lea', display_name: 'Lea', role: 'member' })],
    'GET /api/v1/invites': [],
    'DELETE /api/v1/accounts/2': 204,
  }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

async function openFor(name: string): Promise<HTMLElement> {
  await page.show(<AccountsCard />, '/settings?tab=accounts')
  await page.click(page.button(`Mehr für ${name}`))
  await page.click(page.button('Konto löschen'))
  return document.querySelector<HTMLElement>('[role="dialog"]')!
}

function field(dialog: HTMLElement, label: string): HTMLInputElement {
  const found = [...dialog.querySelectorAll('label')].find((l) => l.textContent?.startsWith(label))
  return found!.querySelector('input')!
}

function deleteButton(dialog: HTMLElement): HTMLButtonElement {
  return [...dialog.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Konto löschen')!
}

describe('deleting an account', () => {
  it('says what goes and that it cannot be brought back, and sends nothing until the name and the password are given', async () => {
    const dialog = await openFor('tom')
    expect(dialog.textContent).toContain('Das Konto von tom wird gelöscht.')
    expect(dialog.textContent).toContain('lassen sich nicht zurückholen. Die Bücher bleiben, wo sie liegen.')
    expect(deleteButton(dialog).disabled).toBe(true)
    await page.type(field(dialog, 'Zum Bestätigen'), 'Tom')
    await page.type(field(dialog, 'Dein eigenes Passwort'), 'own password')
    expect(deleteButton(dialog).disabled).toBe(true)
    await page.type(field(dialog, 'Zum Bestätigen'), 'tom')
    expect(deleteButton(dialog).disabled).toBe(false)
    expect(server.changes()).toEqual([])
    await page.click(deleteButton(dialog))
    expect(server.called('DELETE', '/api/v1/accounts/2')!.body).toEqual({ current_password: 'own password', confirm_name: 'tom' })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('asks for the password only where the operator has one', async () => {
    auth.me = { ...me, sign_in: 'oidc' }
    const dialog = await openFor('tom')
    expect([...dialog.querySelectorAll('input[type="password"]')]).toHaveLength(0)
    await page.type(field(dialog, 'Zum Bestätigen'), 'tom')
    expect(deleteButton(dialog).disabled).toBe(false)
  })

  it('nothing is sent when the operator cancels', async () => {
    const dialog = await openFor('tom')
    await page.type(field(dialog, 'Zum Bestätigen'), 'tom')
    await page.type(field(dialog, 'Dein eigenes Passwort'), 'own password')
    await page.click([...dialog.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Abbrechen'))
    expect(server.changes()).toEqual([])
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('shows what the server refused and keeps the dialog', async () => {
    server.fails['DELETE /api/v1/accounts/2'] = { status: 422, code: 'name_mismatch' }
    const dialog = await openFor('tom')
    await page.type(field(dialog, 'Zum Bestätigen'), 'tom')
    await page.type(field(dialog, 'Dein eigenes Passwort'), 'own password')
    await page.click(deleteButton(dialog))
    expect(dialog.textContent).toContain('Der Name stimmt nicht.')
    expect(document.querySelector('[role="dialog"]')).toBeTruthy()
  })

  it('speaks English too', async () => {
    await changeLanguage('en', false)
    await page.show(<AccountsCard />, '/settings?tab=accounts')
    await page.click(page.button('More for tom'))
    await page.click(page.button('Delete account'))
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    expect(dialog.textContent).toContain('The account of tom will be deleted.')
    expect(dialog.textContent).toContain('The books stay where they lie.')
  })
})
