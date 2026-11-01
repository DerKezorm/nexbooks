/**
 * E-readers on the account page, against a stand-in of the server: the rows in their order (Kindle, the catalogue
 * for reading apps, the sync of KOReader, the sync of a Kobo), what each row says about how far it is set up, the Kindle
 * address and the test mail, a connection made and shown once (address, name, password; for a Kobo the address that holds
 * the secret and the line for its settings file), a connection deleted only after asking, and the page when the operator
 * has switched it all off.
 */
import { changeLanguage } from '../i18n'
import { Page, Server } from '../test/harness'
import { EReaders } from './EReaders'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member' } as { id: number; name: string; role: string } | null }
vi.mock('../state/auth', () => ({ useAuth: () => auth, safeNext: (next: string | null) => next ?? '/' }))

const server = new Server()
const page = new Page()

const kindle = { address: '', mail_ready: false, sender: '', max_megabytes: 50 }
const link = { id: 4, kind: 'opds', name: 'Wohnzimmer', prefix: 'nbo_Ab3d', created_at: '2026-10-01T10:00:00Z', last_used_at: null, blocked: false }

function serve(state: Record<string, unknown> = {}) {
  server.answers = { 'GET /api/v1/ereaders': { allowed: true, links: [], kindle, ...state } }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  auth.me = { id: 1, name: 'jule', role: 'member' }
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the rows', () => {
  it('are the Kindle, the catalogue for reading apps, the sync of KOReader and the Kobo, in that order, and say how far each one is', async () => {
    serve()
    await page.show(<EReaders />)
    const rows = [...document.querySelectorAll('[data-testid="ereader-rows"] > li')]
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual(['ereader-kindle', 'ereader-opds', 'ereader-kosync', 'ereader-kobo'])
    expect(document.querySelector('[data-testid="ereader-kindle-status"]')!.textContent).toBe('Noch nicht eingerichtet')
    expect(document.querySelector('[data-testid="ereader-opds-status"]')!.textContent).toBe('Noch keine Verbindung')
    expect(page.button('Einrichten: Kindle')).toBeTruthy()
    expect(document.querySelector('[data-testid="ereader-kosync-status"]')!.textContent).toBe('Noch keine Verbindung')
    expect(page.button('Verbinden: Katalog für Lese-Apps (OPDS)')).toBeTruthy()
    expect(page.button('Verbinden: Lesestand für KOReader')).toBeTruthy()
    expect(document.querySelector('[data-testid="ereader-kobo-status"]')!.textContent).toBe('Noch keine Verbindung')
    expect(page.button('Verbinden: Kobo')).toBeTruthy()
    expect(page.text).not.toMatch(/[–—]/)
  })

  it('marks the Kobo as a beta, and only the Kobo, and says why when its row is open', async () => {
    serve()
    await page.show(<EReaders />)
    expect([...document.querySelectorAll('[data-testid$="-beta"]')].map((mark) => mark.getAttribute('data-testid'))).toEqual(['ereader-kobo-beta'])
    expect(document.querySelector('[data-testid="ereader-kobo-beta"]')!.textContent).toBe('Beta')
    expect(document.querySelector('[data-testid="ereader-kobo-beta-hint"]')).toBeNull()
    await page.click(page.button('Verbinden: Kobo'))
    expect(document.querySelector('[data-testid="ereader-kobo-beta-hint"]')!.textContent).toContain('noch nicht an einem echten Kobo')
  })

  it('show the address of the Kindle and the number of connections', async () => {
    serve({ kindle: { ...kindle, address: 'jule_k9@example.com' }, links: [link, { ...link, id: 5, name: 'Tablet' }] })
    await page.show(<EReaders />)
    expect(document.querySelector('[data-testid="ereader-kindle-status"]')!.textContent).toBe('jule_k9@example.com')
    expect(document.querySelector('[data-testid="ereader-opds-status"]')!.textContent).toBe('2 Verbindungen')
    // Each row counts the connections of its own kind.
    expect(document.querySelector('[data-testid="ereader-kosync-status"]')!.textContent).toBe('Noch keine Verbindung')
    expect(page.button('Ändern: Kindle')).toBeTruthy()
  })

  it('say nothing while the server has not answered with what the page needs', async () => {
    server.answers = {}
    server.install()
    await page.show(<EReaders />)
    expect(document.querySelector('#ereaders')).toBeNull()
  })

  it('are in English too', async () => {
    await changeLanguage('en', false)
    serve()
    await page.show(<EReaders />)
    expect(document.querySelector('[data-testid="ereader-kindle-status"]')!.textContent).toBe('Not set up yet')
    expect(page.button('Connect: Catalogue for reading apps (OPDS)')).toBeTruthy()
    expect(page.button('Connect: Reading progress for KOReader')).toBeTruthy()
    expect(page.button('Connect: Kobo')).toBeTruthy()
    expect(page.text).not.toMatch(/[–—]/)
  })
})

