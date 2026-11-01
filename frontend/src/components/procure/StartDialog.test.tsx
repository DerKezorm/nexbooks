/**
 * The question when fetching is set up: what nexbooks brings and what it does not, and Audible, which is off until the
 * operator says so. Asked once (the answer is the switch of the source), in the settings of fetching and in the sources.
 */
import type { SourceInfo, SourcesOut } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { ProcurePart } from '../../pages/settings/procure/ProcurePart'
import { SourcesPart } from '../../pages/settings/SourcesCards'
import { Page, Server, plain } from '../../test/harness'
import { StartGate } from './StartDialog'

vi.mock('../../state/auth', () => ({ useAuth: () => ({ me: { profile: { timezone: 'Europe/Berlin' } }, status: 'signedIn' }) }))

const server = new Server()
const page = new Page()

function info(id: SourceInfo['id'], over: Partial<SourceInfo> = {}): SourceInfo {
  return { id, enabled: true, state: 'ready', requests_today: 0, requests_per_day: 3000, last_ok_at: null, last_error: '', last_error_at: null, resting_until: null, waiting: 0, ...over }
}
function state(over: Partial<SourcesOut> = {}): SourcesOut {
  return {
    enabled: true, markets: ['de', 'us'], available_markets: ['de', 'us'], hardcover_connected: false, audible_decided: false, author_info: true, waiting: 0, to_look_at: 0,
    sources: [info('hardcover', { state: 'not_connected' }), info('openlibrary'), info('audible', { enabled: false, state: 'off' }), info('dnb')],
    ...over,
  }
}
const PUT = 'PUT /api/v1/sources'

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'GET /api/v1/sources': state(), [PUT]: state({ audible_decided: true }) }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the question', () => {
  it('says what nexbooks brings and what it does not, and asks about Audible', async () => {
    await page.show(<StartGate />)
    const dialog = page.dialog!
    expect(plain(dialog.querySelector('[data-testid="start-notice"]')!.textContent)).toBe(
      'nexbooks bringt keine Indexer und keine Quellen mit. Du trägst sie selbst ein und bist dafür verantwortlich, nur Inhalte zu laden, die du nutzen darfst. Einen Kopierschutz umgeht nexbooks nie: Geschützte Bücher werden erkannt und abgelehnt.',
    )
    expect(plain(dialog.textContent)).toContain('Audible für Hörbücher nutzen?')
    expect(plain(dialog.textContent)).toContain('fragt nexbooks dich bei jedem geladenen Hörbuch')
    expect(page.button('Audible nutzen', dialog)).toBeTruthy()
    expect(page.button('Ohne Audible', dialog)).toBeTruthy()
    // Nothing was decided by looking at it.
    expect(server.changes()).toEqual([])
  })

  it('is in English too, in the same words in both', async () => {
    await changeLanguage('en', false)
    await page.show(<StartGate />)
    const dialog = page.dialog!
    expect(plain(dialog.textContent)).toContain('nexbooks comes with no indexers and no sources')
    expect(plain(dialog.textContent)).toContain('never gets around a copy protection')
    expect(page.button('Use Audible', dialog)).toBeTruthy()
    expect(page.button('Without Audible', dialog)).toBeTruthy()
    await changeLanguage('de', false)
  })

  it('switches Audible on with one click and is gone', async () => {
    await page.show(<StartGate />)
    await page.inDialog('Audible nutzen')
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ sources: { audible: true } })
    expect(page.dialog).toBeNull()
  })

  it('leaves Audible off with the other click and is gone, saying so to the server', async () => {
    await page.show(<StartGate />)
    await page.inDialog('Ohne Audible')
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ sources: { audible: false } })
    expect(page.dialog).toBeNull()
  })

  it('closes without an answer and sends nothing, to ask again at the next visit', async () => {
    await page.show(<StartGate />)
    await page.click(document.querySelector('[data-close]'))
    expect(page.dialog).toBeNull()
    expect(server.changes()).toEqual([])
    page.close()
    await page.show(<StartGate />)
    expect(page.dialog).not.toBeNull()
  })

  it('is not asked when the choice was made, in either way', async () => {
    server.answers['GET /api/v1/sources'] = state({ audible_decided: true })
    await page.show(<StartGate />)
    expect(page.dialog).toBeNull()
  })

  it('tells a refusal of the server inside the dialog and stays open', async () => {
    server.fails[PUT] = { status: 500, code: 'internal_error' }
    await page.show(<StartGate />)
    await page.inDialog('Audible nutzen')
    expect(page.dialog).not.toBeNull()
    expect(page.dialog!.querySelector('[role="alert"]')).not.toBeNull()
  })
})

describe('where it is asked', () => {
  it('in the settings of fetching, with the note on using it above the tabs', async () => {
    await page.show(<ProcurePart />)
    expect(page.dialog).not.toBeNull()
    expect(plain(document.querySelector('[data-testid="procure-note"]')!.textContent)).toContain('Zur Nutzung: nexbooks bringt keine Indexer und keine Quellen mit.')
  })

  it('in the sources, and the page shows the answer afterwards', async () => {
    await page.show(<SourcesPart />)
    expect(page.dialog).not.toBeNull()
    server.answers[PUT] = state({ audible_decided: true, sources: [info('hardcover', { state: 'not_connected' }), info('openlibrary'), info('audible'), info('dnb')] })
    await page.inDialog('Audible nutzen')
    expect(page.dialog).toBeNull()
    expect(plain(document.querySelector('[data-testid="source-state-audible"]')!.textContent)).toContain('Bereit')
  })
})
