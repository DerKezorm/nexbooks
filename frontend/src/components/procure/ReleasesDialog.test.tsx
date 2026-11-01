/**
 * "Releases ansehen": the progress per indexer, a card per kind, the table with rank, format, language and
 * reasons, loading at once what passes and only after a question what was turned down; blocking and unblocking ask
 * first, and nothing is sent before the answer.
 */
import { act } from 'react'
import type { IndexerProgress, ReleaseCard, ReleaseOut, ReleaseSearch } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server, plain } from '../../test/harness'
import { until } from '../../test/wait'
import { ReleasesDialog } from './ReleasesDialog'

const server = new Server()
const page = new Page()

function release(id: number, over: Partial<ReleaseOut> = {}): ReleaseOut {
  return {
    id, title: `Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EX${id}`, indexer: 'Bücherei', protocol: 'usenet', kind: 'ebook', format: 'epub',
    format_how: 'name', tier: 0, language: 'de', language_sure: true, language_how: 'mark', variant: '', bitrate: null, size: 2_300_000,
    age_days: 3, seeders: null, peers: null, grabs: 41, score: 92, rank: 1, reasons: [], blocked_by: null, ...over,
  }
}

const indexer = (over: Partial<IndexerProgress> = {}): IndexerProgress => ({ id: 1, name: 'Bücherei', kind: 'newznab', state: 'done', found: 3, error: '', until: null, limit: null, ...over })

function card(kind: 'ebook' | 'audio', over: Partial<ReleaseCard> = {}): ReleaseCard {
  return { kind, total: 3, passing: 1, reasons: [{ code: 'other_language', count: 1 }, { code: 'format_not_wanted', count: 1 }], top: release(1), goal: 'epub', below_goal: false, have: null, ...over }
}

function search(over: Partial<ReleaseSearch> = {}): ReleaseSearch {
  return {
    id: 7, state: 'done', error: '', language: 'de', languages: ['de'], kinds: ['ebook', 'audio'], work_id: 3, book: null, title: 'Die Laternenstraße', author: 'Rune Falkner',
    created_at: '2026-10-15T10:00:00Z', finished_at: '2026-10-15T10:00:09Z', indexers: [indexer()], resting: [], can_search_again: true, searches_used_until: null,
    cards: [card('ebook'), card('audio', { total: 2, passing: 0, top: null, reasons: [{ code: 'abridged', count: 2 }], goal: 'flac' })],
    releases: [
      release(1),
      release(2, { title: 'Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-EX2', language: 'fr', rank: null, score: 0, reasons: [{ code: 'other_language', arg: 'fr' }] }),
      release(3, { title: 'Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-EX3', format: 'pdf', tier: 3, rank: null, reasons: [{ code: 'format_not_wanted', arg: 'pdf' }], blocked_by: null }),
      release(4, { title: 'Rune Falkner-Die Laternenstrasse (Gekuerzt)-MP3-DE-EX4', kind: 'audio', format: 'mp3_128', tier: 4, rank: null, reasons: [{ code: 'abridged' }] }),
    ],
    ...over,
  }
}

const START = 'POST /api/v1/procure/searches'
const READ = 'GET /api/v1/procure/searches/7'

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { [START]: search({ state: 'queued', cards: [], releases: [], indexers: [indexer({ state: 'waiting', found: 0 })] }), [READ]: search() }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const rows = () => [...document.querySelectorAll<HTMLTableRowElement>('[data-testid="release-row"]')]
const said: string[] = []

async function open(kind: 'ebook' | 'audio' = 'ebook') {
  said.length = 0
  const closed = { count: 0 }
  await page.show(<ReleasesDialog workId={3} title="Die Laternenstraße" author="Rune Falkner" kind={kind} onClose={() => (closed.count += 1)} onLoaded={(text) => said.push(text)} />)
  return closed
}

