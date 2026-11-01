/**
 * Settings, Libraries: the cards of the folders, the scan and its progress, who sees what, the list of problems, making
 * and changing a library. Everything that removes asks first and says that the files stay where they are.
 */
import type { LibraryAdmin, Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server, plain } from '../../test/harness'
import { LibrariesPart } from './LibraryCards'
import { useServerSettings } from './ServerCards'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', sign_in: 'password' } as unknown as Me
vi.mock('../../state/auth', () => ({ useAuth: () => ({ me, status: 'signedIn' }) }))

const server = new Server()
const page = new Page()

function Part() {
  return <LibrariesPart server={useServerSettings()} />
}

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()

function library(over: Partial<LibraryAdmin> = {}): LibraryAdmin {
  return {
    id: 1, name: 'Familie', kind: 'both', my_right: 'manage', state: 'idle', path: '/books/familie', scan_enabled: true,
    scanned_at: minutesAgo(5), progress: { done: 0, total: 0 }, matching: 'idle', resting: [],
    report: { seen: 13, added: 4, changed: 1, missing: 2, problems: 0, deferred: 0, links: 0, seconds: 3, aborted: null },
    counts: { ebooks: 9, audiobooks: 1, files: 13, bytes: 30_000_000, problems: 0, review: 0, entries: 0, pending: 0 },
    members: [{ user_id: 1, name: 'jule', display_name: 'Jule', right: 'manage' }, { user_id: 2, name: 'rita', display_name: '', right: 'read' }],
    ...over,
  }
}

