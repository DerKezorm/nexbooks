/**
 * Settings, Fetching, Indexers: the cards with their states, the day's count and categories, Prowlarr, and the dialog that
 * tests an address before it saves. What removes or disconnects asks first, and a key is never shown.
 */
import type { IndexerCategory, IndexerInfo, IndexerTest, ProwlarrInfo } from '../../../api/client'
import { changeLanguage } from '../../../i18n'
import { Page, Server, plain } from '../../../test/harness'
import { IndexersPart } from './IndexersPart'

const server = new Server()
const page = new Page()

const KEY = 'k-' + 'x'.repeat(30)

function category(id: number, name: string, over: Partial<IndexerCategory> = {}): IndexerCategory {
  return { id, name, type: id >= 7000 ? 'ebook' : 'audio', lang: 'mixed', on: true, suggested: true, ...over }
}
function indexer(id: number, over: Partial<IndexerInfo> = {}): IndexerInfo {
  return {
    id, name: `Bücherei ${id}`, kind: 'newznab', url: `https://buecherei${id}.example.com/api`, key_set: true, enabled: true, priority: id, daily_limit: 200, state: 'ready', used_today: 42, grabs: 0, grabs_today: 0, rss_checked_at: null, rss_error: '', day_ends_at: '2026-10-17T00:00:00Z', via_prowlarr: false,
    categories: [category(7020, 'Books/EBook'), category(7030, 'Books/Comics', { on: false, suggested: false }), category(3030, 'Audio/Audiobook')], resting_until: null, resting_cause: '', last_error: '',
    api_current: null, api_max: null, grab_current: null, grab_max: null,
    supported_params: { search: ['q'], book: ['q', 'author', 'isbn'], audio: [], book_search: true, audio_search: false }, caps_checked_at: null, ...over,
  }
}
const prowlarr = (over: Partial<ProwlarrInfo> = {}): ProwlarrInfo => ({ connected: false, url: '', key_set: false, auto_add: true, version: '', last_sync_at: null, last_error: '', counts: {}, taken: 0, sync_state: 'idle', ...over })
const tested = (over: Partial<IndexerTest> = {}): IndexerTest => ({
  kind: 'newznab', book_search: true, audio_search: false, search_params: ['q'], book_params: ['q', 'author', 'isbn'], audio_params: [], category_count: 16, items: 1, indexer: null,
  categories: [category(7120, 'Books/EBook German', { lang: 'de' }), category(7020, 'Books/EBook'), category(7320, 'Books/EBook French', { lang: 'fr', on: false, suggested: false }), category(3030, 'Audio/Audiobook')], ...over,
})

const LIST = 'GET /api/v1/procure/indexers'
const PROW = 'GET /api/v1/procure/prowlarr'

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { [LIST]: { indexers: [indexer(1)] }, [PROW]: prowlarr() }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const text = (testid: string) => plain(document.querySelector(`[data-testid="${testid}"]`)!.textContent)

