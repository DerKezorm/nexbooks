/**
 * "Gesucht" as a list: the first look, the rows with what a wish says, the columns put in order, only the rows near the screen
 * drawn, the buttons (and the question before a wish is taken away), the reasons a search found nothing, and the look kept in
 * this browser.
 */
import { Route, Routes } from 'react-router-dom'

import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { ProcureProvider } from '../../state/procure'
import { Page, Server, plain } from '../../test/harness'
import { counts, wish } from '../../test/procure'
import { AcquirePage } from '../AcquirePage'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

function app() {
  return (
    <ProcureProvider>
      <Routes>
        <Route path="acquire" element={<AcquirePage />} />
      </Routes>
    </ProcureProvider>
  )
}

function serve(answers: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/procure/counts': counts({ seeking: 2, running: 0, needs: 0 }),
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

const rows = () => [...document.querySelectorAll('[data-testid="wanted-list"] tbody tr[data-wish]')]
const titles = () => rows().map((row) => plain(row.querySelector('.col-title .lnk')!.textContent))
const row = (id: number) => document.querySelector(`[data-testid="wanted-list"] [data-wish="${id}"]`)!

describe('Gesucht as a list', () => {
  it('is the first look, and shows a row for each wish with what it says', async () => {
    const searched = new Date(Date.now() - 2 * 3_600_000).toISOString()
    const later = new Date(Date.now() + 3 * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'not_found', searched_at: searched, next_try_at: later, detail: { total: 12, reasons: [{ code: 'other_language', count: 9 }, { code: 'bundle', count: 3 }] } }),
          wish(2, { state: 'searching', kind: 'audio', by: 'Tom', mine: false }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="wanted"]')).toBeNull()
    expect(rows()).toHaveLength(2)
    const first = plain(row(1).textContent)
    expect(first).toContain('Wunschbuch 1')
    expect(first).toContain('E-Book · Deutsch')
    expect(first).toContain('Nichts gefunden')
    expect(first).toContain('vor 2 Stunden')
    expect(first).toContain('Gewünscht von Mira')
    expect(plain(row(2).textContent)).toContain('Hörbuch · Deutsch')
    expect(plain(row(2).textContent)).toContain('Sucht gerade')
    expect([...document.querySelectorAll('thead th button')].map((item) => plain(item.textContent))).toEqual(['Buch', 'Art und Sprache', 'Zustand', 'Zuletzt gesucht', 'Nächster Versuch', 'Gewünscht von'])
    expect(document.querySelector('[data-look="list"]')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('gives each button a name that says which book it is for', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(app(), '/acquire')
    const names = ['search', 'releases', 'remove'].map((act) => {
      const button = row(1).querySelector(`[data-act="${act}"]`)!
      expect(button.getAttribute('title')).toBeTruthy()
      return button.getAttribute('aria-label')
    })
    expect(names).toEqual(['Jetzt suchen: Wunschbuch 1', 'Releases ansehen: Wunschbuch 1', 'Wunsch entfernen: Wunschbuch 1'])
  })

  it('searches now at once, and not for a book that is not out or a search that runs', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'not_found' }), wish(2, { state: 'waiting', release_date: '2027-01-01' }), wish(3, { state: 'searching' })] }, 'POST /api/v1/procure/wishes/1/search': wish(1) })
    await page.show(app(), '/acquire')
    const search = (id: number) => row(id).querySelector<HTMLButtonElement>('[data-act="search"]')!
    expect(search(2).disabled).toBe(true)
    expect(search(3).disabled).toBe(true)
    expect(search(2).getAttribute('title')).toContain('Erscheint erst am')
    await page.click(search(1))
    expect(server.changes()).toEqual(['POST /api/v1/procure/wishes/1/search'])
  })

  it('takes a wish away only after the question, and says what stays', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] }, 'DELETE /api/v1/procure/wishes/1': 204 })
    await page.show(app(), '/acquire')
    await page.click(row(1).querySelector('[data-act="remove"]'))
    expect(page.dialog!.textContent).toContain('Gefundene oder geladene Bücher bleiben, wie sie sind.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(row(1).querySelector('[data-act="remove"]'))
    await page.inDialog('Wunsch entfernen')
    expect(server.changes()).toEqual(['DELETE /api/v1/procure/wishes/1'])
  })

  it('turns a wish whose language is no longer allowed to the languages allowed', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'idle', language: 'en', detail: { reason: 'language_off', language: 'en' } })] }, 'POST /api/v1/procure/wishes/1/language': wish(1, { state: 'searching', language: '' }) })
    await page.show(app(), '/acquire')
    expect(row(1).querySelector('[data-act="search"]')).toBeNull()
    expect(row(1).querySelector('[data-act="releases"]')).toBeNull()
    await page.click(row(1).querySelector('[data-act="relanguage"]'))
    expect(server.called('POST', '/api/v1/procure/wishes/1/language')!.body).toEqual({ language: null })
  })

  it('keeps the reasons a search found nothing one press away', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'not_found', detail: { total: 12, reasons: [{ code: 'other_language', count: 9 }, { code: 'bundle', count: 3 }] } }), wish(2, { state: 'searching' })] } })
    await page.show(app(), '/acquire')
    expect(row(2).querySelector('[data-act="reasons"]')).toBeNull()
    await page.click(row(1).querySelector('[data-act="reasons"]'))
    expect(plain(page.dialog!.textContent)).toContain('12 Treffer, keiner passte')
    expect(plain(page.dialog!.querySelector('[data-testid="wanted-reasons"]')!.textContent)).toBe('9 × andere Sprache3 × Sammelpaket')
  })

  it('shows all wishes or only the own ones', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1), wish(2, { mine: false, by: 'Tom' })] } })
    await page.show(app(), '/acquire')
    expect(rows()).toHaveLength(2)
    await page.click(document.querySelector('[data-mine="1"]'))
    expect(rows()).toHaveLength(1)
  })

  it('puts the rows in order by a column and turns the order round, an empty column last either way', async () => {
    const day = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'not_found', next_try_at: day(3), book: { ...wish(1).book, title: 'Zebra' } }),
          wish(2, { state: 'not_found', next_try_at: null, book: { ...wish(2).book, title: 'Anker' } }),
          wish(3, { state: 'not_found', next_try_at: day(1), book: { ...wish(3).book, title: 'Mond' } }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    expect(titles()).toEqual(['Zebra', 'Anker', 'Mond'])
    const head = (name: string) => document.querySelector(`thead [data-sort="${name}"]`)!
    await page.click(head('title'))
    expect(titles()).toEqual(['Anker', 'Mond', 'Zebra'])
    expect(head('title').closest('th')!.getAttribute('aria-sort')).toBe('ascending')
    await page.click(head('title'))
    expect(titles()).toEqual(['Zebra', 'Mond', 'Anker'])
    await page.click(head('next'))
    expect(titles()).toEqual(['Mond', 'Zebra', 'Anker'])
    await page.click(head('next'))
    expect(titles()).toEqual(['Zebra', 'Mond', 'Anker'])
  })

  it('starts the column of the last search with the newest', async () => {
    const hours = (count: number) => new Date(Date.now() - count * 3_600_000).toISOString()
    serve({
      'GET /api/v1/procure/wishes': {
        wishes: [
          wish(1, { state: 'not_found', searched_at: hours(9), book: { ...wish(1).book, title: 'Alt' } }),
          wish(2, { state: 'not_found', searched_at: hours(1), book: { ...wish(2).book, title: 'Neu' } }),
        ],
      },
    })
    await page.show(app(), '/acquire')
    await page.click(document.querySelector('thead [data-sort="last"]'))
    expect(titles()).toEqual(['Neu', 'Alt'])
  })

  it('draws only the rows near the screen, however many wishes there are', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: Array.from({ length: 500 }, (_, index) => wish(index + 1, { state: 'not_found' })) } })
    await page.show(app(), '/acquire')
    expect(rows().length).toBeGreaterThan(5)
    expect(rows().length).toBeLessThan(40)
    expect(document.querySelector('[data-testid="wanted-list"] table')!.getAttribute('aria-rowcount')).toBe('501')
    expect(document.querySelector('tr.pad')).not.toBeNull()
  })
})

