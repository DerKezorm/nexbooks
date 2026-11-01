/**
 * Settings, Fetching, Automation: when the automatic work last ran and comes back, what each indexer's last
 * reading was, what was used today and fetched, the limit the indexer suggests, and the log of the rounds (numbers, no
 * titles, no keys).
 */
import { act } from 'react'

import type { AutomationConfig, AutomationStatus, IndexerInfo } from '../../../api/client'
import { changeLanguage } from '../../../i18n'
import { Page, Server, plain } from '../../../test/harness'
import { idle } from '../../../test/wait'
import { AutomationPart } from './AutomationPart'

const server = new Server()
const page = new Page()

const NOW = new Date('2026-10-16T12:00:00Z')
const AUTO: AutomationConfig = { enabled: true, rss_minutes: 15, retry_hours: 6, replace: true, replace_max: 3, seed: 'ratio', retention_days: 0 }

function indexer(id: number, over: Partial<IndexerInfo> = {}): IndexerInfo {
  return {
    id, name: `Bücherei ${id}`, kind: 'newznab', url: 'https://x.example.com/api', key_set: true, enabled: true, priority: id, daily_limit: 100, state: 'ready', used_today: 12, grabs: 4, grabs_today: 3,
    rss_checked_at: null, rss_error: '', day_ends_at: '2026-10-16T22:00:00Z', via_prowlarr: false, categories: [], resting_until: null, resting_cause: '', last_error: '', api_current: null, api_max: null, grab_current: null,
    grab_max: null, supported_params: { search: [], book: [], audio: [], book_search: false, audio_search: false }, caps_checked_at: null, ...over,
  }
}

function status(over: Partial<AutomationStatus> = {}): AutomationStatus {
  return { enabled: true, last_run_at: '2026-10-16T11:56:00Z', next_run_at: '2026-10-16T12:11:00Z', due: 0, waiting_for_a_day: 0, wishes_open: 0, indexers: [], day_ends_at: '2026-10-16T22:00:00Z', zone: 'Europe/Berlin', runs: [], ...over }
}

function show(over: Partial<AutomationStatus> = {}, indexers: IndexerInfo[] = [indexer(1)]) {
  server.answers = {
    'GET /api/v1/procure/automation': AUTO,
    'GET /api/v1/procure/automation/status?limit=12': status(over),
    'GET /api/v1/procure/indexers': { indexers },
  }
  return page.show(<AutomationPart />)
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
  await changeLanguage('de', false)
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the main switch', () => {
  it('says when the automatic work last ran and comes back next', async () => {
    await show()
    expect(page.text).toContain('Wünsche automatisch beschaffenLetzter Lauf vor 4 Minuten, der nächste in 11 Minuten.')
  })

  it('says it in English too', async () => {
    await changeLanguage('en', false)
    await show()
    expect(page.text).toContain('Fetch wishes automaticallyLast run 4 minutes ago, the next one in 11 minutes.')
  })

  it('says "now" for a run that is due and "no run yet" before the first', async () => {
    await show({ next_run_at: '2026-10-16T11:50:00Z' })
    expect(page.text).toContain('der nächste jetzt.')
    page.close()
    await show({ last_run_at: null, next_run_at: null })
    expect(page.text).toContain('An. Noch kein Lauf.')
  })

  it('names only the last run when no next one is planned, and only the next when there was none yet', async () => {
    await show({ next_run_at: null })
    expect(page.text).toContain('Letzter Lauf vor 4 Minuten.')
    page.close()
    await show({ last_run_at: null })
    expect(page.text).toContain('Der nächste Lauf in 11 Minuten.')
  })

  it('has no runs to report when it is off', async () => {
    server.answers = { 'GET /api/v1/procure/automation': { ...AUTO, enabled: false }, 'GET /api/v1/procure/automation/status?limit=12': status({ enabled: false, next_run_at: null }), 'GET /api/v1/procure/indexers': { indexers: [] } }
    await page.show(<AutomationPart />)
    expect(page.text).toContain('Aus. Wünsche warten auf „Jetzt suchen“.')
    expect(page.text).not.toContain('Letzter Lauf')
  })
})