describe('the search', () => {
  it('looks in the language of the wish it was opened from, never in the language of the page', async () => {
    await page.show(<ReleasesDialog workId={3} title="Die Laternenstraße" author="Rune Falkner" kind="ebook" language="en" onClose={() => undefined} onLoaded={() => undefined} />)
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ work_id: 3, language: 'en' })
  })

  it('starts for the work in the languages allowed and shows each indexer while it asks', async () => {
    server.answers[READ] = search({ state: 'running', cards: [], releases: [], indexers: [indexer({ state: 'searching' }), indexer({ id: 2, name: 'Lesesaal', kind: 'torznab', state: 'failed', error: 'indexer_limit_reached', until: '2026-10-15T23:00:00Z' })] })
    await open()
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ work_id: 3 })
    expect(plain(document.querySelector('[role="dialog"] h2')!.textContent)).toBe('Releases ansehen')
    expect(page.text).toContain('Die Laternenstraße · Rune Falkner')
    await until(() => document.querySelectorAll('[data-testid="indexer-row"]').length === 2)
    const lines = [...document.querySelectorAll('[data-testid="indexer-row"]')].map((row) => plain(row.textContent))
    expect(lines[0]).toBe('BüchereiNewznabfragt …')
    expect(lines[1]).toMatch(/^LesesaalTorznabpausiert bis \d\d:\d\d$/)
    expect(page.text).toContain('Suche läuft')
  })

  it('asks again until the search is done', async () => {
    server.answers[READ] = search({ state: 'running', cards: [], releases: [], indexers: [indexer({ state: 'searching' })] })
    await open()
    await until(() => document.querySelector('[data-testid="indexer-row"][data-state="searching"]'))
    expect(rows()).toHaveLength(0)
    server.answers[READ] = search()
    await until(() => rows().length > 0)
    expect(page.text).toContain('Suche beendet')
  })

  it('asks the indexers again only when asked to, and says when it searched', async () => {
    await open()
    await until(() => rows().length > 0)
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ work_id: 3 })
    expect(page.text).toMatch(/Gesucht um \d\d:\d\d\./)
    server.calls.length = 0
    await page.click(document.querySelector('[data-testid="search-again"]') as HTMLElement)
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ work_id: 3, new: true })
    await until(() => rows().length > 0)
  })

  it('takes the old results away while the new search is being made', async () => {
    await open()
    await until(() => rows().length > 0)
    const real = globalThis.fetch
    let go!: () => void
    const gate = new Promise<void>((resolve) => (go = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'POST') await gate
        return real(url, init)
      }),
    )
    await act(async () => (document.querySelector('[data-testid="search-again"]') as HTMLElement).click())
    expect(rows()).toHaveLength(0)
    expect(document.querySelector('[data-testid="search-again"]')).toBeNull()
    go()
    await until(() => rows().length > 0)
  })

  it('shows a search that failed and offers to search again', async () => {
    server.answers[START] = search({ state: 'failed', error: 'search_failed', cards: [], releases: [], indexers: [indexer({ state: 'waiting', found: 0 })] })
    await open()
    await until(() => document.querySelector('[data-testid="search-failed"]'))
    expect(plain(document.querySelector('[data-testid="search-failed"]')!.textContent)).toBe('Die Suche ist fehlgeschlagen.')
    expect(rows()).toHaveLength(0)
    server.answers[START] = search()
    await page.click(document.querySelector('[data-testid="search-again"]') as HTMLElement)
    expect(server.calls.filter((call) => call.method === 'POST')[1].body).toEqual({ work_id: 3, new: true })
    await until(() => rows().length > 0)
    expect(document.querySelector('[data-testid="search-failed"]')).toBeNull()
  })

  it('offers no new search while one is running', async () => {
    server.answers[READ] = search({ state: 'running', cards: [], releases: [], indexers: [indexer({ state: 'searching' })] })
    await open()
    await until(() => document.querySelector('[data-testid="indexer-row"]'))
    expect(document.querySelector('[data-testid="search-again"]')).toBeNull()
  })

  it('says when no indexer is set up', async () => {
    server.fails[START] = { status: 409, code: 'no_indexers' }
    await open()
    expect(plain(document.querySelector('[data-testid="release-problem"]')!.textContent)).toBe('Noch kein Indexer eingerichtet. Indexer richtet der Betreiber in den Einstellungen unter Beschaffung ein.')
  })
})

