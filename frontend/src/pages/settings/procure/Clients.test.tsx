/**
 * Settings, Fetching, Download programs: the cards, the dialog that tests a connection step by step before it saves, and the
 * path mappings. What removes asks first; a key or password is never shown.
 */
import type { ClientInfo, ClientTest, PathMapping } from '../../../api/client'
import { changeLanguage } from '../../../i18n'
import { Page, Server, plain } from '../../../test/harness'
import { ClientsPart } from './ClientsPart'

const server = new Server()
const page = new Page()

const SECRET = 's-' + 'y'.repeat(30)

function client(id: number, over: Partial<ClientInfo> = {}): ClientInfo {
  return { id, name: 'SABnzbd', kind: 'sabnzbd', protocol: 'usenet', url: 'http://sabnzbd.example.com:8080', username: '', secret_set: true, category: 'nexbooks', has_categories: true, priority: 1, enabled: true, version: '4.3.2', state: 'ready', last_error: '', checked_at: null, ...over }
}
const outcome = (over: Partial<ClientTest> = {}): ClientTest => ({ version: '4.3.2', category_existed: false, has_categories: true, category: 'nexbooks', folder: '/downloads/complete/nexbooks', ...over })
const mapping = (id: number): PathMapping => ({ id, client_id: 1, client: 'SABnzbd', remote: '/downloads/complete', local: '/data/usenet/complete' })