describe('the cards', () => {
  it('say the state, the day, the priority and the categories that are asked', async () => {
    await page.show(<IndexersPart />)
    expect(text('indexer-state-1')).toBe('bereit')
    expect(text('indexer-use-1')).toBe('Heute 42 von 200 Abrufen · Priorität 1')
    const card = document.querySelector('[data-testid="indexer-1"]')!
    expect(plain(card.textContent)).toContain('Books/EBook 7020')
    expect(plain(card.textContent)).toContain('Audio/Audiobook 3030')
    expect(plain(card.textContent)).not.toContain('Comics')
    expect(plain(card.textContent)).toContain('Newznab')
    expect(page.text).toContain('1 eingerichtet. Zuerst fragt nexbooks die mit der kleinsten Priorität.')
  })

  it('mark a category of a language that is not fetched as not asked, and say why', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { languages: ['de'], categories: [category(7020, 'Ebook'), category(7320, 'Ebook', { lang: 'fr', asked: false })] })] }
    await page.show(<IndexersPart />)
    expect(document.querySelector('[data-testid="indexer-1"] [data-asked="0"]')!.textContent).toContain('7320')
    expect(document.querySelectorAll('[data-testid="indexer-1"] [data-asked="0"]').length).toBe(1)
    expect(text('indexer-not-asked-1')).toBe('Eine Kategorie wird nicht gefragt, ihre Sprache ist unter Beschaffung → Sprachen nicht erlaubt.')
  })

  it('say nothing of the kind while every category is asked', async () => {
    await page.show(<IndexersPart />)
    expect(document.querySelector('[data-testid="indexer-not-asked-1"]')).toBeNull()
    expect(document.querySelector('[data-testid="indexer-proposals-1"]')).toBeNull()
  })

  it('say when chosen languages differ from what the indexer states, and take its proposals on request', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { proposals: 1, categories: [category(7120, 'Books/EBook German', { lang: 'mixed', proposed: 'de' })] })] }
    server.answers['POST /api/v1/procure/indexers/1/proposals'] = indexer(1)
    await page.show(<IndexersPart />)
    expect(text('indexer-proposals-1')).toBe('Bei 1 Kategorie weicht die Sprache von der Angabe des Indexers ab.Vorschläge übernehmen')
    server.answers[LIST] = { indexers: [indexer(1)] }
    await page.click(page.button('Vorschläge übernehmen'))
    expect(server.called('POST', '/api/v1/procure/indexers/1/proposals')).toBeTruthy()
    expect(document.querySelector('[data-testid="indexer-proposals-1"]')).toBeNull()
  })

  it('count without a limit as "keine Grenze"', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 0, used_today: 6 })] }
    await page.show(<IndexersPart />)
    expect(text('indexer-use-1')).toBe('Heute 6 Abrufe, keine Grenze · Priorität 1')
  })

  it('say that an indexer rests, until when and why', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { state: 'resting', resting_until: '2026-10-16T00:00:00Z', resting_cause: 'daily_limit' })] }
    await page.show(<IndexersPart />)
    expect(text('indexer-state-1')).toMatch(/^pausiert bis \d\d:\d\d$/)
    expect(text('indexer-note-1')).toMatch(/^Tagesgrenze erreicht\. Ab \d\d:\d\d fragt nexbooks wieder\.$/)
  })

  it('say that the key is refused and offer a new one', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { state: 'key_rejected' })] }
    await page.show(<IndexersPart />)
    expect(text('indexer-state-1')).toBe('Schlüssel abgelehnt')
    expect(page.text).toContain('Der Indexer lehnt den API-Schlüssel ab. Trage einen neuen ein.')
    expect(page.button('Schlüssel ändern')).toBeTruthy()
  })

  it('show an indexer that is off as off, and switch it on', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { state: 'off', enabled: false })] }
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1)
    await page.show(<IndexersPart />)
    expect(text('indexer-state-1')).toBe('aus')
    await page.click(page.button('Einschalten'))
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toEqual({ enabled: true })
  })

  it('switch an indexer off with one press', async () => {
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1, { enabled: false, state: 'off' })
    await page.show(<IndexersPart />)
    await page.click(page.button('Ausschalten'))
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toEqual({ enabled: false })
  })

  it('test a saved indexer and say what it found', async () => {
    server.answers['POST /api/v1/procure/indexers/1/test?language=de'] = tested({ indexer: indexer(1) })
    await page.show(<IndexersPart />)
    await page.click(page.button('Testen', document.querySelector('[data-testid="indexer-1"]')!))
    expect(page.text).toContain('Bücherei 1 antwortet. Buchsuche und 16 Kategorien sind da.')
  })

  it('say why a test failed, in words', async () => {
    server.fails['POST /api/v1/procure/indexers/1/test?language=de'] = { status: 502, code: 'indexer_key_rejected' }
    await page.show(<IndexersPart />)
    await page.click(page.button('Testen', document.querySelector('[data-testid="indexer-1"]')!))
    expect(page.text).toContain('Der Indexer lehnt den API-Schlüssel ab.')
  })

  it('never show a key', async () => {
    await page.show(<IndexersPart />)
    expect(page.text).not.toContain(KEY)
    expect(document.querySelector<HTMLInputElement>('input[type="password"]')).toBeNull()
  })

  it('have a card to add one, and a line that says what an empty list means', async () => {
    server.answers[LIST] = { indexers: [] }
    await page.show(<IndexersPart />)
    expect(page.text).toContain('Noch keiner eingerichtet.')
    expect(page.text).toContain('Ohne Indexer kann nexbooks nichts suchen. Bis dahin bleiben Wünsche liegen.')
    expect(document.querySelector('[data-testid="indexer-add"]')).toBeTruthy()
  })
})

