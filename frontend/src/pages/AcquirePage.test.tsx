/**
 * The page "Beschaffen": the five lists under their tabs, the notes above them, what each row says and what
 * its buttons do (and ask first, where it must), and the way out for a person who may not ask for books.
 */
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { Page, Server, plain } from '../test/harness'
import { blockLine, counts, foreign, line, problem, running, wish } from '../test/procure'
import { idle } from '../test/wait'
import { AcquirePage } from './AcquirePage'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>
}

function app() {
  return (
    <ProcureProvider>
      <Routes>
        <Route path="acquire" element={<AcquirePage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </ProcureProvider>
  )
}

function serve(answers: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/procure/counts': counts({ seeking: 2, running: 1, needs: 1 }),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/procure/problems': { problems: [], foreign: [] },
    'GET /api/v1/procure/history': { entries: [] },
    'GET /api/v1/procure/blocklist': { entries: [] },
    ...answers,
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

/** The cards of "Gesucht" (the list is the first look; the rows of the cards are what these tests read). */
const cards = () => window.localStorage.setItem('nexbooks.acquire.wanted', 'cards')

const text = () => plain(document.querySelector('[data-testid="acquire"]')?.textContent)

describe('the tabs and the notes', () => {
  it('shows the five tabs with their numbers, the state of the automatic work, and Gesucht first', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1), wish(2)] }, 'GET /api/v1/procure/running': { downloads: [running(3)] }, 'GET /api/v1/procure/problems': { problems: [problem(4)], foreign: [foreign(5)] }, 'GET /api/v1/procure/counts': counts({ blocklist: 3 }) })
    await page.show(app(), '/acquire')
    const tabs = [...document.querySelectorAll('.bes-tabs a')].map((item) => plain(item.textContent))
    expect(tabs).toEqual(['Gesucht2', 'Läuft1', 'Braucht dich2', 'Verlauf', 'Sperrliste3'])
    expect(document.querySelector('.bes-tabs a.on')!.textContent).toContain('Gesucht')
    expect(document.querySelector('[data-testid="autochip"]')!.textContent).toBe('Automatik an')
    // The first look is the list.
    expect(document.querySelector('[data-testid="wanted-list"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="wanted"]')).toBeNull()
  })

  it('goes to the tab the address names', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(3)] } })
    await page.show(app(), '/acquire?reiter=running')
    expect(document.querySelector('.bes-tabs a.on')!.textContent).toContain('Läuft')
    expect(document.querySelector('[data-testid="running"]')).not.toBeNull()
  })

  it('says when there is no indexer (the way there only for the operator), when the automatic work is off, and when a program does not answer', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ indexers: 0, automatic: false, clients_down: [{ id: 4, name: 'SABnzbd', code: 'client_unreachable' }] }) })
    await page.show(app(), '/acquire')
    expect(text()).toContain('Es ist noch kein Indexer eingerichtet.')
    expect(text()).toContain('SABnzbd ist nicht erreichbar.')
    expect(document.querySelector('[data-testid="autochip"]')!.textContent).toBe('Automatik aus')
    expect(document.querySelector('.note a.nlink')).toBeNull()
    page.close()
    auth.me = { id: 1, name: 'jule', role: 'operator', profile: {} } as unknown as Me
    serve({ 'GET /api/v1/procure/counts': counts({ indexers: 2, automatic: false, clients_down: [{ id: 4, name: 'SABnzbd', code: 'client_unreachable' }] }) })
    await page.show(app(), '/acquire')
    expect(text()).toContain('Die Automatik ist aus.')
    expect([...document.querySelectorAll('.note a.nlink')].map((link) => link.getAttribute('href'))).toEqual(['/settings?tab=procure&sub=automation', '/settings?tab=procure&sub=clients'])
  })

  it('leads a person who may not ask for books back to the library', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/')
    expect(server.calls.some((call) => call.url === '/api/v1/procure/wishes')).toBe(false)
  })
})

