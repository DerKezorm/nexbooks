/**
 * Everything that deletes or locks out asks first: for each place that has no test of its own for it, the
 * click on the button sends nothing and shows what happens, "Abbrechen" sends nothing, and the red button sends the one
 * request. The places that have such a test in their own file: the account (DeleteAccount), the sessions and the
 * device tokens (Security, Account).
 */
import type { Me } from '../api/client'
import { ApiKeys } from '../components/ApiKeys'
import { changeLanguage } from '../i18n'
import { Page, Server, plain } from '../test/harness'
import { AccountPage } from './AccountPage'
import { AccountsCard, LanguagesCard, useServerSettings } from './settings/ServerCards'
import { SignInPart } from './settings/SignInCards'

const me: Me = {
  id: 1, name: 'jule', display_name: 'Jule', role: 'operator', sign_in: 'password', email: 'jule@example.com', language: 'de',
  email_confirmed: true, email_pending: '', oidc_linked: true, two_factor: true, totp: true, passkeys: 0, two_factor_recovery_left: 8,
  avatar: 'a1', version: '0.0.1', whats_new_seen: '0.0.1', session_stage: 'full', second_factor_required: false, mail: true,
  profile: { theme: 'system', view: 'shelf', market: '', language: '', timezone: 'Europe/Berlin', timezone_source: 'browser', notify_login: true },
}
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), refresh: vi.fn(async () => undefined), signOut: vi.fn(async () => undefined) }
vi.mock('../state/auth', () => ({ useAuth: () => auth, safeNext: (next: string | null) => next ?? '/' }))

const server = new Server()
const page = new Page()

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  server.answers = {
    'GET /api/v1/auth/methods': { password: true, providers: [{ slug: 'authentik', label: 'authentik' }], passkeys: true },
    'GET /api/v1/oidc/me': [{ slug: 'authentik', label: 'authentik', linked: true, address: null }],
    'GET /api/v1/auth/passkeys': [],
    'GET /api/v1/auth/sessions': [],
    'GET /api/v1/locales': [{ code: 'es', name: 'Español', keys: 120 }],
    'GET /api/v1/api-keys': {
      allowed: true,
      keys: [{ id: 4, name: 'Scanner im Flur', prefix: 'nbk_ab12', scope: 'read', created_at: '2026-10-01T10:00:00Z', last_used_at: null, expires_at: null, blocked: false }],
    },
    'GET /api/v1/devices': [],
    'GET /api/v1/push': { key: '', devices: [] },
    'DELETE /api/v1/auth/avatar': { ...me, avatar: null },
    'PUT /api/v1/me/email': { ...me, email: '' },
    'DELETE /api/v1/oidc/authentik/link': 204,
    'DELETE /api/v1/api-keys/4': 204,
    'DELETE /api/v1/locales/es': 204,
    'DELETE /api/v1/oidc/admin/providers/3': { count: 1, only: 0, only_names: [] },
    'DELETE /api/v1/invites/9': 204,
  }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

/** The click on the trigger sends nothing; Abbrechen sends nothing; the red button sends exactly what is named. */
async function asksFirst(trigger: () => HTMLElement | undefined, red: string, sent: string) {
  await page.click(trigger())
  expect(page.dialog).toBeTruthy()
  expect(server.changes()).toEqual([])
  await page.inDialog('Abbrechen')
  expect(page.dialog).toBeNull()
  expect(server.changes()).toEqual([])
  await page.click(trigger())
  await page.inDialog(red)
  expect(server.changes()).toEqual([sent])
}

describe('my account', () => {
  it('asks before the profile picture goes', async () => {
    await page.show(<AccountPage />, '/account')
    await asksFirst(() => page.button('Entfernen'), 'Bild entfernen', 'DELETE /api/v1/auth/avatar')
  })

  it('asks before the mail address goes, and names it', async () => {
    await page.show(<AccountPage />, '/account')
    // The password is asked again for a change of the address.
    await page.type(document.querySelector<HTMLInputElement>('input[type="password"]'), 'geheimwort')
    await page.click(page.button('Adresse entfernen'))
    expect(plain(page.dialog!.textContent)).toContain('Die Adresse jule@example.com wird gelöscht.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
  })

  it('asks before the link to the provider is let go', async () => {
    await page.show(<AccountPage />, '/account?tab=security')
    await asksFirst(() => page.button('Lösen'), 'Verknüpfung lösen', 'DELETE /api/v1/oidc/authentik/link')
  })

  it('asks before an API key is deleted, and names it', async () => {
    await page.show(<ApiKeys />, '/account?tab=devices')
    await page.click(page.button('Schlüssel „Scanner im Flur“ löschen') ?? document.querySelector<HTMLElement>('button[aria-label*="Scanner im Flur"]'))
    expect(plain(page.dialog!.textContent)).toContain('Programme mit dem Schlüssel „Scanner im Flur“ kommen danach nicht mehr in nexbooks.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    await page.click(document.querySelector<HTMLElement>('button[aria-label*="Scanner im Flur"]'))
    await page.inDialog('Schlüssel löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/api-keys/4'])
  })
})

describe('the operator', () => {
  it('asks before a language is removed', async () => {
    await page.show(<LanguagesCard />)
    await asksFirst(() => page.button('Español entfernen') ?? document.querySelector<HTMLElement>('button[aria-label*="Español"]') ?? undefined, 'Sprache entfernen', 'DELETE /api/v1/locales/es')
  })

  it('asks before an invitation is withdrawn', async () => {
    server.answers['GET /api/v1/accounts'] = []
    server.answers['GET /api/v1/invites'] = [{ id: 9, email: 'neu@example.com', expires_at: '2026-10-20T10:00:00Z' }]
    await page.show(<AccountsCard />)
    await asksFirst(() => page.button('Zurückziehen'), 'Einladung zurückziehen', 'DELETE /api/v1/invites/9')
  })

  it('asks before a sign-in provider is removed, with the number of accounts that lose their link', async () => {
    server.answers['GET /api/v1/oidc/admin/providers'] = [{
      id: 3, slug: 'authentik', label: 'authentik', issuer: 'https://auth.example.com/application/o/nexbooks', client_id: 'nexbooks',
      has_secret: true, scopes: 'openid profile email', enabled: true, auto_create: false, trusts_second_factor: true, managed: 'authentik',
      position: 0, redirect_uri: 'https://books.example.com/api/v1/oidc/authentik/callback', links: 1, editable: true,
    }]
    server.answers['GET /api/v1/oidc/admin/providers/3/impact'] = { issuer_change: 0, count: 1, only: 0, only_names: [] }
    server.answers['GET /api/v1/oidc/authentik/address'] = { public_url: 'https://books.example.com', seen: 'https://books.example.com', forwarded_proto: '', forwarded_host: '', proxy_ignored: false, callback_path: '/api/v1/oidc/authentik/callback', redirect_uri: '', registered_redirect_uri: '', stale: false }
    server.answers['GET /api/v1/settings'] = { public_url: 'https://books.example.com', password_login: true, two_factor_required: true }
    function Cards() {
      const settings = useServerSettings()
      return settings.settings ? <SignInPart server={settings} /> : null
    }
    await page.show(<Cards />)
    await asksFirst(() => page.button('Entfernen'), 'Entfernen', 'DELETE /api/v1/oidc/admin/providers/3')
  })
})