const LIST = 'GET /api/v1/procure/clients'
const MAPS = 'GET /api/v1/procure/path-mappings'

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { [LIST]: { clients: [client(1)] }, [MAPS]: { mappings: [] } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const text = (testid: string) => plain(document.querySelector(`[data-testid="${testid}"]`)!.textContent)
const field = (dialog: HTMLElement, label: string) => [...dialog.querySelectorAll('label')].find((item) => plain(item.querySelector('span')?.textContent) === label)?.querySelector('input') as HTMLInputElement

describe('the cards', () => {
  it('say the state, the version and the category', async () => {
    await page.show(<ClientsPart />)
    expect(text('client-state-1')).toBe('bereit')
    expect(text('client-line-1')).toBe('Version 4.3.2 · Kategorie „nexbooks“ angelegt')
    expect(plain(document.querySelector('[data-testid="client-1"]')!.textContent)).toContain('Usenet')
    expect(page.text).toContain('Es nutzt dafür die Kategorie „nexbooks“, damit es nur seine eigenen Aufträge sieht.')
  })

  it('say that a program cannot be reached, and that its downloads stay', async () => {
    server.answers[LIST] = { clients: [client(1, { name: 'NZBGet', kind: 'nzbget', state: 'unreachable', version: '', last_error: 'client_unreachable' })] }
    await page.show(<ClientsPart />)
    expect(text('client-state-1')).toBe('nicht erreichbar')
    expect(text('client-note-1')).toBe('nexbooks erreicht NZBGet nicht. Seine Downloads bleiben, wie sie sind. Prüfe Adresse und Anmeldung.')
  })

  it('say that the login was refused', async () => {
    server.answers[LIST] = { clients: [client(1, { state: 'login', last_error: 'client_auth_failed' })] }
    await page.show(<ClientsPart />)
    expect(text('client-state-1')).toBe('Anmeldung abgelehnt')
  })

  it('test a saved program and say what it found', async () => {
    server.answers['POST /api/v1/procure/clients/1/test'] = outcome()
    await page.show(<ClientsPart />)
    await page.click(page.button('Testen', document.querySelector('[data-testid="client-1"]')!))
    expect(page.text).toContain('SABnzbd antwortet: Version 4.3.2, die Kategorie „nexbooks“ ist da.')
  })

  it('never show a key or password', async () => {
    await page.show(<ClientsPart />)
    expect(page.text).not.toContain(SECRET)
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })

  it('remove a program only after the question, and say what stays', async () => {
    server.answers['DELETE /api/v1/procure/clients/1'] = 204
    await page.show(<ClientsPart />)
    await page.click(page.button('Programm entfernen'))
    expect(plain(page.dialog!.textContent)).toContain('Laufende Downloads im Programm bleiben dort, nexbooks fasst sie nicht mehr an.')
    expect(server.called('DELETE', '/api/v1/procure/clients/1')).toBeUndefined()
    await page.inDialog('Abbrechen')
    expect(server.called('DELETE', '/api/v1/procure/clients/1')).toBeUndefined()
    await page.click(page.button('Programm entfernen'))
    await page.inDialog('Programm entfernen')
    expect(server.called('DELETE', '/api/v1/procure/clients/1')).toBeTruthy()
  })
})

describe('the dialog to add a program', () => {
  async function open(): Promise<HTMLElement> {
    server.answers[LIST] = { clients: [] }
    await page.show(<ClientsPart />)
    await page.click(document.querySelector('[data-testid="client-add"]') as HTMLElement)
    return page.dialog!
  }

  it('offers the five programs, each with its protocol, and SABnzbd first', async () => {
    const dialog = await open()
    const options = [...dialog.querySelectorAll('select option')].map((item) => plain(item.textContent))
    expect(options).toEqual(['SABnzbd (Usenet)', 'NZBGet (Usenet)', 'qBittorrent (Torrent)', 'Transmission (Torrent)', 'Deluge (Torrent)'])
    expect(field(dialog, 'Name').value).toBe('SABnzbd')
    expect(field(dialog, 'Kategorie').value).toBe('nexbooks')
  })

  it('asks for the credentials that fit the program: a key, a user and a password, or a password', async () => {
    const dialog = await open()
    expect(plain(dialog.textContent)).toContain('API-Schlüssel')
    expect(plain(dialog.textContent)).not.toContain('Benutzer')
    await page.choose(dialog.querySelector('select'), 'qbittorrent')
    expect(field(dialog, 'Name').value).toBe('qBittorrent')
    expect(plain(dialog.textContent)).toContain('Benutzer')
    expect(plain(dialog.textContent)).toContain('Passwort')
    await page.choose(dialog.querySelector('select'), 'deluge')
    expect(plain(dialog.textContent)).not.toContain('Benutzer')
    expect(plain(dialog.textContent)).toContain('Passwort')
  })

  it('asks for the test before it saves', async () => {
    const dialog = await open()
    await page.inDialog('Programm anlegen')
    expect(text('client-local-problem')).toBe('Name und Adresse fehlen.')
    await page.type(field(dialog, 'Adresse'), 'http://sabnzbd.example.com:8080')
    await page.inDialog('Programm anlegen')
    expect(text('client-local-problem')).toBe('Teste zuerst die Verbindung.')
    expect(server.called('POST', '/api/v1/procure/clients')).toBeUndefined()
  })

  it('shows the four steps of a test that went through and saves with the key', async () => {
    server.answers['POST /api/v1/procure/clients/test'] = outcome()
    server.answers['POST /api/v1/procure/clients'] = client(2)
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'http://sabnzbd.example.com:8080')
    await page.type(field(dialog, 'API-Schlüssel'), SECRET)
    await page.click(page.button('Testen', dialog))
    const steps = [...document.querySelectorAll('[data-testid="client-checks"] li')].map((item) => plain(item.textContent))
    expect(steps).toEqual(['Erreichbarsabnzbd.example.com:8080', 'Version 4.3.2wird unterstützt', 'Anmeldung angenommen', 'Kategorie „nexbooks“ angelegtund zurückgelesen'])
    expect(server.called('POST', '/api/v1/procure/clients/test')!.body).toMatchObject({ kind: 'sabnzbd', url: 'http://sabnzbd.example.com:8080', secret: SECRET, category: 'nexbooks' })
    await page.inDialog('Programm anlegen')
    expect(server.called('POST', '/api/v1/procure/clients')!.body).toEqual({ kind: 'sabnzbd', name: 'SABnzbd', url: 'http://sabnzbd.example.com:8080', username: '', category: 'nexbooks', secret: SECRET })
    expect(page.dialog).toBeNull()
    expect(page.text).not.toContain(SECRET)
  })

  it.each([
    ['client_auth_failed', 'Das Programm lehnt die Anmeldung ab.'],
    ['client_wrong_kind', 'An dieser Adresse antwortet etwas, aber nicht das gewählte Programm.'],
    ['client_unreachable', 'Das Programm ist nicht erreichbar. Stimmen Adresse und Port?'],
    ['client_category_unusable', 'Das Programm behandelt diese Kategorie so, dass nexbooks ihr nicht folgen kann.'],
  ])('says in words what went wrong: %s', async (code, sentence) => {
    server.fails['POST /api/v1/procure/clients/test'] = { status: 502, code }
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'http://sabnzbd.example.com:8080')
    await page.click(page.button('Testen', dialog))
    expect(text('client-checks')).toContain(sentence)
  })

  it('needs a new test once the address, the key or the category changes', async () => {
    server.answers['POST /api/v1/procure/clients/test'] = outcome()
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'http://sabnzbd.example.com:8080')
    await page.click(page.button('Testen', dialog))
    await page.type(field(dialog, 'Kategorie'), 'bücher')
    expect(document.querySelectorAll('[data-testid="client-checks"] li').length).toBe(0)
    await page.inDialog('Programm anlegen')
    expect(text('client-local-problem')).toBe('Teste zuerst die Verbindung.')
  })
})