describe('Gesucht', () => {
  beforeEach(cards)

  it('says where each wish stands, in its words', async () => {
    const reasons = [{ code: 'other_language', count: 9 }, { code: 'bundle', count: 3 }]
    const later = new Date(Date.now() + 3 * 3_600_000 - 60_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'searching' }),
          wish(2, { state: 'not_found', next_try_at: later, detail: { total: 17, reasons } }),
          wish(3, { state: 'waiting', release_date: '2026-11-19' }),
          wish(4, { state: 'capped', detail: { indexer: 'Leseclub', until: null } }),
          wish(5, { state: 'upgrade', next_try_at: later, detail: { have: 'mp3_128', goal: 'flac' }, kind: 'audio' }),
          wish(6, { state: 'idle', detail: { reason: 'main_off' } }),
          wish(7, { state: 'idle', detail: { reason: 'main_off', passing: 2 } }),
          wish(8, { state: 'searching', waiting_for_a_place: true }),
          wish(9, { state: 'not_found', detail: { total: 0, reasons: [], load_error: 'no_client' }, next_try_at: later }),
          wish(10, { state: 'idle', detail: { reason: 'no_right' } }),
          wish(11, { state: 'not_found', detail: { total: 1, reasons: [], load_error: 'other_language_running' }, next_try_at: later }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    const row = (id: number) => plain(document.querySelector(`[data-wish="${id}"] .rq-state`)!.textContent)
    expect(row(1)).toBe('Sucht gerade' + 'bei 2 Indexern')
    expect(row(2)).toContain('Nichts gefunden')
    expect(row(2)).toContain('Nächster Versuch in 3 Std.')
    expect(row(2)).toContain('17 Treffer, keiner passte. Gründe')
    expect(plain(document.querySelector('[data-wish="2"] .why ul')!.textContent)).toBe('9 × andere Sprache3 × Sammelpaket')
    expect(row(3)).toContain('Erscheint am 19. November 2026.')
    expect(row(4)).toContain('Leseclub hat heute sein Limit erreicht.')
    expect(row(4)).toContain('Wartet aufs Tageslimit')
    expect(row(5)).toContain('Hat MP3 128, sucht bis FLAC.')
    expect(row(6)).toContain('Die Automatik ist aus. Suche von Hand.')
    expect(row(7)).toContain('2 passende Releases gefunden.')
    expect(row(8)).toContain('Wartet auf einen freien Platz für die Suche.')
    expect(row(9)).toContain('Ein Release passt, ließ sich aber nicht laden:')
    expect(row(10)).toContain('Ruht: Dem, der den Wunsch angelegt hat, fehlt das Recht zum Beschaffen in dieser Bibliothek.')
    expect(row(11)).toContain('Von diesem Buch läuft schon ein Release in einer anderen Sprache.')
    expect(document.querySelector('[data-wish="5"] .chip.upg')!.textContent).toBe('Upgrade')
    // The row names who wished it, the kind and language, the library.
    expect(plain(document.querySelector('[data-wish="1"]')!.textContent)).toContain('Gewünscht von Mira')
    expect(plain(document.querySelector('[data-wish="1"] .chips')!.textContent)).toContain('E-Book · Deutsch')
    expect(plain(document.querySelector('[data-wish="1"] .chips')!.textContent)).toContain('Bücher')
  })

  it('searches now at once, and not for a book that is not out or a search that runs', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'not_found' }), wish(2, { state: 'waiting', release_date: '2027-01-01' }), wish(3, { state: 'searching' })] }, 'POST /api/v1/procure/wishes/1/search': wish(1) })
    await page.show(app(), '/acquire')
    const search = (id: number) => document.querySelector<HTMLButtonElement>(`[data-wish="${id}"] [data-act="search"]`)!
    expect(search(2).disabled).toBe(true)
    expect(search(3).disabled).toBe(true)
    await page.click(search(1))
    expect(server.changes()).toEqual(['POST /api/v1/procure/wishes/1/search'])
  })

  it('says that a wish rests when its language is no longer allowed, and turns it to the languages allowed', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'idle', language: 'en', detail: { reason: 'language_off', language: 'en' } })] }, 'POST /api/v1/procure/wishes/1/language': wish(1, { state: 'searching', language: '' }) })
    await page.show(app(), '/acquire')
    expect(plain(document.querySelector('[data-wish="1"] .rq-state')!.textContent)).toContain('Ruht: Englisch ist keine erlaubte Sprache mehr.')
    // Neither "Jetzt suchen" nor the releases: the wish would look in a language nexbooks does not fetch.
    expect(document.querySelector('[data-wish="1"] [data-act="search"]')).toBeNull()
    expect(document.querySelector('[data-wish="1"] [data-act="releases"]')).toBeNull()
    await page.click(document.querySelector('[data-wish="1"] [data-act="relanguage"]'))
    expect(server.changes()).toEqual(['POST /api/v1/procure/wishes/1/language'])
    expect(server.called('POST', '/api/v1/procure/wishes/1/language')!.body).toEqual({ language: null })
  })

  it('takes a wish away only after the question, and says what stays', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] }, 'DELETE /api/v1/procure/wishes/1': 204 })
    await page.show(app(), '/acquire')
    await page.click(document.querySelector('[data-wish="1"] [data-act="remove"]'))
    expect(page.dialog!.textContent).toContain('Gefundene oder geladene Bücher bleiben, wie sie sind.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(document.querySelector('[data-wish="1"] [data-act="remove"]'))
    await page.inDialog('Wunsch entfernen')
    expect(server.changes()).toEqual(['DELETE /api/v1/procure/wishes/1'])
  })

  it('shows all wishes or only the own ones, and says so when there is nothing', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1), wish(2, { mine: false, by: 'Tom' })] } })
    await page.show(app(), '/acquire')
    expect(document.querySelectorAll('[data-wish]')).toHaveLength(2)
    await page.click(document.querySelector('[data-mine="1"]'))
    expect(document.querySelectorAll('[data-wish]')).toHaveLength(1)
    page.close()
    serve()
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="wanted-empty"]')!.textContent).toContain('Nichts gesucht.')
  })

  it('does not list a wish that already loads (it stands under Läuft)', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'loading' })] } })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-wish]')).toBeNull()
  })
})