const settings = { public_url: '', password_login: true, two_factor_required: true, backup_schedule: 'daily', backup_keep: 7, smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '', api_keys_allowed: false, ereaders_allowed: true, update_check: true, scan_minutes: 60 }

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = {
    'GET /api/v1/settings': settings,
    'GET /api/v1/libraries/admin': [library()],
    'GET /api/v1/libraries/roots': { roots: [{ root: '/books', folders: ['/books/familie', '/books/hoerbuecher'] }] },
    'GET /api/v1/accounts': [
      { id: 1, name: 'jule', display_name: 'Jule', role: 'operator' },
      { id: 2, name: 'rita', display_name: '', role: 'member' },
      { id: 3, name: 'sam', display_name: 'Sam', role: 'member' },
      { id: 4, name: 'gesperrt', display_name: '', role: 'member', blocked: true },
    ],
  }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the card of a library', () => {
  it('shows the name, the folder, what it holds, the last run and who is in it', async () => {
    await page.show(<Part />)
    const card = document.getElementById('library-1')!
    expect(card.querySelector('h2')!.textContent).toBe('Familie')
    expect(card.textContent).toContain('/books/familie')
    expect(plain(document.querySelector('[data-testid="library-counts-1"]')!.textContent)).toBe('E-Books und Hörbücher · 9 E-Books · 1 Hörbuch · 13 Dateien · 30 MB')
    expect(plain(document.querySelector('[data-testid="library-last-1"]')!.textContent)).toBe('Zuletzt durchgesehen: vor 5 Minuten')
    expect(plain(document.querySelector('[data-testid="library-report-1"]')!.textContent)).toBe('Letzter Lauf: 4 neu, 1 geändert, 2 verschwunden.')
    const members = plain(document.querySelector('[data-testid="library-members-1"]')!.textContent)
    expect(members).toContain('Jule · Verwalten')
    expect(members).toContain('rita · Lesen')
  })

  it('says that a library was never read, and counts one of each in the singular', async () => {
    server.answers['GET /api/v1/libraries/admin'] = [library({ scanned_at: null, report: null, counts: { ebooks: 1, audiobooks: 1, files: 1, bytes: 0, problems: 0, review: 0, entries: 0, pending: 0 }, members: [] })]
    await page.show(<Part />)
    expect(plain(document.querySelector('[data-testid="library-last-1"]')!.textContent)).toBe('Noch nicht eingelesen')
    expect(plain(document.querySelector('[data-testid="library-counts-1"]')!.textContent)).toContain('1 E-Book · 1 Hörbuch · 1 Datei')
    expect(plain(document.querySelector('[data-testid="library-members-1"]')!.textContent)).toContain('Niemand hat Zugriff.')
  })

  it('shows the progress of a scan that runs, and no new scan can be started', async () => {
    server.answers['GET /api/v1/libraries/admin'] = [library({ state: 'running', progress: { done: 12, total: 40 } })]
    await page.show(<Part />)
    const state = document.querySelector('[data-testid="library-state-1"]')!
    expect(plain(state.textContent)).toBe('Wird eingelesen · 12 von 40')
    const bar = state.querySelector('[role="progressbar"]')!
    expect([bar.getAttribute('aria-valuenow'), bar.getAttribute('aria-valuemax')]).toEqual(['12', '40'])
    expect(page.button('Jetzt einlesen')!.disabled).toBe(true)
  })

  it('says that a scan waits for its turn', async () => {
    server.answers['GET /api/v1/libraries/admin'] = [library({ state: 'queued' })]
    await page.show(<Part />)
    expect(plain(document.querySelector('[data-testid="library-state-1"]')!.textContent)).toBe('Wartet auf den Start')
    expect(document.querySelector('[role="progressbar"]')).toBeNull()
  })

  it('looks again while a scan runs and shows the result when it is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    server.answers['GET /api/v1/libraries/admin'] = [library({ state: 'running', progress: { done: 1, total: 4 } })]
    await page.show(<Part />)
    server.answers['GET /api/v1/libraries/admin'] = [library({ state: 'idle', scanned_at: minutesAgo(0) })]
    await vi.advanceTimersByTimeAsync(2100)
    const { idle } = await import('../../test/wait')
    await idle()
    expect(plain(document.querySelector('[data-testid="library-last-1"]')!.textContent)).toBe('Zuletzt durchgesehen: gerade eben')
    expect(document.querySelector('[role="progressbar"]')).toBeNull()
  })

  it('warns when the folder was gone, empty or the run failed, and says nothing was marked missing', async () => {
    for (const [aborted, text] of [
      ['offline', 'Der Ordner ist nicht erreichbar. Nichts wurde als verschwunden markiert.'],
      ['empty', 'Im Ordner liegt kein Buch, nexbooks kennt aber Bücher darin. Nichts wurde als verschwunden markiert. Ist die Freigabe eingebunden?'],
      ['truncated', 'Der Ordner enthält mehr Einträge, als nexbooks in einem Lauf ansieht. Was gesehen wurde, ist gelesen, nichts wurde als verschwunden markiert.'],
      ['error', 'Der letzte Lauf ist fehlgeschlagen. Das Log sagt mehr.'],
    ] as const) {
      const base = library().report!
      server.answers['GET /api/v1/libraries/admin'] = [library({ report: { ...base, aborted } })]
      await page.show(<Part />)
      expect(plain(document.querySelector('[data-testid="library-aborted-1"]')!.textContent)).toBe(text)
      page.close()
    }
  })

  it('mentions files that were still being written and links that were not followed', async () => {
    const base = library().report!
    server.answers['GET /api/v1/libraries/admin'] = [library({ report: { ...base, deferred: 2, links: 1 } })]
    await page.show(<Part />)
    const text = plain(document.querySelector('[data-testid="library-report-1"]')!.textContent)
    expect(text).toContain('2 Dateien wurden gerade noch geschrieben')
    expect(text).toContain('1 Verknüpfung wurde nicht verfolgt.')
  })
})

describe('starting a scan', () => {
  it('sends the one request and looks at the library again', async () => {
    server.answers['POST /api/v1/libraries/1/scan'] = { state: 'queued' }
    await page.show(<Part />)
    await page.click(page.button('Jetzt einlesen'))
    expect(server.changes()).toEqual(['POST /api/v1/libraries/1/scan'])
    expect(server.calls.filter((call) => call.url === '/api/v1/libraries/admin').length).toBe(2)
  })
})

