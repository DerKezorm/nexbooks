/**
 * Settings, Sources: the main switch, a card for each source with how it fares, Hardcover's token (pasted in, never shown
 * again), the markets of Audible and "match everything again". What deletes or costs asks first.
 */
import type { SourceInfo, SourcesOut } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server, plain } from '../../test/harness'
import { SourcesPart } from './SourcesCards'

vi.mock('../../state/auth', () => ({ useAuth: () => ({ me: { profile: { timezone: 'Europe/Berlin' } }, status: 'signedIn' }) }))

const server = new Server()
const page = new Page()

const SECRET = 'tok-' + 'x'.repeat(30)

function info(id: SourceInfo['id'], over: Partial<SourceInfo> = {}): SourceInfo {
  return { id, enabled: true, state: 'ready', requests_today: 12, requests_per_day: 4000, last_ok_at: null, last_error: '', last_error_at: null, resting_until: null, waiting: 0, ...over }
}
function state(over: Partial<SourcesOut> = {}): SourcesOut {
  return {
    enabled: true, markets: ['de', 'us'], available_markets: ['de', 'us', 'uk', 'fr', 'ca', 'au', 'it'], hardcover_connected: false, audible_decided: true, author_info: true, waiting: 0, to_look_at: 0,
    sources: [info('hardcover', { enabled: true, state: 'not_connected', requests_per_day: 5000 }), info('openlibrary'), info('audible'), info('dnb', { enabled: false, state: 'off' })],
    ...over,
  }
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'GET /api/v1/sources': state() }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const sourceState = (id: string) => plain(document.querySelector(`[data-testid="source-state-${id}"]`)!.textContent)

describe('the cards', () => {
  it('say for each source whether it is ready, off or not connected, with the requests of today', async () => {
    await page.show(<SourcesPart />)
    expect(sourceState('openlibrary')).toBe('Bereit · Heute 12 von 4000 Anfragen')
    expect(sourceState('audible')).toBe('Bereit · Heute 12 von 4000 Anfragen')
    expect(sourceState('hardcover')).toBe('Nicht verbunden · Heute 12 von 5000 Anfragen')
    expect(sourceState('dnb')).toBe('Aus · Heute 12 von 4000 Anfragen')
    expect(page.text).toContain('Deutsche Nationalbibliothek')
  })

  it('have a card for Apple Books Charts, which only Discover reads, and switch it like the others', async () => {
    server.answers['GET /api/v1/sources'] = state({ sources: [...state().sources, info('apple', { requests_per_day: 2000 })] })
    server.answers['PUT /api/v1/sources'] = state({ sources: [...state().sources, info('apple', { enabled: false, state: 'off', requests_per_day: 2000 })] })
    await page.show(<SourcesPart />)
    expect(sourceState('apple')).toBe('Bereit · Heute 12 von 2000 Anfragen')
    expect(page.text).toContain('Apple Books Charts')
    expect(page.text).toContain('Nur für Entdecken')
    await page.click(document.querySelector('[role="switch"][aria-label="Apple Books Charts fragen"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ sources: { apple: false } })
  })

  it('have a card for Wikidata and Wikimedia Commons, which gives the photos of authors, and one for the photos', async () => {
    const sources = [info('hardcover'), info('openlibrary'), info('audible'), info('dnb'), info('wikidata', { requests_per_day: 3000 })]
    server.answers['GET /api/v1/sources'] = state({ sources })
    server.answers['PUT /api/v1/sources'] = state({ sources, author_info: false })
    await page.show(<SourcesPart />)
    expect(sourceState('wikidata')).toBe('Bereit · Heute 12 von 3000 Anfragen')
    expect(page.text).toContain('Wikidata und Wikimedia Commons')
    expect(page.text).toContain('mit einem freien Foto samt Urheber und Lizenz')
    const switchOf = document.querySelector('#sources-authors [role="switch"]') as HTMLElement
    expect(switchOf.getAttribute('aria-checked')).toBe('true')
    await page.click(switchOf)
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ author_info: false })
    await page.click(document.querySelector('[role="switch"][aria-label="Wikidata und Wikimedia Commons fragen"]') as HTMLElement)
    expect(server.calls.filter((c) => c.method === 'PUT').at(-1)!.body).toEqual({ sources: { wikidata: false } })
  })

  it('say in one sentence what goes out', async () => {
    await page.show(<SourcesPart />)
    expect(plain(document.querySelector('[data-testid="sources-privacy"]')!.textContent)).toBe('Geschickt werden nur Titel, Autor und ISBN. Nie Dateien, Ordner, Bibliotheken oder Namen von Personen.')
  })

  it('show the last error with its words and when it was', async () => {
    server.answers['GET /api/v1/sources'] = state({ sources: [info('hardcover'), info('openlibrary', { state: 'resting', last_error: 'rate_limited', last_error_at: new Date(Date.now() - 300_000).toISOString() }), info('audible'), info('dnb')] })
    await page.show(<SourcesPart />)
    expect(sourceState('openlibrary')).toContain('Ruht kurz nach Fehlern')
    expect(plain(document.querySelector('[data-testid="source-error-openlibrary"]')!.textContent)).toBe('Letzter Fehler: zu viele Anfragen, vor 5 Minuten')
  })

  it('say that a source is at its daily limit and until when, not that it rests a short while', async () => {
    server.answers['GET /api/v1/sources'] = state({ sources: [info('hardcover'), info('openlibrary'), info('audible'), info('dnb', { state: 'resting', requests_today: 3000, requests_per_day: 3000, last_error: 'daily_limit', last_error_at: new Date(Date.now() - 60_000).toISOString() })] })
    await page.show(<SourcesPart />)
    expect(sourceState('dnb')).toContain('Tageslimit erreicht, ruht bis Mitternacht')
    expect(sourceState('dnb')).not.toContain('kurz')
    expect(plain(document.querySelector('[data-testid="source-error-dnb"]')!.textContent)).toBe('Letzter Fehler: Tageslimit erreicht, morgen wieder, vor 1 Minute')
  })

  it('say for each source how many books wait for it, and until when a resting source rests', async () => {
    server.answers['GET /api/v1/sources'] = state({
      waiting: 5,
      sources: [info('hardcover'), info('openlibrary'), info('audible', { waiting: 4 }), info('dnb', { state: 'resting', waiting: 1, resting_until: '2026-10-14T22:00:00Z' })],
    })
    await page.show(<SourcesPart />)
    expect(document.querySelector('[data-testid="source-waiting-audible"]')!.textContent!.trim()).toBe('· wartend: 4')
    expect(document.querySelector('[data-testid="source-waiting-dnb"]')!.textContent!.trim()).toBe('· wartend: 1')
    expect(document.querySelector('[data-testid="source-waiting-openlibrary"]')).toBeNull()
    expect(document.querySelector('[data-testid="source-until-dnb"]')!.textContent!.trim()).toMatch(/^· ruht bis \d\d:\d\d$/)
    expect(document.querySelector('[data-testid="source-until-audible"]')).toBeNull()
  })

  it('count the books that wait and those that need a look', async () => {
    server.answers['GET /api/v1/sources'] = state({ waiting: 1, to_look_at: 3 })
    await page.show(<SourcesPart />)
    expect(plain(document.querySelector('[data-testid="sources-numbers"]')!.textContent)).toBe('1 Buch wartet auf den Abgleich. · 3 Bücher brauchen einen Blick.')
  })
})