describe('Läuft', () => {
  it('shows the progress, the time left, the program and the size', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1), running(2, { phase: 'queued', progress: 0, remaining_seconds: null }), running(3, { phase: 'unpacking' }), running(4, { phase: 'waiting', progress: 100 })] } })
    await page.show(app(), '/acquire?reiter=running')
    const one = document.querySelector('[data-download="1"]')!
    expect(plain(one.querySelector('.pgrow')!.textContent)).toBe('lädt42 %noch 3 Min')
    expect(one.querySelector('[role="progressbar"]')!.getAttribute('aria-valuenow')).toBe('42')
    expect(plain(one.querySelector('.meta')!.textContent)).toContain('SABnzbd')
    expect(plain(one.querySelector('.meta')!.textContent)).toContain('Bücherei')
    expect(plain(one.querySelector('.meta')!.textContent)).toContain('412 MB')
    expect(plain(document.querySelector('[data-download="2"] .pgrow')!.textContent)).toBe('wartet in der Schlangewartet')
    expect(document.querySelector('[data-download="3"] .pg')!.className).toContain('stripe')
    expect(document.querySelector('[data-download="3"] .stepnote')!.textContent).toBe('Archiv wird entpackt')
    expect(plain(document.querySelector('[data-download="4"] .pgrow')!.textContent)).toContain('wartet auf Einordnen')
    expect(document.querySelector('[data-download="4"] .stepnote')!.textContent).toBe('Fertig geladen. Ziel: Hörbücher.')
  })

  it('says "wird geprüft" while the files are read and "wird eingeordnet" while they are put in place, as the model does', async () => {
    serve({
      'GET /api/v1/procure/running': {
        downloads: [running(1, { phase: 'checking', kind: 'audio', progress: 100 }), running(2, { phase: 'checking', kind: 'ebook', progress: 100 }), running(3, { phase: 'filing', progress: 100 })],
      },
    })
    await page.show(app(), '/acquire?reiter=running')
    expect(plain(document.querySelector('[data-download="1"] .pgrow')!.textContent)).toContain('wird geprüft')
    expect(document.querySelector('[data-download="1"] .pg')!.className).toContain('stripe')
    expect(document.querySelector('[data-download="1"] .stepnote')!.textContent).toBe('Laufzeit und Sprache werden mit dem Katalog verglichen')
    expect(document.querySelector('[data-download="2"] .stepnote')!.textContent).toBe('Sprache, Titel und Autor werden aus der Datei gelesen')
    expect(plain(document.querySelector('[data-download="3"] .pgrow')!.textContent)).toContain('wird eingeordnet')
    expect(document.querySelector('[data-download="3"] .stepnote')!.textContent).toBe('Ziel: Hörbücher')
  })

  it('shows the seeding of a filed torrent and ends it only after the question', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1, { phase: 'seeding', protocol: 'torrent', client: 'qBittorrent', seed_ratio: 0.64, goal_ratio: 1 })] }, 'POST /api/v1/procure/downloads/1/stop-seeding': { id: 1, state: 'imported' } })
    await page.show(app(), '/acquire?reiter=running')
    const row = document.querySelector('[data-download="1"]')!
    expect(plain(row.querySelector('.pgrow')!.textContent)).toBe('seedet bis ZielVerhältnis 0,64 von 1,00')
    expect(row.querySelector('.pg')!.className).toContain('seed')
    await page.click(row.querySelector('[data-act="stopseed"]'))
    expect(page.dialog!.textContent).toContain('Die Datei im Regal bleibt.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Seeden beenden')
    expect(server.changes()).toEqual(['POST /api/v1/procure/downloads/1/stop-seeding'])
  })

  it('asks three ways before it cancels: not at all, only cancel, or block and look on', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1)] }, 'POST /api/v1/procure/downloads/1/cancel': { id: 1, state: 'removed' } })
    await page.show(app(), '/acquire?reiter=running')
    await page.click(document.querySelector('[data-download="1"] [data-act="cancel"]'))
    expect(page.dialog!.textContent).toContain('Soll das Release gesperrt und ein anderes gesucht werden?')
    await page.inDialog('Nichts ändern')
    expect(server.changes()).toEqual([])
    await page.click(document.querySelector('[data-download="1"] [data-act="cancel"]'))
    await page.inDialog('Nur löschen')
    expect(server.called('POST', '/api/v1/procure/downloads/1/cancel')!.body).toEqual({ block: false })
    await page.click(document.querySelector('[data-download="1"] [data-act="cancel"]'))
    await page.inDialog('Sperren und weitersuchen')
    expect(server.calls.filter((call) => call.method === 'POST').map((call) => call.body)).toEqual([{ block: false }, { block: true }])
  })

  it('says how fast a download loads, in MB per second, and only while it loads', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1, { speed: 9_800_000 }), running(2, { phase: 'paused', speed: 5_000_000 }), running(3, { phase: 'queued', speed: null })] } })
    await page.show(app(), '/acquire?reiter=running')
    expect(plain(document.querySelector('[data-download="1"] .meta')!.textContent)).toContain('9,8 MB/s')
    expect(plain(document.querySelector('[data-download="2"] .meta')!.textContent)).not.toContain('MB/s')
    expect(plain(document.querySelector('[data-download="3"] .meta')!.textContent)).not.toContain('MB/s')
  })

  it('tells before it cancels that the job goes with everything loaded of it and that this cannot be undone', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1), running(2, { phase: 'waiting', progress: 100 })] } })
    await page.show(app(), '/acquire?reiter=running')
    for (const id of ['1', '2']) {
      await page.click(document.querySelector(`[data-download="${id}"] [data-act="cancel"]`))
      const said = plain(page.dialog!.textContent)
      expect(said).toContain('mit allem, was das Download-Programm schon geladen hat')
      expect(said).toContain('Das lässt sich nicht zurückholen.')
      expect(said).not.toContain('Teildatei')
      await page.inDialog('Nichts ändern')
    }
    expect(server.changes()).toEqual([])
  })

  it('says what the stall is, without a problem', async () => {
    serve({ 'GET /api/v1/procure/running': { downloads: [running(1, { stalled: true })] } })
    await page.show(app(), '/acquire?reiter=running')
    expect(plain(document.querySelector('[data-download="1"] .meta')!.textContent)).toContain('kommt gerade nicht voran')
  })
})