describe('reading everything again', () => {
  it('asks first, says what stays, and sends the one request with everything only after the button of the dialog', async () => {
    server.answers['POST /api/v1/libraries/1/scan'] = { state: 'queued' }
    await page.show(<Part />)
    await page.click(page.button('Alles neu einlesen'))
    expect(page.dialog).toBeTruthy()
    const text = plain(page.dialog!.textContent)
    expect(text).toContain('Familie ganz neu einlesen?')
    expect(text).toContain('Was Personen von Hand festgelegt haben')
    expect(text).toContain('bleibt, ebenso Lesefortschritt und Lesezeichen')
    expect(text).toContain('Die Dateien selbst werden nicht angefasst.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Alles neu einlesen'))
    await page.inDialog('Alles neu einlesen')
    expect(server.changes()).toEqual(['POST /api/v1/libraries/1/scan'])
    expect(server.called('POST', '/api/v1/libraries/1/scan')!.body).toEqual({ everything: true })
  })

  it('cannot be started while a scan runs, and the plain scan sends no body', async () => {
    server.answers['GET /api/v1/libraries/admin'] = [library({ state: 'running', progress: { done: 3, total: 10 } })]
    server.answers['POST /api/v1/libraries/1/scan'] = { state: 'queued' }
    await page.show(<Part />)
    expect((page.button('Alles neu einlesen') as HTMLButtonElement).disabled).toBe(true)
    server.answers['GET /api/v1/libraries/admin'] = [library()]
    page.close()
    await page.show(<Part />)
    await page.click(page.button('Jetzt einlesen'))
    expect(server.called('POST', '/api/v1/libraries/1/scan')!.body).toBeUndefined()
  })
})

describe('the interval', () => {
  it('is chosen for the whole server and saved at once', async () => {
    server.answers['PUT /api/v1/settings'] = { ...settings, scan_minutes: 360 }
    await page.show(<Part />)
    const select = document.querySelector<HTMLSelectElement>('#libraries-scan select')!
    expect(select.value).toBe('60')
    expect([...select.options].map((option) => option.textContent)).toEqual(['Nie von selbst', 'Alle 15 Minuten', 'Jede Stunde', 'Alle 6 Stunden', 'Einmal am Tag'])
    await page.choose(select, '360')
    expect(server.called('PUT', '/api/v1/settings')!.body).toEqual({ scan_minutes: 360 })
  })
})

describe('removing a library', () => {
  it('asks first, says that the files stay where they are, and sends nothing before the red button', async () => {
    server.answers['DELETE /api/v1/libraries/1'] = 204
    await page.show(<Part />)
    await page.click(page.button('Entfernen'))
    expect(page.dialog).toBeTruthy()
    expect(plain(page.dialog!.textContent)).toContain('Die Dateien bleiben liegen, nichts wird gelöscht, und die Merklisten bleiben, wie sie sind.')
    // The reading goes with it, and cannot be brought back.
    expect(plain(page.dialog!.textContent)).toContain('Lesefortschritt, die Lesezeichen und den Lesestapel aller, die darin lesen')
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich nicht zurückholen.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Entfernen'))
    await page.inDialog('Bibliothek entfernen')
    expect(server.changes()).toEqual(['DELETE /api/v1/libraries/1'])
  })

  it('sends it once however often the red button is pressed', async () => {
    server.answers['DELETE /api/v1/libraries/1'] = 204
    await page.show(<Part />)
    await page.click(page.button('Entfernen'))
    const red = page.button('Bibliothek entfernen', page.dialog!)!
    await page.click(red)
    await page.click(red)
    expect(server.changes()).toEqual(['DELETE /api/v1/libraries/1'])
  })
})