describe('removing', () => {
  it('asks first, says what stays, and sends nothing before the answer', async () => {
    server.answers['DELETE /api/v1/procure/indexers/1'] = 204
    await page.show(<IndexersPart />)
    await page.click(page.button('Indexer entfernen'))
    expect(page.dialog).toBeTruthy()
    expect(plain(page.dialog!.textContent)).toContain('Bereits geladene Bücher und der Verlauf bleiben. Du kannst ihn später neu eintragen.')
    expect(server.called('DELETE', '/api/v1/procure/indexers/1')).toBeUndefined()
    await page.inDialog('Abbrechen')
    expect(server.called('DELETE', '/api/v1/procure/indexers/1')).toBeUndefined()
    await page.click(page.button('Indexer entfernen'))
    await page.inDialog('Indexer entfernen')
    expect(server.called('DELETE', '/api/v1/procure/indexers/1')).toBeTruthy()
  })

  it('does not offer to remove an indexer that comes from Prowlarr', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { via_prowlarr: true })] }
    await page.show(<IndexersPart />)
    const remove = page.button('Indexer aus Prowlarr entfernst du in Prowlarr') as HTMLButtonElement
    expect(remove.disabled).toBe(true)
    expect(plain(document.querySelector('[data-testid="indexer-1"]')!.textContent)).toContain('über Prowlarr')
  })
})

describe('Prowlarr', () => {
  it('invites to connect when there is none', async () => {
    await page.show(<IndexersPart />)
    expect(text('prowlarr-state')).toBe('nicht verbunden')
    expect(page.text).toContain('Hast du Prowlarr, trage Adresse und Schlüssel ein.')
    expect(page.button('Prowlarr verbinden')).toBeTruthy()
  })

  it('says how many indexers it gave, how many it left out and when it was read', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true, taken: 12, last_sync_at: new Date(Date.now() - 5 * 60_000).toISOString(), counts: { skipped_categories: 3, skipped_disabled: 1, skipped_unsupported: 0 } })
    await page.show(<IndexersPart />)
    expect(text('prowlarr-state')).toBe('verbunden')
    expect(text('prowlarr-url')).toBe('http://prowlarr.example.com:9696')
    expect(text('prowlarr-line')).toBe('12 Indexer übernommen · 4 ohne Buchkategorien übersprungen · abgeglichen vor 5 Minuten')
  })

  it('says that the last sync went wrong and why', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true, last_error: 'prowlarr_key_rejected' })
    await page.show(<IndexersPart />)
    expect(text('prowlarr-state')).toBe('Abgleich gescheitert')
    expect(text('prowlarr-error')).toBe('Der letzte Abgleich ging schief: Prowlarr lehnt den API-Schlüssel ab.')
  })

  it('reads again on request', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true })
    server.answers['POST /api/v1/procure/prowlarr/sync'] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true, sync_state: 'queued' })
    await page.show(<IndexersPart />)
    await page.click(page.button('Jetzt abgleichen'))
    expect(server.called('POST', '/api/v1/procure/prowlarr/sync')).toBeTruthy()
  })

  it('takes new indexers or not, with the switch', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true })
    server.answers['PUT /api/v1/procure/prowlarr'] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true, auto_add: false })
    await page.show(<IndexersPart />)
    await page.click(document.querySelector('[role="switch"][aria-label="Neue Indexer aus Prowlarr übernehmen"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/procure/prowlarr')!.body).toEqual({ url: 'http://prowlarr.example.com:9696', auto_add: false })
  })

  it('asks before it lets go of Prowlarr and says what goes with it', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true })
    server.answers['DELETE /api/v1/procure/prowlarr'] = 204
    await page.show(<IndexersPart />)
    await page.click(page.button('Trennen'))
    expect(plain(page.dialog!.textContent)).toContain('Die aus Prowlarr übernommenen Indexer verschwinden aus nexbooks. Selbst eingetragene bleiben.')
    expect(server.called('DELETE', '/api/v1/procure/prowlarr')).toBeUndefined()
    await page.inDialog('Trennen')
    expect(server.called('DELETE', '/api/v1/procure/prowlarr')).toBeTruthy()
  })

  it('tests the connection in the dialog, then saves it with the key typed, which is not shown again', async () => {
    server.answers['POST /api/v1/procure/prowlarr/test'] = { version: '2.6.5.5623', indexers: 5, with_books: 3 }
    server.answers['PUT /api/v1/procure/prowlarr'] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true })
    await page.show(<IndexersPart />)
    await page.click(page.button('Prowlarr verbinden'))
    const dialog = page.dialog!
    await page.type(dialog.querySelector<HTMLInputElement>('input[placeholder="http://prowlarr.example.com:9696"]'), 'http://prowlarr.example.com:9696')
    await page.type(dialog.querySelector<HTMLInputElement>('input[type="password"]'), KEY)
    await page.click(page.button('Testen', dialog))
    expect(plain(document.querySelector('[data-testid="prowlarr-test"]')!.textContent)).toBe('Verbunden, 5 Indexer, davon 3 mit Buchkategorien, Version 2.6.5.5623.')
    await page.inDialog('Speichern')
    expect(server.called('PUT', '/api/v1/procure/prowlarr')!.body).toEqual({ url: 'http://prowlarr.example.com:9696', api_key: KEY, auto_add: true })
    expect(page.text).not.toContain(KEY)
  })

  it('tells what is wrong when the test fails', async () => {
    server.fails['POST /api/v1/procure/prowlarr/test'] = { status: 502, code: 'prowlarr_not_prowlarr' }
    await page.show(<IndexersPart />)
    await page.click(page.button('Prowlarr verbinden'))
    await page.type(page.dialog!.querySelector<HTMLInputElement>('input[placeholder="http://prowlarr.example.com:9696"]'), 'http://prowlarr.example.com')
    await page.click(page.button('Testen', page.dialog!))
    expect(plain(document.querySelector('[data-testid="prowlarr-test"]')!.textContent)).toContain('An dieser Adresse antwortet etwas, aber nicht Prowlarr.')
  })
})