describe('Braucht dich', () => {
  const path = (over = {}) => problem(1, { code: 'path_not_found', can_map: true, values: { search: 'proposed', proposal: { remote: '/downloads', local: '/data/usenet' } }, ...over })

  it('shows a folder it cannot find with the suggestion, and the operator confirms exactly that', async () => {
    auth.me = { id: 1, name: 'jule', role: 'operator', profile: {} } as unknown as Me
    serve({
      'GET /api/v1/procure/problems': { problems: [path()], foreign: [] },
      'GET /api/v1/procure/downloads/1/mapping': { reported: '/downloads/complete/x', search: 'proposed', proposal: { remote: '/downloads', local: '/data/usenet' }, matches: [], choices: [], roots: ['/data'] },
      'POST /api/v1/procure/downloads/1/mapping': { id: 3, client: 'SABnzbd', remote: '/downloads', local: '/data/usenet' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    const card = document.querySelector('[data-problem="1"]')!
    expect(card.querySelector('.rchip')!.textContent).toBe('Pfad nicht gefunden')
    expect(plain(card.querySelector('.pair')!.textContent)).toContain('/downloads')
    expect(plain(card.querySelector('.pair')!.textContent)).toContain('/data/usenet')
    // The card says in one sentence what to do.
    expect(plain(card.querySelector('[data-testid="mapping-todo"]')!.textContent)).toBe('Prüf den Vorschlag und bestätige ihn. Danach holt nexbooks die Datei selbst.')
    await page.click(card.querySelector('[data-act="mapping"]'))
    expect(page.dialog!.textContent).toContain('Ist das derselbe Ordner?')
    expect(page.dialog!.textContent).toContain('Danach wartet das Buch auf das Einordnen.')
    expect(page.dialog!.textContent).not.toContain('gleich ab')
    expect(server.changes()).toEqual([])
    await page.click(page.dialog!.querySelector('[data-act="confirm-mapping"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/1/mapping')!.body).toEqual({ remote: '/downloads', local: '/data/usenet' })
  })

  it('lets the operator pick when the folder exists more than once', async () => {
    auth.me = { id: 1, name: 'jule', role: 'operator', profile: {} } as unknown as Me
    serve({
      'GET /api/v1/procure/problems': { problems: [path({ values: { search: 'twice' } })], foreign: [] },
      'GET /api/v1/procure/downloads/1/mapping': { reported: '/dl/x', search: 'twice', proposal: null, matches: ['/data/a/x', '/data/b/x'], choices: [{ remote: '/dl', local: '/data/a', place: '/data/a/x' }, { remote: '/dl', local: '/data/b', place: '/data/b/x' }], roots: [] },
      'POST /api/v1/procure/downloads/1/mapping': { id: 3, client: 'SABnzbd', remote: '/dl', local: '/data/b' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(document.querySelector('[data-testid="mapping-todo"]')!.textContent)).toBe('Wähl den richtigen Ordner und bestätige ihn. Danach holt nexbooks die Datei selbst.')
    await page.click(document.querySelector('[data-act="mapping"]'))
    const radios = [...page.dialog!.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
    expect(radios).toHaveLength(2)
    await page.click(radios[1])
    await page.click(page.dialog!.querySelector('[data-act="confirm-mapping"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/1/mapping')!.body).toEqual({ remote: '/dl', local: '/data/b' })
  })

  it('tells a member to ask the operator and gives no button for the mapping', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [path({ can_map: false, values: { search: 'proposed' } })], foreign: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(document.querySelector('[data-act="mapping"]')).toBeNull()
    expect(document.querySelector('[data-testid="mapping-todo"]')).toBeNull()
    expect(text()).toContain('Die Betreiberin oder der Betreiber kann die Zuordnung bestätigen.')
  })

  it('checks again, chooses another library and cancels', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'no_write' })], foreign: [] },
      'POST /api/v1/procure/downloads/1/recheck': { id: 1, state: 'problem' },
      'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }, { id: 2, name: 'Hörbücher', kind: 'audio' }, { id: 3, name: 'Familie', kind: 'ebook' }] },
      'POST /api/v1/procure/downloads/1/library': { id: 1, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(text()).toContain('nexbooks darf in „Bücher“ nicht schreiben.')
    await page.click(document.querySelector('[data-act="recheck"]'))
    expect(server.changes()).toEqual(['POST /api/v1/procure/downloads/1/recheck'])
    await page.click(document.querySelector('[data-act="library"]'))
    // Only libraries that take an e-book are offered.
    expect([...page.dialog!.querySelectorAll('.opt b')].map((item) => plain(item.textContent))).toEqual(['Bücher', 'Familie'])
    expect((page.dialog!.querySelector('[data-act="choose-library"]') as HTMLButtonElement).disabled).toBe(true)
    await page.click(page.dialog!.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1])
    await page.click(page.dialog!.querySelector('[data-act="choose-library"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/1/library')!.body).toEqual({ library_id: 3 })
  })

  it('shows a job nobody asked for, and ignoring or removing it asks first', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [foreign(7)] },
      'POST /api/v1/procure/foreign/7/ignore': 204,
      'DELETE /api/v1/procure/foreign/7': 204,
    })
    await page.show(app(), '/acquire?reiter=needs')
    const card = document.querySelector('[data-foreign="7"]')!
    expect(card.querySelector('.rchip')!.textContent).toBe('Fremder Auftrag in der Kategorie')
    expect(plain(card.querySelector('.rel')!.textContent)).toContain('Unbekannter.Autor')
    await page.click(card.querySelector('[data-act="ignore"]'))
    expect(page.dialog!.textContent).toContain('Er bleibt im Download-Programm, wie er ist.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    await page.click(card.querySelector('[data-act="remove-job"]'))
    expect(page.dialog!.textContent).toContain('Das lässt sich nicht zurückholen.')
    await page.inDialog('Entfernen')
    expect(server.changes()).toEqual(['DELETE /api/v1/procure/foreign/7'])
  })

  it('says it plainly when nothing needs anybody', async () => {
    serve()
    await page.show(app(), '/acquire?reiter=needs')
    expect(document.querySelector('[data-testid="needs-empty"]')!.textContent).toContain('Nichts, was dich braucht.')
  })
})