describe('programs that make no category', () => {
  it('say on the card what is true for each: NZBGet found it, Transmission has none', async () => {
    server.answers[LIST] = { clients: [client(1, { name: 'NZBGet', kind: 'nzbget' }), client(2, { name: 'Transmission', kind: 'transmission', has_categories: false })] }
    await page.show(<ClientsPart />)
    expect(text('client-line-1')).toBe('Version 4.3.2 · Kategorie „nexbooks“ in NZBGet gefunden')
    expect(text('client-line-2')).toBe('Version 4.3.2 · keine Kategorien, Ordner „nexbooks“')
    expect(page.text).not.toContain('angelegt')
  })

  it('test of Transmission says it has no categories and does not claim one was made', async () => {
    server.answers['POST /api/v1/procure/clients/test'] = outcome({ has_categories: false })
    await page.show(<ClientsPart />)
    await page.click(page.button('Programm hinzufügen') ?? (document.querySelector('[data-testid="client-add"]') as HTMLElement))
    const dialog = page.dialog!
    await page.choose(dialog.querySelector('select') as HTMLSelectElement, 'transmission')
    expect(plain(dialog.textContent)).toContain('Ordner')
    await page.type(field(dialog, 'Adresse'), 'http://transmission.example.com:9091')
    await page.click(page.button('Testen', dialog))
    const steps = [...document.querySelectorAll('[data-testid="client-checks"] li')].map((item) => plain(item.textContent))
    expect(steps.at(-1)).toBe('Transmission kennt keine Kategoriendie Aufträge gehen in den Ordner „nexbooks“ im Download-Ordner')
    expect(steps.join(' ')).not.toContain('angelegt')
  })

  it('test of a category that was there says so instead of "made"', async () => {
    server.answers['POST /api/v1/procure/clients/test'] = outcome({ category_existed: true })
    await page.show(<ClientsPart />)
    await page.click(document.querySelector('[data-testid="client-add"]') as HTMLElement)
    const dialog = page.dialog!
    await page.type(field(dialog, 'Adresse'), 'http://sabnzbd.example.com:8080')
    await page.click(page.button('Testen', dialog))
    expect([...document.querySelectorAll('[data-testid="client-checks"] li')].map((item) => plain(item.textContent)).at(-1)).toBe('Kategorie „nexbooks“ vorhandenund zurückgelesen')
  })

  it('says to make the category in NZBGet when the test finds none', async () => {
    server.fails['POST /api/v1/procure/clients/test'] = { status: 502, code: 'client_category_missing', values: { category: 'nexbooks' } }
    await page.show(<ClientsPart />)
    await page.click(document.querySelector('[data-testid="client-add"]') as HTMLElement)
    const dialog = page.dialog!
    await page.type(field(dialog, 'Adresse'), 'http://nzbget.example.com:6789')
    await page.click(page.button('Testen', dialog))
    expect(text('client-checks')).toContain('NZBGet kennt die Kategorie „nexbooks“ nicht. Lege sie in NZBGet an')
  })
})