describe('the switches', () => {
  it('turn the main switch off with one request and leave the rest alone', async () => {
    server.answers['PUT /api/v1/sources'] = state({ enabled: false })
    await page.show(<SourcesPart />)
    await page.click(document.querySelector('[role="switch"][aria-label="nexbooks darf Quellen im Internet fragen"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ enabled: false })
  })

  it('turn one source on or off by its name', async () => {
    server.answers['PUT /api/v1/sources'] = state()
    await page.show(<SourcesPart />)
    await page.click(document.querySelector('[role="switch"][aria-label="Deutsche Nationalbibliothek fragen"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ sources: { dnb: true } })
  })
})

describe('the markets', () => {
  it('are chosen from the markets there are, and sent as a whole list', async () => {
    server.answers['PUT /api/v1/sources'] = state({ markets: ['de', 'us', 'uk'] })
    await page.show(<SourcesPart />)
    const group = document.querySelector('#sources-markets [role="group"]') as HTMLElement
    expect([...group.querySelectorAll('button')].map((button) => [plain(button.textContent), button.getAttribute('aria-pressed')])).toEqual([
      ['Deutschland', 'true'], ['USA', 'true'], ['Großbritannien', 'false'], ['Frankreich', 'false'], ['Kanada', 'false'], ['Australien', 'false'], ['Italien', 'false'],
    ])
    await page.click(page.button('Großbritannien', group))
    expect(server.called('PUT', '/api/v1/sources')!.body).toEqual({ markets: ['de', 'us', 'uk'] })
  })

  it('never fewer than one, and never more than six', async () => {
    server.answers['GET /api/v1/sources'] = state({ markets: ['de'] })
    await page.show(<SourcesPart />)
    expect(page.button('Deutschland')!.disabled).toBe(true)
    page.close()
    server.answers['GET /api/v1/sources'] = state({ markets: ['de', 'us', 'uk', 'fr', 'ca', 'au'], available_markets: ['de', 'us', 'uk', 'fr', 'ca', 'au', 'it'] })
    await page.show(<SourcesPart />)
    expect(page.button('Italien')!.disabled).toBe(true)
    expect(page.button('Kanada')!.disabled).toBe(false)
  })
})

describe('the token of Hardcover', () => {
  it('is a password field, goes out once, and is gone from the page afterwards', async () => {
    server.answers['PUT /api/v1/sources/hardcover/token'] = state({ hardcover_connected: true, sources: [info('hardcover'), info('openlibrary'), info('audible'), info('dnb')] })
    await page.show(<SourcesPart />)
    const field = document.querySelector('#source-hardcover input') as HTMLInputElement
    expect(field.type).toBe('password')
    expect(field.autocomplete).toBe('off')
    expect(page.button('Verbinden')!.disabled).toBe(true)
    await page.type(field, `  ${SECRET} `)
    await page.click(page.button('Verbinden'))
    expect(server.called('PUT', '/api/v1/sources/hardcover/token')!.body).toEqual({ token: SECRET })
    expect((document.querySelector('#source-hardcover input') as HTMLInputElement).value).toBe('')
    expect(document.body.innerHTML).not.toContain(SECRET)
    expect(page.text).toContain('Hardcover ist verbunden.')
    expect(page.text).toContain('Ein Token ist gespeichert. Ein neuer ersetzt ihn.')
    expect(page.button('Token ersetzen')).toBeTruthy()
  })

  it('says what Hardcover said when it refuses the token', async () => {
    server.fails['PUT /api/v1/sources/hardcover/token'] = { status: 422, code: 'source_token_invalid' }
    await page.show(<SourcesPart />)
    await page.type(document.querySelector('#source-hardcover input') as HTMLInputElement, SECRET)
    await page.click(page.button('Verbinden'))
    expect(page.text).toContain('Hardcover nimmt diesen Token nicht an.')
  })

  it('asks before it disconnects, and says what stays', async () => {
    server.answers['GET /api/v1/sources'] = state({ hardcover_connected: true })
    server.answers['DELETE /api/v1/sources/hardcover/token'] = state()
    await page.show(<SourcesPart />)
    await page.click(page.button('Trennen'))
    expect(server.changes()).toEqual([])
    expect(plain(page.dialog!.textContent)).toContain('Was Hardcover schon geliefert hat, bleibt an den Büchern.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Trennen'))
    await page.inDialog('Trennen')
    expect(server.changes()).toEqual(['DELETE /api/v1/sources/hardcover/token'])
  })

  it('shows a warning when Hardcover refused the token and offers a new one', async () => {
    server.answers['GET /api/v1/sources'] = state({ hardcover_connected: true, sources: [info('hardcover', { state: 'token_invalid', last_error: 'token_invalid' }), info('openlibrary'), info('audible'), info('dnb')] })
    await page.show(<SourcesPart />)
    expect(plain(document.querySelector('[data-testid="hardcover-token-invalid"]')!.textContent)).toContain('Hardcover hat den Token abgelehnt, er ist wohl abgelaufen.')
    expect(sourceState('hardcover')).toContain('Token abgelehnt')
    expect(page.button('Token ersetzen')).toBeTruthy()
  })

  it('says that the interface is a beta', async () => {
    await page.show(<SourcesPart />)
    expect(page.text).toContain('noch Beta')
  })
})

describe('matching everything again', () => {
  it('asks first and says that what people decided stays', async () => {
    server.answers['POST /api/v1/sources/rematch'] = { books: 12 }
    await page.show(<SourcesPart />)
    await page.click(page.button('Alles neu abgleichen'))
    expect(server.changes()).toEqual([])
    expect(plain(page.dialog!.textContent)).toContain('Was Personen entschieden oder korrigiert haben, bleibt.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Alles neu abgleichen'))
    await page.inDialog('Neu abgleichen')
    expect(server.changes()).toEqual(['POST /api/v1/sources/rematch'])
    expect(page.text).toContain('Die Bücher werden im Hintergrund neu abgeglichen.')
  })
})

describe('the words', () => {
  it('come in English with the same sentences, and never use a dash as a pause', async () => {
    await changeLanguage('en', false)
    await page.show(<SourcesPart />)
    expect(sourceState('dnb')).toBe('Off · 12 of 4000 requests today')
    expect(plain(document.querySelector('[data-testid="sources-privacy"]')!.textContent)).toBe('Only a title, an author and an ISBN go out. Never files, folders, libraries or the names of people.')
    expect(page.text).toContain('German National Library')
    expect(page.text).not.toMatch(/[–—]/)
  })
})