describe('a search that could not ask because of a limit', () => {
  const tomorrow = new Date(Date.now() + 5 * 3_600_000).toISOString()
  const hour = (iso: string) => new Date(iso).toLocaleTimeString('de', { hour: '2-digit', minute: '2-digit' })
  const atLimit = (over: Partial<IndexerProgress> = {}) => indexer({ state: 'failed', found: 0, error: 'indexer_daily_limit', until: tomorrow, limit: 100, ...over })
  const resting = { id: 1, name: 'Archivbund', cause: 'daily_limit' as const, until: tomorrow, limit: 100 }

  it('says why nothing was searched, in place of "Kein Release gefunden", and offers no new search', async () => {
    server.answers[READ] = search({ indexers: [atLimit({ name: 'Archivbund' })], cards: [card('ebook', { total: 0, passing: 0, top: null, reasons: [] })], releases: [], resting: [resting], can_search_again: false })
    await open()
    await until(() => document.querySelector('[data-testid="limit-note"]'))
    const note = plain(document.querySelector('[data-testid="limit-note"]')!.textContent)
    expect(note).toContain('Es wurde nicht gesucht.')
    expect(note).toContain(`Archivbund hat sein Tageslimit in nexbooks erreicht (100 Suchen). Neue Suche ab`)
    expect(note).toContain(hour(tomorrow))
    expect(page.text).not.toContain('Kein Release gefunden')
    expect(page.text).not.toContain('0 von 0 passen')
    expect(document.querySelector('[data-testid="release-results"]')).toBeNull()
    const again = document.querySelector<HTMLButtonElement>('[data-testid="search-again"]')!
    expect(again.disabled).toBe(true)
    expect(again.title).toBe('Gerade nicht möglich, die Gründe stehen oben.')
  })

  it('names the cause when it was read from the progress of the search alone', async () => {
    server.answers[READ] = search({ indexers: [atLimit({ error: 'indexer_share_used', limit: 100, name: 'Archivbund' })], cards: [], releases: [] })
    await open()
    await until(() => document.querySelector('[data-testid="limit-note"]'))
    expect(plain(document.querySelector('[data-testid="limit-note"]')!.textContent)).toContain('Dein Anteil am Tageslimit von Archivbund ist aufgebraucht (100 Suchen am Tag für alle).')
    expect(document.querySelector<HTMLButtonElement>('[data-testid="search-again"]')!.disabled).toBe(false)
  })

  it.each([
    ['indexer_limit_reached', 'Bücherei hat sein eigenes Limit erreicht und pausiert.'],
    ['indexer_key_rejected', 'Bücherei lehnt den API-Schlüssel ab.'],
    ['indexer_unreachable', 'Bücherei antwortet nicht und pausiert für kurze Zeit.'],
  ])('says in words what %s means', async (error, sentence) => {
    server.answers[READ] = search({ indexers: [atLimit({ error, limit: null })], cards: [], releases: [] })
    await open()
    await until(() => document.querySelector('[data-testid="limit-note"]'))
    expect(plain(document.querySelector('[data-testid="limit-note"]')!.textContent)).toContain(sentence)
  })

  it('shows the hits of the earlier search under the note when no indexer can be asked, and still lets a release be loaded', async () => {
    server.answers[READ] = search({ resting: [resting], can_search_again: false })
    await open()
    await until(() => rows().length > 0)
    const note = plain(document.querySelector('[data-testid="limit-note"]')!.textContent)
    expect(note).toContain('Neu suchen geht gerade nicht. Das sind die Treffer der Suche von')
    expect(note).toContain('Archivbund hat sein Tageslimit in nexbooks erreicht (100 Suchen).')
    expect(document.querySelector('[data-testid="release-results"]')).not.toBeNull()
    expect(page.text).toMatch(/Letzte Suche: .*\d\d:\d\d\./)
    expect(document.querySelector<HTMLButtonElement>('[data-testid="search-again"]')!.disabled).toBe(true)
    server.answers['POST /api/v1/procure/searches/7/releases/1/load'] = { id: 1, title: 'x', kind: 'ebook', state: 'queued', client: 'Programm', protocol: 'usenet', forced: [], created_at: '2026-10-15T10:01:00Z' }
    await page.click(rows()[0])
    await page.click(document.querySelector('[data-testid="take"]'))
    expect(server.called('POST', '/api/v1/procure/searches/7/releases/1/load')).toBeDefined()
  })

  it('says it when the person has made the searches of the day, and shows the earlier hits', async () => {
    server.answers[READ] = search({ can_search_again: false, searches_used_until: tomorrow })
    await open()
    await until(() => rows().length > 0)
    expect(plain(document.querySelector('[data-testid="limit-note"]')!.textContent)).toContain('Du hast heute so viele Suchen gemacht, wie dir erlaubt sind. Neue Suche ab')
    expect(document.querySelector<HTMLButtonElement>('[data-testid="search-again"]')!.disabled).toBe(true)
  })

  it('says nothing of the kind while a search can be made, and does not block "Neu suchen"', async () => {
    await open()
    await until(() => rows().length > 0)
    expect(document.querySelector('[data-testid="limit-note"]')).toBeNull()
    expect(document.querySelector<HTMLButtonElement>('[data-testid="search-again"]')!.disabled).toBe(false)
  })

  it('does not call it "not searched" when another indexer did search and found nothing', async () => {
    server.answers[READ] = search({ indexers: [indexer({ found: 0 }), atLimit({ id: 2, name: 'Archivbund' })], cards: [card('ebook', { total: 0, passing: 0, top: null, reasons: [] })], releases: [] })
    await open()
    await until(() => document.querySelector('[data-testid="release-results"]'))
    expect(document.querySelector('[data-testid="limit-note"]')).toBeNull()
    expect(page.text).toContain('Kein Release gefunden')
  })
})