describe('the reading of the newest entries', () => {
  it('tells each indexer: last read, next time', async () => {
    await show({ indexers: [{ id: 1, name: 'Bücherei 1', checked_at: '2026-10-16T11:56:00Z', next_at: '2026-10-16T12:11:00Z', error: '', resting_until: null }] })
    expect(plain(document.querySelector('[data-testid="reading-1"]')!.textContent)).toBe('Bücherei 1: zuletzt gelesen vor 4 Minuten, das nächste Mal in 11 Minuten.')
  })

  it('tells the indexer that was never read, that rests, that failed, and the one whose half of the limit is spent', async () => {
    await show({
      indexers: [
        { id: 1, name: 'Eins', checked_at: null, next_at: '2026-10-16T12:00:00Z', error: '', resting_until: null },
        { id: 2, name: 'Zwei', checked_at: '2026-10-16T11:00:00Z', next_at: '2026-10-16T12:15:00Z', error: '', resting_until: '2026-10-16T13:30:00Z' },
        { id: 3, name: 'Drei', checked_at: '2026-10-16T11:00:00Z', next_at: '2026-10-16T12:15:00Z', error: 'indexer_key_rejected', resting_until: null },
        { id: 4, name: 'Vier', checked_at: '2026-10-16T11:00:00Z', next_at: '2026-10-16T12:15:00Z', error: 'rss_share_used', resting_until: null },
      ],
    })
    const line = (id: number) => plain(document.querySelector(`[data-testid="reading-${id}"]`)!.textContent)
    expect(line(1)).toBe('Eins: noch nicht gelesen.')
    expect(line(2)).toMatch(/^Zwei: ruht bis .*\d{2}:\d{2}\.$/)
    expect(line(3)).toContain('Drei: zuletzt nicht gelungen.')
    expect(line(4)).toBe('Vier: pausiert bis morgen. Die Hälfte der Tagesgrenze ist fürs Lesen verbraucht, die andere Hälfte bleibt für Suchen.')
  })
})

describe('the limits of the day', () => {
  it('shows what was used today and what was fetched, apart', async () => {
    await show({}, [indexer(1, { used_today: 12, grabs_today: 3 }), indexer(2, { daily_limit: 0, used_today: 6, grabs_today: 1 })])
    const one = plain(document.querySelector('[data-testid="limit-1"]')!.textContent)
    expect(one).toContain('heute 12 von 100')
    expect(plain(document.querySelector('[data-testid="grabs-1"]')!.textContent)).toBe('3 Downloads heute')
    expect(plain(document.querySelector('[data-testid="grabs-2"]')!.textContent)).toBe('1 Download heute')
    expect(plain(document.querySelector('[data-testid="limit-2"]')!.textContent)).toContain('heute 6')
    expect(page.text).toContain('Die Zählung beginnt neu um')
  })

  it('offers what the indexer says of itself as the limit, only while none is set, and takes it with a click', async () => {
    server.answers['PATCH /api/v1/procure/indexers/2'] = indexer(2, { daily_limit: 250 })
    await show({}, [indexer(1, { api_max: 250 }), indexer(2, { daily_limit: 0, api_max: 250 })])
    expect(document.querySelector('[data-testid="suggest-1"]')).toBeNull()
    expect(plain(document.querySelector('[data-testid="suggest-2"]')!.textContent)).toContain('Der Indexer meldet 250 Abrufe pro Tag.')
    await page.click(document.querySelector('[data-testid="suggest-2"] button') as HTMLElement)
    expect(server.called('PATCH', '/api/v1/procure/indexers/2')!.body).toEqual({ daily_limit: 250 })
  })
})