describe('making a library', () => {
  it('offers the folders below the root, sends name, folder, kind and the switch, and shows a refusal at the form', async () => {
    server.answers['POST /api/v1/libraries'] = library({ id: 2, name: 'Hörbücher' })
    await page.show(<Part />)
    await page.click(page.button('Bibliothek anlegen'))
    const dialog = page.dialog!
    expect(plain(dialog.textContent)).toContain('unterhalb von /books')
    // The folders below the root are offered as buttons; one fills the field.
    await page.click(page.button('/books/hoerbuecher', dialog))
    expect((dialog.querySelectorAll('input')[1] as HTMLInputElement).value).toBe('/books/hoerbuecher')
    await page.type(dialog.querySelectorAll('input')[0] as HTMLInputElement, 'Hörbücher')
    await page.click(page.button('Nur Hörbücher', dialog))
    server.fails['POST /api/v1/libraries'] = { status: 422, code: 'path_overlap' }
    await page.click(page.button('Anlegen', dialog))
    expect(plain(dialog.textContent)).toContain('Der Ordner überschneidet sich mit einer anderen Bibliothek.')
    delete server.fails['POST /api/v1/libraries']
    await page.click(page.button('Anlegen', dialog))
    expect(server.calls.filter((call) => call.method === 'POST').at(-1)!.body).toEqual({ name: 'Hörbücher', path: '/books/hoerbuecher', kind: 'audio', scan_enabled: true })
    expect(page.dialog).toBeNull()
  })

  it('names the folder below which libraries may lie once, in German and in English', async () => {
    for (const [language, open, below] of [['de', 'Bibliothek anlegen', 'unterhalb von /books'], ['en', 'Add a library', 'below /books']] as const) {
      await changeLanguage(language, false)
      await page.show(<Part />)
      await page.click(page.button(open))
      const text = plain(page.dialog!.textContent)
      // The sentence above the form names it; the hint under the field only says how to write a path.
      expect(text.split(below).length - 1, language).toBe(1)
      expect(text, language).not.toMatch(/(Erlaubt ist alles|Everything below)/)
      page.close()
    }
  })

  it('says why a folder is refused in words', async () => {
    await page.show(<Part />)
    await page.click(page.button('Bibliothek anlegen'))
    const dialog = page.dialog!
    for (const [code, text] of [
      ['path_outside_roots', 'Der Ordner liegt nicht unterhalb eines erlaubten Ordners.'],
      ['path_is_data', 'Der Datenordner von nexbooks kann keine Bibliothek sein'],
      ['path_missing', 'Diesen Ordner gibt es nicht'],
      ['name_taken', 'Dieser Name ist vergeben.'],
    ]) {
      server.fails['POST /api/v1/libraries'] = { status: 422, code }
      await page.type(dialog.querySelectorAll('input')[0] as HTMLInputElement, 'X')
      await page.type(dialog.querySelectorAll('input')[1] as HTMLInputElement, '/elsewhere')
      await page.click(page.button('Anlegen', dialog))
      expect(plain(dialog.textContent)).toContain(text)
    }
  })

  it('tells a refusal about the folder at the field of the folder, and any other below the form', async () => {
    await page.show(<Part />)
    await page.click(page.button('Bibliothek anlegen'))
    const dialog = page.dialog!
    const field = dialog.querySelectorAll('input')[1] as HTMLInputElement
    await page.type(dialog.querySelectorAll('input')[0] as HTMLInputElement, 'X')
    await page.type(field, '/elsewhere')
    server.fails['POST /api/v1/libraries'] = { status: 422, code: 'path_missing' }
    await page.click(page.button('Anlegen', dialog))
    const block = field.closest('div.text-sm')!.parentElement!
    expect(plain(block.querySelector('[role="alert"]')!.textContent)).toContain('Diesen Ordner gibt es nicht')
    expect(dialog.querySelectorAll('[role="alert"]').length).toBe(1)
    server.fails['POST /api/v1/libraries'] = { status: 409, code: 'name_taken' }
    await page.click(page.button('Anlegen', dialog))
    expect(block.querySelector('[role="alert"]')).toBeNull()
    expect(plain(dialog.querySelector('[role="alert"]')!.textContent)).toContain('Dieser Name ist vergeben.')
  })

  it('cannot make one where the server allows no folder', async () => {
    server.answers['GET /api/v1/libraries/roots'] = { roots: [] }
    server.answers['GET /api/v1/libraries/admin'] = []
    await page.show(<Part />)
    expect(plain(document.querySelector('[data-testid="libraries-empty"]')!.textContent)).toContain('NEXBOOKS_LIBRARY_ROOTS ist leer')
    expect(page.button('Bibliothek anlegen')!.disabled).toBe(true)
  })

  it('is told "none yet" when there is a root and no library', async () => {
    server.answers['GET /api/v1/libraries/admin'] = []
    await page.show(<Part />)
    expect(plain(document.querySelector('[data-testid="libraries-empty"]')!.textContent)).toBe('Noch keine Bibliothek. Lege unten einen Ordner an.')
  })
})