describe('Verlauf and Sperrliste', () => {
  it('lists what became of the downloads and narrows it by the filter', async () => {
    serve({
      'GET /api/v1/procure/history': { entries: [line(1), line(2, { result: 'failed', note: { code: 'client_failed', detail: 'password' } }), line(3, { result: 'removed', note: { code: 'cancelled', blocked: false } }), line(4, { result: 'failed', note: { code: 'interrupted' } })] },
      'GET /api/v1/procure/history?result=bad': { entries: [line(2, { result: 'failed', note: { code: 'client_failed', detail: 'password' } })] },
    })
    await page.show(app(), '/acquire?reiter=history')
    expect(document.querySelectorAll('.hr')).toHaveLength(4)
    expect(plain(document.querySelector('[data-result="failed"]')!.textContent)).toContain('Archiv mit Passwort, nicht zu öffnen')
    expect(plain(document.body.textContent)).toContain('Die Übergabe wurde durch einen Neustart unterbrochen')
    expect(plain(document.querySelector('[data-result="done"] .rchip')!.textContent)).toBe('geladen, geprüft, eingeordnet')
    await page.click(document.querySelector('[data-hist="bad"]'))
    expect(server.calls.some((call) => call.url === '/api/v1/procure/history?result=bad')).toBe(true)
    expect(document.querySelectorAll('.hr')).toHaveLength(1)
  })

  it('shows the blocked releases, with who blocked them, and unblocks only after the question', async () => {
    serve({
      'GET /api/v1/procure/blocklist': { entries: [blockLine(1), blockLine(2, { by: '', reason: 'other_language' })] },
      'DELETE /api/v1/procure/blocklist/1': 204,
    })
    await page.show(app(), '/acquire?reiter=blocklist')
    expect(document.querySelectorAll('.hr')).toHaveLength(2)
    expect(plain(document.querySelector('[data-block="1"]')!.textContent)).toContain('von Tom gesperrt')
    expect(plain(document.querySelector('[data-block="2"]')!.textContent)).toContain('automatisch gesperrt')
    await page.click(document.querySelector('[data-block="1"] [data-act="unblock"]'))
    expect(page.dialog!.textContent).toContain('darf dieses Release bei der nächsten Suche wieder nehmen')
    expect(server.changes()).toEqual([])
    await page.inDialog('Entsperren')
    expect(server.changes()).toEqual(['DELETE /api/v1/procure/blocklist/1'])
  })

  it('gives every blocked release a whole sentence, never one with an empty place in it', async () => {
    serve({
      'GET /api/v1/procure/blocklist': { entries: [blockLine(1, { reason: 'other_language' }), blockLine(2, { reason: 'format_not_wanted' }), blockLine(3, { reason: 'bundle' })] },
      'DELETE /api/v1/procure/blocklist/1': 204,
    })
    await page.show(app(), '/acquire?reiter=blocklist')
    const why = (id: number) => plain(document.querySelector(`[data-block="${id}"] .hres small`)!.textContent)
    expect(why(1)).toBe('Das Release war in einer anderen Sprache als gewünscht.')
    expect(why(2)).toBe('Das Format steht in der Rangliste nicht angehakt.')
    expect(why(3)).toBe('Das Paket enthält mehrere Bände, nicht nur den gesuchten.')
    for (const id of [1, 2, 3]) expect(document.querySelector(`[data-block="${id}"]`)!.textContent).not.toMatch(/ist \.| ,|\{\{/)
    await page.click(document.querySelector('[data-block="1"] [data-act="unblock"]'))
    expect(plain(page.dialog!.textContent)).toContain('weil: Das Release war in einer anderen Sprache als gewünscht.')
  })

  it('says it when the history and the blocklist are empty', async () => {
    serve()
    await page.show(app(), '/acquire?reiter=history')
    expect(document.querySelector('[data-testid="history-empty"]')).not.toBeNull()
    page.close()
    serve()
    await page.show(app(), '/acquire?reiter=blocklist')
    expect(document.querySelector('[data-testid="block-empty"]')!.textContent).toBe('Keine gesperrten Releases.')
  })

  it('keeps the page standing when a list cannot be read', async () => {
    serve()
    await page.show(app(), '/acquire?reiter=history')
    server.fails['GET /api/v1/procure/history?result=done'] = { status: 500, code: 'internal_error' }
    await page.click(document.querySelector('[data-hist="done"]'))
    expect(text()).toContain('Die Liste ließ sich nicht laden.')
    await idle()
  })
})

describe('Gesucht, the automatic work', () => {
  beforeEach(cards)

  it('says when a wish was searched last, why nothing passed, and that a release is handed over again', async () => {
    const searched = new Date(Date.now() - 2 * 3_600_000).toISOString()
    const later = new Date(Date.now() + 4 * 3_600_000 - 60_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'not_found', searched_at: searched, next_try_at: later, detail: { total: 4, reasons: [{ code: 'abridged', count: 3 }, { code: 'dramatized', count: 1 }] } }),
          wish(2, { state: 'not_found', next_try_at: later, detail: { total: 1, reasons: [], load_error: 'client_unreachable' }, handing_over_again: true }),
          wish(3, { state: 'upgrade', searched_at: searched, next_try_at: later, detail: { have: 'mobi', goal: 'epub' } }),
          wish(4, { state: 'upgrade', language: '', next_try_at: later, detail: { have: 'epub', goal: 'epub', have_language: 'en', goal_language: 'de' } }),
          wish(6, { state: 'not_found', next_try_at: later, detail: { total: 2, look: 2, reasons: [{ code: 'maybe_other_volume', count: 2 }] } }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    const row = (id: number) => plain(document.querySelector(`[data-wish="${id}"] .rq-state`)!.textContent)
    expect(row(4)).toContain('Hat es auf Englisch, sucht es auf Deutsch. Nächster Versuch in 4 Std.')
    expect(row(6)).toContain('2 unsichere Treffer warten unter Braucht dich. Nächster Versuch in 4 Std.')
    expect(row(1)).toContain('Nächster Versuch in 4 Std. Zuletzt gesucht vor 2 Stunden.')
    expect(plain(document.querySelector('[data-wish="1"] .why ul')!.textContent)).toBe('3 × gekürzt1 × Hörspiel')
    expect(row(2)).toContain('Ein Release passt. Das Download-Programm hat nicht geantwortet, nexbooks gibt es gleich noch einmal ab.')
    expect(row(2)).not.toContain('Nächster Versuch')
    expect(row(3)).toContain('Hat MOBI, sucht bis EPUB. Nächster Versuch in 4 Std. Zuletzt gesucht vor 2 Stunden.')
  })

  it('says for a wish held back by the limit of the day when it goes on, with the day when that is not today, and the reasons', async () => {
    const tomorrow = new Date(Date.now() + 30 * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'capped', next_try_at: tomorrow, detail: { indexer: 'Leseclub', until: tomorrow, code: 'indexer_daily_limit', total: 3, reasons: [{ code: 'bundle', count: 3 }] } }),
          wish(2, { state: 'capped', next_try_at: tomorrow, detail: { until: tomorrow, code: 'indexer_limit_reached' } }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    const row = (id: number) => plain(document.querySelector(`[data-wish="${id}"] .rq-state`)!.textContent)
    const day = new Date(tomorrow).toLocaleString('de', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
    expect(row(1)).toContain(`Wartet aufs Tageslimit bis ${day}`)
    expect(row(1)).toContain('Leseclub hat sein Tageslimit in nexbooks erreicht.')
    expect(plain(document.querySelector('[data-wish="1"] .why ul')!.textContent)).toBe('3 × Sammelpaket')
    expect(row(2)).toContain(`Wartet aufs Tageslimit bis ${day}`)
    expect(row(2)).toContain('Ein Indexer hat sein Limit erreicht.')
  })

  it('says in one line how many wishes wait for the limit of the day and when the first goes on', async () => {
    const soon = new Date(Date.now() + 2 * 3_600_000).toISOString()
    const late = new Date(Date.now() + 30 * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'capped', next_try_at: late, detail: { indexer: 'Leseclub', until: late, code: 'indexer_daily_limit', limit: 100 } }),
          wish(2, { state: 'capped', next_try_at: soon, detail: { indexer: 'Leseclub', until: soon, code: 'indexer_daily_limit', limit: 100 } }),
          wish(3, { state: 'searching' }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    const first = new Date(soon).toLocaleString('de', { hour: '2-digit', minute: '2-digit', ...(new Date(soon).toDateString() === new Date().toDateString() ? {} : { weekday: 'short' }) })
    expect(plain(document.querySelector('[data-testid="wanted-capped"]')!.textContent)).toBe(`2 Wünsche warten aufs Tageslimit. Weiter ab ${first}.`)
    expect(plain(document.querySelector('[data-wish="1"] .rq-state')!.textContent)).toContain('Leseclub hat sein Tageslimit in nexbooks erreicht (100 Suchen).')
  })

  it('has no such line while no wish waits for a limit', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'searching' })] } })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="wanted-capped"]')).toBeNull()
  })

  it('says for a wish that had its downloads of the day that it goes on when the first is a day old', async () => {
    const later = new Date(Date.now() + 20 * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [wish(1, { state: 'not_found', next_try_at: later, detail: { total: 0, reasons: [], error: 'replace_limit' } })],
      },
    })
    await page.show(app(), '/acquire')
    const text = plain(document.querySelector('[data-wish="1"] .rq-state')!.textContent)
    expect(text).toContain('Dieser Wunsch hatte in 24 Stunden schon so viele Downloads, wie erlaubt sind. Es geht weiter, sobald der erste einen Tag alt ist.')
    expect(text).not.toContain('Ersatzversuche')
  })

  it('says for a wish held back by the share of a person that it is their share, not the limit of the indexer', async () => {
    const tomorrow = new Date(Date.now() + 30 * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [wish(1, { state: 'capped', next_try_at: tomorrow, detail: { indexer: 'Leseclub', until: tomorrow, code: 'indexer_share_used' } })],
      },
    })
    await page.show(app(), '/acquire')
    const day = new Date(tomorrow).toLocaleString('de', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
    const text = plain(document.querySelector('[data-wish="1"] .rq-state')!.textContent)
    expect(text).toContain(`Wartet aufs Tageslimit bis ${day}`)
    expect(text).toContain('Dein Anteil an Leseclub ist für heute aufgebraucht.')
    expect(text).not.toContain('sein Limit')
  })
})