describe('the dialog to add an indexer', () => {
  async function open(): Promise<HTMLElement> {
    await page.show(<IndexersPart />)
    await page.click(document.querySelector('[data-testid="indexer-add"]') as HTMLElement)
    return page.dialog!
  }
  /** The input of the field with this name above it. */
  const field = (dialog: HTMLElement, label: string) => [...dialog.querySelectorAll('label')].find((item) => plain(item.querySelector('span')?.textContent) === label)?.querySelector('input') as HTMLInputElement

  it('asks for the test before it saves, and for a name and an address before that', async () => {
    const dialog = await open()
    await page.inDialog('Indexer anlegen')
    expect(text('indexer-local-problem')).toBe('Name und Adresse fehlen.')
    await page.type(field(dialog, 'Name'), 'Meine Bücherei')
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.inDialog('Indexer anlegen')
    expect(text('indexer-local-problem')).toBe('Teste zuerst die Verbindung.')
    expect(server.called('POST', '/api/v1/procure/indexers')).toBeUndefined()
    expect(dialog.querySelector('[data-testid="indexer-categories"]')).toBeNull()
  })

  it('tests, says what the indexer can, and offers its categories with the fitting ones chosen', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested()
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(text('indexer-test')).toBe('Verbunden. Der Indexer kann Bücher suchen (q, author, isbn) und meldet 16 Kategorien.')
    const rows = [...dialog.querySelectorAll<HTMLInputElement>('[data-testid="indexer-categories"] input[type="checkbox"]')]
    expect(rows.map((box) => box.checked)).toEqual([true, true, false, true])
    expect(plain(dialog.querySelector('[data-testid="indexer-categories"]')!.textContent)).toContain('Vorschlag')
    expect(server.called('POST', '/api/v1/procure/indexers/test')!.body).toMatchObject({ kind: 'newznab', url: 'https://indexer.example.com/api', language: 'de' })
  })

  it('says beside a category of a language not fetched that it is not asked, also after its language is changed', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested({ languages: ['de'] })
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(text('category-not-asked-7320')).toBe('wird nicht gefragt: Französisch ist unter Sprachen nicht erlaubt')
    expect(dialog.querySelector('[data-testid="category-not-asked-7120"]')).toBeNull()
    expect(dialog.querySelector('[data-testid="category-not-asked-7020"]')).toBeNull()
    const select = dialog.querySelector<HTMLSelectElement>('select[aria-label*="7320"], select[aria-label*="Books/EBook French"]')!
    await page.choose(select, 'de')
    expect(dialog.querySelector('[data-testid="category-not-asked-7320"]')).toBeNull()
  })

  it('offers the language the indexer states beside a category set to another, and takes it with one press', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested({ categories: [category(7120, 'Books/EBook German', { lang: 'de', proposed: 'de' })] })
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(dialog.querySelector('[data-testid="category-proposed-7120"]')).toBeNull()
    const select = dialog.querySelector<HTMLSelectElement>('select[aria-label*="Books/EBook German"]')!
    await page.choose(select, 'mixed')
    expect(text('category-proposed-7120')).toBe('Vorschlag: Deutsch')
    await page.click(dialog.querySelector('[data-testid="category-proposed-7120"]'))
    expect(select.value).toBe('de')
    expect(dialog.querySelector('[data-testid="category-proposed-7120"]')).toBeNull()
  })

  it('says when the indexer has no book search of its own', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested({ book_search: false, book_params: [] })
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(text('indexer-test')).toContain('keine eigene Buchsuche')
  })

  it('saves what was chosen, the key included, and closes', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested()
    server.answers['POST /api/v1/procure/indexers'] = indexer(2)
    const dialog = await open()
    await page.type(field(dialog, 'Name'), 'Meine Bücherei')
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.type(dialog.querySelector<HTMLInputElement>('input[type="password"]'), KEY)
    await page.click(page.button('Testen', dialog))
    await page.click(dialog.querySelector<HTMLInputElement>('[data-testid="indexer-categories"] input[type="checkbox"]:nth-of-type(1)'))
    await page.inDialog('Indexer anlegen')
    const sent = server.called('POST', '/api/v1/procure/indexers')!.body as Record<string, unknown>
    expect(sent).toMatchObject({ name: 'Meine Bücherei', kind: 'newznab', url: 'https://indexer.example.com/api', api_key: KEY, priority: 2, daily_limit: 0 })
    expect(sent.categories).toEqual([7020, 3030])
    expect(sent.category_langs).toEqual({ 7020: 'mixed', 3030: 'mixed' })
    expect(page.dialog).toBeNull()
  })

  it('needs a new test once the address, the kind or the key changes', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested()
    const dialog = await open()
    await page.type(field(dialog, 'Name'), 'X')
    await page.type(field(dialog, 'Adresse'), 'https://indexer.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(dialog.querySelector('[data-testid="indexer-categories"]')).toBeTruthy()
    await page.type(field(dialog, 'Adresse'), 'https://other.example.com/api')
    await page.inDialog('Indexer anlegen')
    expect(text('indexer-local-problem')).toBe('Teste zuerst die Verbindung.')
  })

  it('shows why a test failed and saves nothing', async () => {
    server.fails['POST /api/v1/procure/indexers/test'] = { status: 502, code: 'indexer_unreachable' }
    const dialog = await open()
    await page.type(field(dialog, 'Adresse'), 'https://nowhere.example.com/api')
    await page.click(page.button('Testen', dialog))
    expect(text('indexer-test')).toBe('Der Indexer ist nicht erreichbar. Stimmen Adresse und Port?')
    expect(server.called('POST', '/api/v1/procure/indexers')).toBeUndefined()
  })
})