describe('changing a library', () => {
  it('renames it and changes what it holds, but not its folder', async () => {
    server.answers['PATCH /api/v1/libraries/1'] = library({ name: 'Neu' })
    await page.show(<Part />)
    await page.click(page.button('Bearbeiten'))
    const dialog = page.dialog!
    expect(dialog.querySelectorAll('input[type="text"], input:not([type])').length).toBe(1)
    await page.type(dialog.querySelector('input') as HTMLInputElement, 'Neu')
    await page.click(page.button('Nur E-Books', dialog))
    await page.click(page.button('Speichern', dialog))
    expect(server.called('PATCH', '/api/v1/libraries/1')!.body).toEqual({ name: 'Neu', kind: 'ebook', scan_enabled: true })
  })
})

describe('who sees the library', () => {
  it('lists the accounts that are not blocked, with a choice of no access, read or manage, and saves them all at once', async () => {
    server.answers['PUT /api/v1/libraries/1/members'] = library()
    await page.show(<Part />)
    await page.click(page.button('Zugriff ändern'))
    const dialog = page.dialog!
    expect([...dialog.querySelectorAll('select')].map((select) => plain(select.getAttribute('aria-label')))).toEqual(['Jule', 'rita', 'Sam'])
    const selects = [...dialog.querySelectorAll<HTMLSelectElement>('select')]
    expect(selects.map((select) => select.value)).toEqual(['manage', 'read', 'none'])
    await page.choose(selects[1], 'none')
    await page.choose(selects[2], 'read')
    await page.click(page.button('Speichern', dialog))
    expect(server.called('PUT', '/api/v1/libraries/1/members')!.body).toEqual({ members: [{ user_id: 1, right: 'manage' }, { user_id: 3, right: 'read' }] })
  })
})

describe('the list of problems', () => {
  it('is behind a button that says how many files, and gives each file with the reason in words', async () => {
    server.answers['GET /api/v1/libraries/admin'] = [library({ counts: { ebooks: 9, audiobooks: 1, files: 13, bytes: 0, problems: 2, review: 0, entries: 0, pending: 0 } })]
    server.answers['GET /api/v1/libraries/1/problems?limit=50&offset=0'] = {
      total: 2,
      items: [
        { path: 'Autor/kaputt.epub', reason: 'unreadable', message: 'x', seen_at: minutesAgo(1) },
        { path: 'Autor/Bombe.epub', reason: 'zip_bomb', message: 'x', seen_at: minutesAgo(1) },
        { path: 'bad?name', reason: 'bad_name', message: 'x', seen_at: minutesAgo(1) },
        { path: 'Autor/Neu.epub', reason: 'failed', message: 'x', seen_at: minutesAgo(1) },
      ],
    }
    await page.show(<Part />)
    expect(server.calls.some((call) => call.url.includes('/problems'))).toBe(false)
    await page.click(page.button('2 Dateien konnten nicht gelesen werden. Anzeigen'))
    const list = document.querySelector('[data-testid="library-problems-1"]')!
    expect(plain(list.textContent)).toContain('Autor/kaputt.epub')
    expect(plain(list.textContent)).toContain('Die Datei ist beschädigt oder keine, die ihr Name verspricht.')
    expect(plain(list.textContent)).toContain('Das E-Book entpackt sich zu viel größer, als es wiegt.')
    expect(plain(list.textContent)).toContain('Der Name ist kein gültiger Text und wurde ausgelassen.')
    expect(plain(list.textContent)).toContain('nexbooks konnte diese Datei nicht verarbeiten.')
    await page.click(page.button('Verbergen'))
    expect(document.querySelector('[data-testid="library-problems-1"]')).toBeNull()
  })

  it('is not offered when there is none', async () => {
    await page.show(<Part />)
    expect(document.body.textContent).not.toContain('konnten nicht gelesen werden')
  })
})

describe('the words', () => {
  it('come in English with the same sentences', async () => {
    await changeLanguage('en', false)
    await page.show(<Part />)
    expect(plain(document.querySelector('[data-testid="library-counts-1"]')!.textContent)).toBe('E-books and audiobooks · 9 e-books · 1 audiobook · 13 files · 30 MB')
    expect(plain(document.querySelector('[data-testid="library-last-1"]')!.textContent)).toBe('Last looked through: 5 minutes ago')
    await page.click(page.button('Remove'))
    expect(plain(page.dialog!.textContent)).toContain('The files stay where they are, nothing is deleted, and the wish lists stay as they are.')
  })

  it('never use a dash as a pause', async () => {
    await page.show(<Part />)
    await page.click(page.button('Zugriff ändern'))
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})