describe('Gesucht, the look', () => {
  it('shows the cards when the person chooses them, and keeps the choice in this browser', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(app(), '/acquire')
    await page.click(document.querySelector('[data-look="cards"]'))
    expect(document.querySelector('[data-testid="wanted"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="wanted-list"]')).toBeNull()
    expect(window.localStorage.getItem('nexbooks.acquire.wanted')).toBe('cards')
    page.close()
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="wanted"]')).not.toBeNull()
    await page.click(document.querySelector('[data-look="list"]'))
    expect(document.querySelector('[data-testid="wanted-list"]')).not.toBeNull()
    expect(window.localStorage.getItem('nexbooks.acquire.wanted')).toBe('list')
  })

  it('works in a browser that keeps nothing', async () => {
    const broken = { getItem: () => { throw new Error('no storage') }, setItem: () => { throw new Error('no storage') }, removeItem: () => undefined, clear: () => undefined }
    vi.stubGlobal('localStorage', broken)
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(app(), '/acquire')
    expect(document.querySelector('[data-testid="wanted-list"]')).not.toBeNull()
    await page.click(document.querySelector('[data-look="cards"]'))
    expect(document.querySelector('[data-testid="wanted"]')).not.toBeNull()
  })

  it('keeps the same buttons in both looks', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { state: 'not_found' })] } })
    await page.show(app(), '/acquire')
    const acts = () => [...document.querySelectorAll('[data-wish="1"] [data-act]')].map((item) => item.getAttribute('data-act'))
    expect(acts()).toEqual(['search', 'releases', 'remove'])
    await page.click(document.querySelector('[data-look="cards"]'))
    expect(acts()).toEqual(['search', 'releases', 'remove'])
  })
})