describe('the dialogs as the design draws them', () => {
  it('are the broad form with the address and the key in the typewriter face', async () => {
    await page.show(<IndexersPart />)
    await page.click(document.querySelector('[data-testid="indexer-add"]') as HTMLElement)
    const panel = page.dialog!
    expect(panel.className).toContain('max-w-[1040px]')
    const inputs = [...panel.querySelectorAll<HTMLInputElement>('input')]
    const address = inputs.find((item) => item.placeholder === 'https://indexer.example.com/api')!
    const key = inputs.find((item) => item.type === 'password')!
    const name = inputs.find((item) => item.placeholder === 'z. B. Bücherei')!
    expect(address.className).toContain('font-mono')
    expect(key.className).toContain('font-mono')
    expect(name.className).not.toContain('font-mono')
    // The kind stands beside the name, not above it, and the chosen kind is tinted.
    expect(panel.querySelector('[role="radiogroup"]')!.parentElement!.parentElement!.parentElement).toBe(name.closest('.grid'))
    expect(panel.querySelector('[role="radio"][aria-checked="true"]')!.className).toContain('bg-accent-soft')
  })
})

describe('an address that points elsewhere', () => {
  const edit = async () => {
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1)
    server.answers['POST /api/v1/procure/indexers/test'] = tested()
    await page.show(<IndexersPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="indexer-1"]')!))
    return page.dialog!
  }
  const address = (dialog: HTMLElement) => dialog.querySelector<HTMLInputElement>('input[placeholder="https://indexer.example.com/api"]')!

  it('asks for the key again and sends nothing, for the test and for saving', async () => {
    const dialog = await edit()
    await page.type(address(dialog), 'https://elsewhere.example.com/api')
    expect(plain(dialog.textContent)).toContain('nexbooks schickt den gespeicherten Schlüssel nie an eine andere Stelle')
    expect(dialog.querySelector<HTMLInputElement>('input[type="password"]')!.placeholder).not.toBe('gespeichert, leer lassen zum Behalten')
    await page.click(page.button('Testen', dialog))
    await page.inDialog('Speichern')
    expect(server.called('POST', '/api/v1/procure/indexers/test')).toBeUndefined()
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')).toBeUndefined()
  })

  it('goes through once the key is typed again', async () => {
    const dialog = await edit()
    await page.type(address(dialog), 'https://elsewhere.example.com/api')
    await page.type(dialog.querySelector<HTMLInputElement>('input[type="password"]'), KEY)
    await page.click(page.button('Testen', dialog))
    expect(server.called('POST', '/api/v1/procure/indexers/test')!.body).toMatchObject({ api_key: KEY })
    await page.inDialog('Speichern')
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toMatchObject({ api_key: KEY, url: 'https://elsewhere.example.com/api' })
  })

  it('does not ask for it while the host and the port stay', async () => {
    const dialog = await edit()
    await page.type(address(dialog), 'https://buecherei1.example.com/other')
    expect(plain(dialog.textContent)).not.toContain('nie an eine andere Stelle')
    await page.click(page.button('Testen', dialog))
    expect(server.called('POST', '/api/v1/procure/indexers/test')).toBeTruthy()
  })

  it('is the same for Prowlarr: another port needs the key again', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true })
    server.answers['POST /api/v1/procure/prowlarr/test'] = { version: '2.6.5', indexers: 5, with_books: 3 }
    await page.show(<IndexersPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('#prowlarr')!))
    const dialog = page.dialog!
    await page.type(dialog.querySelector<HTMLInputElement>('input[placeholder="http://prowlarr.example.com:9696"]'), 'http://prowlarr.example.com:9999')
    await page.click(page.button('Testen', dialog))
    expect(plain(document.querySelector('[data-testid="prowlarr-test"]')!.textContent)).toContain('nie an eine andere Stelle')
    expect(server.called('POST', '/api/v1/procure/prowlarr/test')).toBeUndefined()
  })

  it('says it in words when the server refuses it', async () => {
    server.fails['PATCH /api/v1/procure/indexers/1'] = { status: 422, code: 'key_required' }
    const dialog = await edit()
    await page.inDialog('Speichern')
    expect(page.text).toContain('Die Adresse hat sich geändert. Trage den Schlüssel noch einmal ein')
    expect(dialog).toBeTruthy()
  })
})