describe('an address that points elsewhere', () => {
  it('asks for the key or password again and sends nothing', async () => {
    server.answers['PATCH /api/v1/procure/clients/1'] = client(1)
    server.answers['POST /api/v1/procure/clients/test'] = outcome()
    await page.show(<ClientsPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="client-1"]')!))
    const dialog = page.dialog!
    await page.type(field(dialog, 'Adresse'), 'http://other.example.com:8080')
    expect(plain(dialog.textContent)).toContain('nexbooks schickt den gespeicherten Schlüssel nie an eine andere Stelle')
    await page.click(page.button('Testen', dialog))
    await page.inDialog('Speichern')
    expect(server.called('POST', '/api/v1/procure/clients/test')).toBeUndefined()
    expect(server.called('PATCH', '/api/v1/procure/clients/1')).toBeUndefined()
    await page.type(field(dialog, 'API-Schlüssel'), SECRET)
    await page.click(page.button('Testen', dialog))
    expect(server.called('POST', '/api/v1/procure/clients/test')!.body).toMatchObject({ secret: SECRET })
  })
})

describe('the dialog to change a program', () => {
  it('is saved as it stands when nothing about the connection changed, and the key stays', async () => {
    server.answers['PATCH /api/v1/procure/clients/1'] = client(1)
    await page.show(<ClientsPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="client-1"]')!))
    expect(page.dialog!.querySelector<HTMLInputElement>('input[type="password"]')!.placeholder).toBe('gespeichert, leer lassen zum Behalten')
    await page.type(field(page.dialog!, 'Name'), 'Usenet')
    await page.inDialog('Speichern')
    const sent = server.called('PATCH', '/api/v1/procure/clients/1')!.body as Record<string, unknown>
    expect(sent).not.toHaveProperty('secret')
    expect(sent).toMatchObject({ name: 'Usenet', kind: 'sabnzbd', url: 'http://sabnzbd.example.com:8080' })
  })
})

describe('the path mappings', () => {
  it('say there are none and where the suggestion will be', async () => {
    await page.show(<ClientsPart />)
    expect(page.text).toContain('Noch keine Zuordnung. Wenn nexbooks eine Datei nicht findet, steht der Vorschlag unter „Braucht dich“.')
  })

  it('list what the operator confirmed, and remove one only after the question', async () => {
    server.answers[MAPS] = { mappings: [mapping(1)] }
    server.answers['DELETE /api/v1/procure/path-mappings/1'] = 204
    await page.show(<ClientsPart />)
    expect(plain(document.querySelector('[data-testid="mapping-1"]')!.textContent)).toContain('/downloads/complete')
    expect(plain(document.querySelector('[data-testid="mapping-1"]')!.textContent)).toContain('/data/usenet/complete')
    await page.click(page.button('Zuordnung entfernen'))
    expect(server.called('DELETE', '/api/v1/procure/path-mappings/1')).toBeUndefined()
    await page.inDialog('Zuordnung entfernen')
    expect(server.called('DELETE', '/api/v1/procure/path-mappings/1')).toBeTruthy()
  })
})