describe('the results', () => {
  it('show a card per kind and the table of the chosen kind, the best first', async () => {
    await open()
    await until(() => rows().length > 0)
    const cards = [...document.querySelectorAll<HTMLButtonElement>('[data-testid="release-card"]')]
    expect(cards.map((item) => item.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    expect(plain(cards[0].textContent)).toContain('würde nehmen')
    expect(plain(cards[0].textContent)).toContain('01 EPUB · 2,3 MB · Bücherei · Sprache Deutsch (erkannt)')
    expect(plain(cards[0].textContent)).toContain('1 von 3 passen1 andere Sprache1 Format nicht erlaubt')
    expect(plain(cards[1].textContent)).toContain('nichts passt')
    expect(plain(cards[1].textContent)).toContain('Keins der 2 Releases passt zu deinen Formaten und Fassungen.')
    expect(page.text).toContain('Alle Releases · E-Book')
    expect(rows()).toHaveLength(3)
    const first = rows()[0]
    expect(first.classList.contains('best')).toBe(true)
    expect([...first.querySelectorAll('td')].map((cell) => plain(cell.textContent))).toEqual([
      '1', 'Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EX1Bücherei · EPUB · 2,3 MB', 'BüchereiUsenet', '01 EPUB', 'Deutscherkannt', '2,3 MB', '3 T', '41', '92', 'würde nehmen',
    ])
    expect(first.querySelector('.rel')!.getAttribute('dir')).toBe('auto')
    expect(plain(rows()[1].querySelector('td')!.textContent)).toBe('·')
    await page.click(cards[1])
    expect(page.text).toContain('Alle Releases · Hörbuch')
    expect(rows()).toHaveLength(1)
  })

  it('say whether the language was detected or is presumed', async () => {
    server.answers[READ] = search({ releases: [release(1), release(9, { rank: 2, language_sure: false, language_how: 'presumed' })] })
    await open()
    await until(() => rows().length > 1)
    const languages = rows().map((row) => plain(row.querySelectorAll('td')[4].textContent))
    expect(languages).toEqual(['Deutscherkannt', 'Deutschvermutet'])
  })

  it('open a row to its reasons as sentences', async () => {
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[1])
    const why = document.querySelector('[data-testid="why-row"]')!
    expect(plain(why.querySelector('li')!.textContent)).toBe('Die Sprache ist Französisch, gewünscht ist Deutsch.')
    expect(rows()[1].getAttribute('aria-expanded')).toBe('true')
    await page.click(rows()[1])
    expect(document.querySelector('[data-testid="why-row"]')).toBeNull()
  })

  it('a below-goal note names the goal', async () => {
    server.answers[READ] = search({ cards: [card('ebook', { top: release(5, { format: 'mobi', tier: 2 }), below_goal: true })] })
    await open()
    await until(() => rows().length > 0)
    expect(page.text).toContain('Unter dem Ziel EPUB.')
  })
})

describe('loading', () => {
  it('loads a release that passes at once and says so', async () => {
    server.answers['POST /api/v1/procure/searches/7/releases/1/load'] = { id: 1, title: 'x', kind: 'ebook', state: 'sent', client: 'SABnzbd', protocol: 'usenet', forced: [], created_at: '2026-10-15T10:01:00Z' }
    const closed = await open()
    await until(() => rows().length > 0)
    await page.click(rows()[0])
    await page.click(page.button('Dieses Release laden'))
    expect(server.called('POST', '/api/v1/procure/searches/7/releases/1/load')!.body).toEqual({ force: false })
    expect(said).toEqual(['Lädt jetzt: Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EX1'])
    expect(closed.count).toBe(1)
  })

  it('asks before it loads what was turned down, and sends nothing without the answer', async () => {
    server.answers['POST /api/v1/procure/searches/7/releases/2/load'] = { id: 2, title: 'x', kind: 'ebook', state: 'sent', client: 'SABnzbd', protocol: 'usenet', forced: ['other_language'], created_at: '2026-10-15T10:01:00Z' }
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[1])
    await page.click(page.button('Trotzdem laden'))
    expect(page.dialog!.textContent).toContain('nexbooks hat dieses Release abgelehnt: Die Sprache ist Französisch, gewünscht ist Deutsch. Es wird nur dieses eine Mal geladen und trotzdem noch geprüft.')
    expect(server.changes()).toEqual(['POST /api/v1/procure/searches'])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual(['POST /api/v1/procure/searches'])
    await page.click(page.button('Trotzdem laden'))
    await page.inDialog('Trotzdem laden')
    expect(server.called('POST', '/api/v1/procure/searches/7/releases/2/load')!.body).toEqual({ force: true })
  })

  it('says why a release could not be loaded and stays open', async () => {
    server.fails['POST /api/v1/procure/searches/7/releases/1/load'] = { status: 409, code: 'no_client', values: { protocol: 'usenet' } }
    const closed = await open()
    await until(() => rows().length > 0)
    await page.click(rows()[0])
    await page.click(page.button('Dieses Release laden'))
    expect(plain(document.querySelector('[data-testid="release-note"]')!.textContent)).toBe('Für diese Art von Release ist kein Download-Programm eingeschaltet.')
    expect(closed.count).toBe(0)
  })

  it('a release below the first loads "statt des ersten", and only the first is the one that would be taken', async () => {
    server.answers[READ] = search({ releases: [release(1), release(6, { rank: 2, score: 80 })] })
    await open()
    await until(() => rows().length > 1)
    const results = rows().map((row) => plain([...row.querySelectorAll('td')].at(-1)!.textContent))
    expect(results).toEqual(['würde nehmen', 'passt'])
    await page.click(rows()[1])
    expect(page.button('Dieses statt des ersten laden')).toBeTruthy()
  })

  it('does not send a second request while the first is on its way', async () => {
    server.answers['POST /api/v1/procure/searches/7/releases/1/load'] = { id: 1, title: 'x', kind: 'ebook', state: 'sent', client: 'SABnzbd', protocol: 'usenet', forced: [], created_at: '2026-10-15T10:01:00Z' }
    await open()
    await until(() => rows().length > 0)
    const real = globalThis.fetch
    let open_!: () => void
    const gate = new Promise<void>((resolve) => (open_ = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'POST' && String(url).endsWith('/load')) await gate
        return real(url, init)
      }),
    )
    await page.click(rows()[0])
    const take = page.button('Dieses Release laden')!
    // Not through page.click: that waits for every request of the page to end, and this one is held back.
    await act(async () => take.click())
    expect(take.disabled).toBe(true)
    await act(async () => take.click())
    open_()
    await until(() => said.length > 0)
    expect(server.calls.filter((call) => call.url.endsWith('/load'))).toHaveLength(1)
  })
})