describe('what the automatic work did last', () => {
  it('says nothing yet when nothing was done', async () => {
    await show()
    expect(page.text).toContain('Noch nichts. Sobald die Automatik einen Indexer liest oder einen Wunsch sucht, steht es hier.')
    expect(document.querySelectorAll('[data-testid="auto-log"] li')).toHaveLength(0)
  })

  it('lists the rounds with the numbers per indexer', async () => {
    await show({
      runs: [
        { id: 3, kind: 'rss', started_at: '2026-10-16T11:56:00Z', finished_at: '2026-10-16T11:56:02Z', error: '', indexers: [{ id: 1, name: 'Bücherei 1', requests: 2, entries: 40, error: '' }], wishes: 7, matched: 1, loaded: 1, handovers: 0 },
        { id: 2, kind: 'search', started_at: '2026-10-16T11:30:00Z', finished_at: '2026-10-16T11:30:20Z', error: '', indexers: [{ id: 1, name: 'Bücherei 1', requests: 5, entries: 12, error: 'indexer_daily_limit' }, { id: 9, name: '', requests: 1, entries: 0, error: '' }], wishes: 1, matched: 0, loaded: 0, handovers: 0 },
        { id: 1, kind: 'handover', started_at: '2026-10-16T11:00:00Z', finished_at: '2026-10-16T11:00:01Z', error: '', indexers: [], wishes: 0, matched: 0, loaded: 1, handovers: 1 },
      ],
    })
    const items = [...document.querySelectorAll('[data-testid="auto-log"] li')].map((item) => plain(item.textContent))
    expect(items).toHaveLength(3)
    expect(items[0]).toContain('Neue Einträge gelesen')
    expect(items[0]).toContain('40 Einträge, mit 7 Wünschen verglichen, 1 Release geladen')
    expect(items[0]).toContain('Bücherei 1: 2 Abrufe, 40 Einträge')
    expect(items[1]).toContain('Wunsch gesucht')
    expect(items[1]).toContain('1 Wunsch entschieden, 0 Releases geladen')
    expect(items[1]).toContain('Bücherei 1: 5 Abrufe, 12 Einträge, ')
    expect(items[1]).toContain('Indexer entfernt: 1 Abruf, 0 Einträge')
    expect(items[2]).toContain('Übergabe wiederholt')
    expect(items[2]).toContain('1 Übergabe, 1 Release geladen')
  })

  it('names a round that broke off', async () => {
    await show({ runs: [{ id: 1, kind: 'rss', started_at: '2026-10-16T11:56:00Z', finished_at: '2026-10-16T11:56:02Z', error: 'internal_error', indexers: [], wishes: 0, matched: 0, loaded: 0, handovers: 0 }] })
    expect(page.text).toContain('Neue Einträge gelesen')
    expect(document.querySelector('[data-run="rss"] .text-bad')).not.toBeNull()
  })
})

describe('the page looking again', () => {
  it('asks for the state of the automatic work every 30 seconds', async () => {
    const timer = vi.spyOn(window, 'setInterval')
    await show()
    const watching = timer.mock.calls.find((call) => call[1] === 30_000)
    expect(watching, 'a timer of 30 seconds').toBeTruthy()
    const asked = () => server.calls.filter((call) => call.url === '/api/v1/procure/automation/status?limit=12').length
    const before = asked()
    await act(async () => (watching![0] as () => void)())
    await idle()
    expect(asked()).toBe(before + 1)
    timer.mockRestore()
  })
})

describe('the end of the day', () => {
  it("says the time in the instance's time zone and that the background keeps a part for people", async () => {
    await show({ day_ends_at: '2026-10-16T22:00:00Z', zone: 'Pacific/Auckland' })
    expect(page.text).toContain('Die Zählung beginnt neu um 11:00 (Mitternacht der Instanz)')
    expect(page.text).toContain('höchstens 70 Prozent der Grenze')
  })

  it('says the clock time of the new day and no weekday with it', async () => {
    await show({ day_ends_at: '2026-10-16T22:00:00Z', zone: 'Europe/Berlin' })
    expect(page.text).toContain('Die Zählung beginnt neu um 00:00 (Mitternacht der Instanz)')
  })
})

describe('the page being left', () => {
  it('stops looking at the state of the automatic work', async () => {
    const clear = vi.spyOn(window, 'clearInterval')
    server.answers = { 'GET /api/v1/procure/automation': AUTO, 'GET /api/v1/procure/automation/status?limit=12': status(), 'GET /api/v1/procure/indexers': { indexers: [] } }
    const own = new Page()
    await own.show(<AutomationPart />)
    own.close()
    expect(clear).toHaveBeenCalled()
    clear.mockRestore()
  })
})