describe('Prowlarr beyond the limit', () => {
  it('says how many indexers were left out because nexbooks holds at most 200', async () => {
    server.answers[PROW] = prowlarr({ connected: true, url: 'http://prowlarr.example.com:9696', key_set: true, taken: 12, last_error: 'prowlarr_too_many', counts: { skipped_limit: 5 } })
    await page.show(<IndexersPart />)
    expect(text('prowlarr-line')).toContain('5 wegen der Obergrenze von 200 Indexern nicht übernommen')
    expect(text('prowlarr-error')).toContain('nexbooks hält höchstens 200 Indexer')
  })
})

describe('the limits the indexer states', () => {
  it('say beside the own limit what the indexer allows, searches and grabs', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 0, api_max: 999999, grab_max: 400 })] }
    await page.show(<IndexersPart />)
    expect(text('indexer-reported-1')).toBe('Der Indexer erlaubt 999.999 Suchen und 400 Grabs am Tag.')
    expect(document.querySelector('[data-testid="indexer-below-1"]')).toBeNull()
  })

  it('name the one limit there is when the indexer states only one, and nothing when it states none', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 0, grab_max: 400 }), indexer(2, { daily_limit: 0 })] }
    await page.show(<IndexersPart />)
    expect(text('indexer-reported-1')).toBe('Der Indexer erlaubt 400 Grabs am Tag.')
    expect(document.querySelector('[data-testid="indexer-reported-2"]')).toBeNull()
  })

  it('warn when the own limit is below the one of the indexer, and take the limit of the indexer with one press', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 100, api_max: 999999, grab_max: 400 })] }
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1, { daily_limit: 0 })
    await page.show(<IndexersPart />)
    expect(text('indexer-below-1')).toContain('Deine Tagesgrenze von 100 liegt unter dem, was der Indexer erlaubt (999.999 Suchen).')
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 0, api_max: 999999, grab_max: 400 })] }
    await page.click(page.button('Grenze des Indexers übernehmen', document.querySelector('[data-testid="indexer-1"]')!))
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toEqual({ daily_limit: 0 })
    expect(document.querySelector('[data-testid="indexer-below-1"]')).toBeNull()
  })

  it('give no warning when the own limit is not below, or there is none, or the indexer states none', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 1000, api_max: 500 }), indexer(2, { daily_limit: 0, api_max: 500 }), indexer(3, { daily_limit: 50 })] }
    await page.show(<IndexersPart />)
    for (const id of [1, 2, 3]) expect(document.querySelector(`[data-testid="indexer-below-${id}"]`)).toBeNull()
  })

  it('show the same in the dialog, and take the limit of the indexer into the field', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { daily_limit: 100, api_max: 999999, grab_max: 400 })] }
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1)
    await page.show(<IndexersPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="indexer-1"]')!))
    const dialog = page.dialog!
    expect(text('indexer-dialog-reported')).toBe('Der Indexer erlaubt 999.999 Suchen und 400 Grabs am Tag.')
    expect(dialog.querySelector('[data-testid="indexer-dialog-below"]')).not.toBeNull()
    await page.click(page.button('Grenze des Indexers übernehmen', dialog))
    expect(dialog.querySelector('[data-testid="indexer-dialog-below"]')).toBeNull()
    await page.inDialog('Speichern')
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toMatchObject({ daily_limit: 0 })
  })

  it('offer a new indexer without an own limit', async () => {
    server.answers['POST /api/v1/procure/indexers/test'] = tested()
    server.answers['POST /api/v1/procure/indexers'] = indexer(2)
    await page.show(<IndexersPart />)
    await page.click(document.querySelector('[data-testid="indexer-add"]') as HTMLElement)
    const dialog = page.dialog!
    const limit = [...dialog.querySelectorAll('label')].find((item) => plain(item.querySelector('span')?.textContent) === 'Tagesgrenze')!.querySelector('input')!
    expect(limit.value).toBe('0')
    expect(dialog.querySelector('[data-testid="indexer-dialog-limits"]')).toBeNull()
  })
})