describe('the Kindle', () => {
  it('is set with an address that is saved as typed, without the spaces around it', async () => {
    serve()
    server.answers['PUT /api/v1/ereaders/kindle'] = { ...kindle, address: 'jule_k9@example.com' }
    await page.show(<EReaders />)
    await page.click(page.button('Einrichten: Kindle'))
    await page.type(document.querySelector('input'), '  jule_k9@example.com ')
    await page.click(page.button('Speichern'))
    expect(server.called('PUT', '/api/v1/ereaders/kindle')!.body).toEqual({ address: 'jule_k9@example.com' })
    expect(page.text).toContain('Gespeichert.')
  })

  it('says what the server refused', async () => {
    serve()
    server.fails['PUT /api/v1/ereaders/kindle'] = { status: 422, code: 'invalid_kindle_address' }
    await page.show(<EReaders />)
    await page.click(page.button('Einrichten: Kindle'))
    await page.type(document.querySelector('input'), 'jule@example.com')
    await page.click(page.button('Speichern'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('Das ist keine Kindle-Adresse. Sie endet auf @kindle.com.')
  })

  it('names the sender to allow at Amazon when the server can mail, and offers a test mail only once there is an address', async () => {
    serve({ kindle: { address: '', mail_ready: true, sender: 'books@example.com', max_megabytes: 50 } })
    await page.show(<EReaders />)
    await page.click(page.button('Einrichten: Kindle'))
    expect(document.querySelector('[data-testid="kindle-mail"]')!.textContent).toContain('books@example.com')
    expect(document.querySelector('[data-testid="kindle-mail"]')!.textContent).toContain('Höchstens 50 MB')
    expect(document.querySelector('[data-testid="kindle-web"]')).toBeNull()
    expect(page.button('Testmail senden')).toBeUndefined()
  })

  it('sends a test mail and says it is on its way', async () => {
    serve({ kindle: { address: 'jule_k9@example.com', mail_ready: true, sender: 'books@example.com', max_megabytes: 50 } })
    server.answers['POST /api/v1/ereaders/kindle/test'] = { sent: true }
    await page.show(<EReaders />)
    await page.click(page.button('Ändern: Kindle'))
    await page.click(page.button('Testmail senden'))
    expect(server.changes()).toEqual(['POST /api/v1/ereaders/kindle/test'])
    expect(page.text).toContain('Die Testmail ist unterwegs.')
  })

  it('explains the way by hand when the server cannot mail', async () => {
    serve()
    await page.show(<EReaders />)
    await page.click(page.button('Einrichten: Kindle'))
    expect(document.querySelector('[data-testid="kindle-web"]')!.textContent).toContain('Send to Kindle')
    expect(document.querySelector('[data-testid="kindle-mail"]')).toBeNull()
    expect(page.button('Testmail senden')).toBeUndefined()
  })
})

describe('a connection for a reading app', () => {
  const made = { link, username: 'jule', secret: 'nbo_' + 'Qz'.repeat(16) }

  it('is made with a name, shown once with the address, the name and the password, and then only by its first characters', async () => {
    serve()
    server.answers['POST /api/v1/ereaders/links'] = made
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    await page.click(page.button('Verbindung anlegen'))
    await page.type(document.querySelector('input'), 'Wohnzimmer')
    server.answers['GET /api/v1/ereaders'] = { allowed: true, links: [link], kindle }
    await page.click(page.button('Anlegen'))
    expect(server.called('POST', '/api/v1/ereaders/links')!.body).toEqual({ name: 'Wohnzimmer', kind: 'opds' })
    const shown = document.querySelector('[data-testid="ereader-shown"]')!
    const values = [...shown.querySelectorAll('input')].map((input) => input.value)
    expect(values).toEqual([`${window.location.origin}/opds`, 'jule', made.secret])
    expect(shown.textContent).toContain('nur jetzt')
    await page.click(page.button('Fertig'))
    expect(document.querySelector('[data-testid="ereader-shown"]')).toBeNull()
    // Afterwards only the first characters stand in the list, never the password.
    expect(page.text).toContain('nbo_Ab3d')
    expect(page.text).not.toContain(made.secret)
    expect(document.querySelectorAll('input[value*="Qz"]')).toHaveLength(0)
  })

  it('is not made without a name', async () => {
    serve()
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    await page.click(page.button('Verbindung anlegen'))
    expect(page.button('Anlegen')!.disabled).toBe(true)
    expect(server.changes()).toEqual([])
  })

  it('says why it was not made when the server refuses', async () => {
    serve()
    server.fails['POST /api/v1/ereaders/links'] = { status: 409, code: 'too_many_links' }
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    await page.click(page.button('Verbindung anlegen'))
    await page.type(document.querySelector('input'), 'Noch eine')
    await page.click(page.button('Anlegen'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('So viele Verbindungen sind genug: Lösche zuerst eine.')
  })

  it('is deleted only after the question, which says it cannot be taken back', async () => {
    serve({ links: [link] })
    server.answers['DELETE /api/v1/ereaders/links/4'] = 204
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    await page.click(page.button('Verbindung Wohnzimmer löschen'))
    expect(server.changes()).toEqual([])
    expect(page.dialog!.textContent).toContain('Wohnzimmer')
    expect(page.dialog!.textContent).toContain('Das lässt sich nicht zurückholen.')
    await page.click(page.button('Abbrechen', page.dialog!))
    expect(server.changes()).toEqual([])
    await page.click(page.button('Verbindung Wohnzimmer löschen'))
    await page.inDialog('Verbindung löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/ereaders/links/4'])
  })

  it('shows when the operator blocked it and when it was last used', async () => {
    serve({ links: [{ ...link, blocked: true }, { ...link, id: 5, name: 'Tablet', last_used_at: '2026-10-02T08:30:00Z' }] })
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    expect(document.querySelector('[data-testid="ereader-link-blocked"]')!.textContent).toBe('vom Betreiber gesperrt')
    const rows = [...document.querySelectorAll('[data-testid="ereader-link"]')]
    expect(rows[1].textContent).toContain('zuletzt benutzt')
    expect(rows[1].textContent).not.toContain('noch nie')
  })

  it('cannot be deleted when the operator blocked it: only the operator lifts a block', async () => {
    serve({ links: [{ ...link, blocked: true }, { ...link, id: 5, name: 'Tablet' }] })
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    const rows = [...document.querySelectorAll('[data-testid="ereader-link"]')]
    expect(rows[0].querySelector('button')).toBeNull()
    expect(rows[1].querySelector('button')).not.toBeNull()
  })

  it('says why a new one of that kind was not made when the operator blocked one', async () => {
    serve({ links: [{ ...link, blocked: true }] })
    server.fails['POST /api/v1/ereaders/links'] = { status: 409, code: 'kind_blocked' }
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    await page.click(page.button('Verbindung anlegen'))
    await page.type(document.querySelector('input'), 'Küche')
    await page.click(page.button('Anlegen'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('Der Betreiber hat eine Verbindung dieser Art gesperrt. Frag den Betreiber.')
  })
})

describe('a connection for the sync of KOReader', () => {
  const sync = { ...link, id: 7, kind: 'kosync', name: 'Kobo', prefix: 'nbs_Cd4e' }
  const made = { link: sync, username: 'jule', secret: 'nbs_' + 'Rw'.repeat(16) }

  it('is made as its own kind, shown once with the address of the sync server, the name and the password, and says where to enter them', async () => {
    serve()
    server.answers['POST /api/v1/ereaders/links'] = made
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Lesestand für KOReader'))
    await page.click(page.button('Verbindung anlegen'))
    await page.type(document.querySelector('input'), 'Kobo')
    server.answers['GET /api/v1/ereaders'] = { allowed: true, links: [sync], kindle }
    await page.click(page.button('Anlegen'))
    expect(server.called('POST', '/api/v1/ereaders/links')!.body).toEqual({ name: 'Kobo', kind: 'kosync' })
    const shown = document.querySelector('[data-testid="ereader-shown"]')!
    expect([...shown.querySelectorAll('input')].map((input) => input.value)).toEqual([`${window.location.origin}/kosync`, 'jule', made.secret])
    expect(shown.textContent).toContain('Adresse des Sync-Servers')
    expect(shown.textContent).toContain('Custom sync server')
    expect(shown.textContent).toContain('Binary')
    expect(shown.textContent).not.toMatch(/[–—]/)
    await page.click(page.button('Fertig'))
    expect(page.text).toContain('nbs_Cd4e')
    expect(page.text).not.toContain(made.secret)
    expect(document.querySelector('[data-testid="ereader-kosync-status"]')!.textContent).toBe('1 Verbindung')
  })

  it('lists only its own kind, and the catalogue row lists only the catalogue', async () => {
    serve({ links: [link, sync] })
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Lesestand für KOReader'))
    expect([...document.querySelectorAll('[data-testid="ereader-link"]')].map((row) => row.textContent)).toEqual([expect.stringContaining('Kobo')])
    await page.click(page.button('Schließen: Lesestand für KOReader'))
    await page.click(page.button('Verbinden: Katalog für Lese-Apps (OPDS)'))
    expect([...document.querySelectorAll('[data-testid="ereader-link"]')].map((row) => row.textContent)).toEqual([expect.stringContaining('Wohnzimmer')])
  })

  it('is deleted only after the question', async () => {
    serve({ links: [sync] })
    server.answers['DELETE /api/v1/ereaders/links/7'] = 204
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Lesestand für KOReader'))
    await page.click(page.button('Verbindung Kobo löschen'))
    expect(server.changes()).toEqual([])
    await page.inDialog('Verbindung löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/ereaders/links/7'])
  })

  it('stays in the list of the member when the operator has switched it all off', async () => {
    serve({ allowed: false, links: [sync] })
    await page.show(<EReaders />)
    expect(document.querySelector('[data-testid="ereaders-off"]')).toBeTruthy()
    expect([...document.querySelectorAll('[data-testid="ereader-link"]')].map((row) => row.textContent)).toEqual([expect.stringContaining('Kobo')])
  })
})

describe('a connection for a Kobo', () => {
  const box = { ...link, id: 9, kind: 'kobo', name: 'Wohnzimmer-Kobo', prefix: 'nbe_Ef5g' }
  const made = { link: box, username: 'jule', secret: 'nbe_' + 'Qz'.repeat(16) }

  it('is made as its own kind and shown once as an address that holds the secret, with the line for the settings file and no name or password', async () => {
    serve()
    server.answers['POST /api/v1/ereaders/links'] = made
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Kobo'))
    await page.click(page.button('Verbindung anlegen'))
    await page.type(document.querySelector('input'), 'Wohnzimmer-Kobo')
    server.answers['GET /api/v1/ereaders'] = { allowed: true, links: [box], kindle }
    await page.click(page.button('Anlegen'))
    expect(server.called('POST', '/api/v1/ereaders/links')!.body).toEqual({ name: 'Wohnzimmer-Kobo', kind: 'kobo' })
    const shown = document.querySelector('[data-testid="ereader-shown"]')!
    const address = `${window.location.origin}/kobo/${made.secret}`
    expect([...shown.querySelectorAll('input')].map((input) => input.value)).toEqual([address, `api_endpoint=${address}`])
    // It says in plain words where the line goes and that the address is a password.
    expect(shown.textContent).toContain('Kobo eReader.conf')
    expect(shown.textContent).toContain('[OneStoreServices]')
    expect(shown.textContent).toContain('gib sie also nicht weiter')
    expect(shown.textContent).not.toMatch(/[–—]/)
    expect(shown.textContent).not.toContain('Benutzername')
    await page.click(page.button('Fertig'))
    // Afterwards only the first characters stand on the page.
    expect(page.text).toContain('nbe_Ef5g')
    expect(page.text).not.toContain(made.secret)
    expect(document.querySelector('[data-testid="ereader-kobo-status"]')!.textContent).toBe('1 Verbindung')
  })

  it('warns when the page is seen over https and nexbooks answers as if it were http: a proxy it was not told about', async () => {
    serve({ kobo_base: 'http://books.example.com' })
    vi.stubGlobal('location', new URL('https://books.example.com/account'))
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Kobo'))
    const warning = document.querySelector('[data-testid="ereader-proxy-warning"]')!
    expect(warning.textContent).toContain('NEXBOOKS_PUBLIC_URL')
    expect(warning.textContent).toContain('NEXBOOKS_TRUSTED_PROXIES')
    expect(warning.textContent).toContain('http://books.example.com')
    expect(warning.textContent).not.toMatch(/[–—]/)
  })

  it('does not warn when nexbooks answers with the scheme the page is seen with, and tells of the proxy settings in the text', async () => {
    serve({ kobo_base: 'http://localhost:3000' })
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Kobo'))
    expect(document.querySelector('[data-testid="ereader-proxy-warning"]')).toBeNull()
    expect(page.text).toContain('NEXBOOKS_TRUSTED_PROXIES')
  })

  it('says in the row that the store of Kobo does not work while it points at nexbooks', async () => {
    serve()
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Kobo'))
    expect(page.text).toContain('Der Kobo-Shop und die anderen Dienste von Kobo gehen nicht')
  })

  it('lists only its own kind and is deleted only after the question', async () => {
    serve({ links: [link, box] })
    server.answers['DELETE /api/v1/ereaders/links/9'] = 204
    await page.show(<EReaders />)
    await page.click(page.button('Verbinden: Kobo'))
    expect([...document.querySelectorAll('[data-testid="ereader-link"]')].map((row) => row.textContent)).toEqual([expect.stringContaining('Wohnzimmer-Kobo')])
    await page.click(page.button('Verbindung Wohnzimmer-Kobo löschen'))
    expect(server.changes()).toEqual([])
    await page.inDialog('Verbindung löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/ereaders/links/9'])
  })

  it('stays in the list of the member when the operator has switched it all off', async () => {
    serve({ allowed: false, links: [box] })
    await page.show(<EReaders />)
    expect([...document.querySelectorAll('[data-testid="ereader-link"]')].map((row) => row.textContent)).toEqual([expect.stringContaining('Wohnzimmer-Kobo')])
  })
})

describe('when the operator has switched it off', () => {
  it('says so to a member and shows no rows', async () => {
    serve({ allowed: false })
    await page.show(<EReaders />)
    expect(document.querySelector('[data-testid="ereaders-off"]')!.textContent).toContain('Der Betreiber hat E-Reader ausgeschaltet.')
    expect(document.querySelector('[data-testid="ereader-rows"]')).toBeNull()
    expect(document.querySelector('a')).toBeNull()
  })

  it('points the operator to the switch', async () => {
    auth.me = { id: 1, name: 'ops', role: 'operator' }
    serve({ allowed: false })
    await page.show(<EReaders />)
    const to = document.querySelector('[data-testid="ereaders-off"] a')!
    expect(to.getAttribute('href')).toBe('/settings?tab=api')
  })

  it('still lets a person delete the connections they made', async () => {
    serve({ allowed: false, links: [link] })
    await page.show(<EReaders />)
    expect(page.button('Verbindung Wohnzimmer löschen')).toBeTruthy()
  })
})