describe('the reasons in words', () => {
  it('say whether an abridged version is turned down by the settings or by an unabridged one that fits', async () => {
    server.answers[READ] = search({
      releases: [
        release(1),
        release(10, { kind: 'ebook', rank: null, reasons: [{ code: 'abridged', arg: 'settings' }] }),
        release(11, { kind: 'ebook', rank: null, reasons: [{ code: 'abridged', arg: 'alternative' }, { code: 'not_a_book' }] }),
      ],
    })
    await open()
    await until(() => rows().length > 2)
    await page.click(rows()[1])
    expect(plain(document.querySelector('[data-testid="why-row"] li')!.textContent)).toBe('Gekürzte Fassung, die Einstellungen nehmen hier keine gekürzten.')
    await page.click(rows()[2])
    const sentences = [...document.querySelectorAll('[data-testid="why-row"] li')].map((item) => plain(item.textContent))
    expect(sentences).toContain('Gekürzte Fassung. Es gibt ein ungekürztes Release, das passt und vorgeht.')
    expect(sentences).toContain('Der Name ist der einer Datei, die kein Buch ist, etwa eine .nfo.')
  })

  it('name every language that is taken when no language was asked for', async () => {
    server.answers[READ] = search({ language: '', languages: ['de', 'en'] })
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[1])
    expect(plain(document.querySelector('[data-testid="why-row"] li')!.textContent)).toBe('Die Sprache ist Französisch, gewünscht ist Deutsch / Englisch.')
    await page.click(page.button('Trotzdem laden'))
    expect(page.dialog!.textContent).toContain('gewünscht ist Deutsch / Englisch.')
  })
})