describe('the dialog to change an indexer', () => {
  it('keeps the saved key when none is typed, and sends only what is named', async () => {
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1)
    await page.show(<IndexersPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="indexer-1"]')!))
    const dialog = page.dialog!
    expect(dialog.querySelector<HTMLInputElement>('input[type="password"]')!.placeholder).toBe('gespeichert, leer lassen zum Behalten')
    await page.inDialog('Speichern')
    const sent = server.called('PATCH', '/api/v1/procure/indexers/1')!.body as Record<string, unknown>
    expect(sent).not.toHaveProperty('api_key')
    expect(sent).toMatchObject({ name: 'Bücherei 1', url: 'https://buecherei1.example.com/api', priority: 1, daily_limit: 200, categories: [7020, 3030] })
  })

  it('does not offer the name, the kind, the address or the key of an indexer from Prowlarr', async () => {
    server.answers[LIST] = { indexers: [indexer(1, { via_prowlarr: true })] }
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1)
    await page.show(<IndexersPart />)
    await page.click(page.button('Bearbeiten', document.querySelector('[data-testid="indexer-1"]')!))
    expect(page.dialog!.querySelector<HTMLInputElement>('input[type="password"]')).toBeNull()
    await page.inDialog('Speichern')
    const sent = server.called('PATCH', '/api/v1/procure/indexers/1')!.body as Record<string, unknown>
    expect(Object.keys(sent).sort()).toEqual(['categories', 'category_langs', 'daily_limit', 'priority'])
  })
})