describe('the blocklist', () => {
  it('blocks a release only after a question that says it can be taken back', async () => {
    server.answers['POST /api/v1/procure/searches/7/releases/1/block'] = { id: 9 }
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[0])
    await page.click(page.button('Sperren'))
    expect(page.dialog!.textContent).toContain('Entsperren kannst du es hier jederzeit wieder.')
    expect(server.changes()).toEqual(['POST /api/v1/procure/searches'])
    await page.inDialog('Sperren')
    expect(server.called('POST', '/api/v1/procure/searches/7/releases/1/block')!.body).toEqual({ reason: 'by_hand' })
    expect(plain(document.querySelector('[data-testid="release-note"]')!.textContent)).toBe('Gesperrt.')
  })

  it('unblocks a blocked release only after a question', async () => {
    server.answers[READ] = search({ releases: [release(8, { rank: null, reasons: [{ code: 'blocklisted' }], blocked_by: 31 })] })
    server.answers['DELETE /api/v1/procure/blocklist/31'] = 204
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[0])
    await page.click(page.button('Entsperren'))
    expect(page.dialog!.textContent).toContain('Sperren kannst du es hier jederzeit wieder.')
    expect(server.changes()).toEqual(['POST /api/v1/procure/searches'])
    await page.inDialog('Entsperren')
    expect(server.changes()).toContain('DELETE /api/v1/procure/blocklist/31')
  })
})

describe('a block of another library', () => {
  it('turns the release down here too, and offers neither to lift it nor to block it again', async () => {
    server.answers[READ] = search({ releases: [release(8, { rank: null, reasons: [{ code: 'blocklisted' }], blocked_by: null })] })
    await open()
    await until(() => rows().length > 0)
    await page.click(rows()[0])
    expect(page.text).toContain('Dieses Release steht auf der Sperrliste.')
    expect(document.querySelector('[data-testid="unblock"]')).toBeNull()
    expect(document.querySelector('[data-testid="block"]')).toBeNull()
  })
})

describe('in English', () => {
  it('words the table and the reasons', async () => {
    await changeLanguage('en', false)
    await open()
    await until(() => rows().length > 0)
    expect(page.text).toContain('All releases · E-book')
    await page.click(rows()[2])
    expect(page.text).toContain('PDF is not ticked in the ranking.')
  })
})
