/**
 * Settings, Fetching, Formats and place, Automation and Rights: the ranking of the formats with its ticks and its goal, the
 * languages, the places with the check, the naming with its preview, the automation, and the rights of a library. A change is
 * saved at once, and a refusal puts the page back as it was.
 */
import type { AutomationConfig, FormatsOut, IndexerInfo, LibraryAdmin, LibraryCheck } from '../../../api/client'
import { changeLanguage } from '../../../i18n'
import { Page, Server, plain } from '../../../test/harness'
import { AutomationPart } from './AutomationPart'
import { FormatsPart } from './FormatsPart'
import { RightsPart } from './RightsPart'

const server = new Server()
const page = new Page()

function formats(over: Partial<FormatsOut> = {}): FormatsOut {
  return {
    ebook: [{ f: 'epub', on: true }, { f: 'azw3', on: true }, { f: 'mobi', on: true }, { f: 'pdf', on: false }],
    audio: [{ f: 'flac', on: true }, { f: 'm4b_aac', on: true }, { f: 'mp3_320', on: true }, { f: 'mp3_192', on: true }, { f: 'mp3_128', on: true }, { f: 'mp3_64', on: false }],
    target: { ebook: 'epub', audio: 'flac' }, languages: ['de', 'en'], original: true, variant: 'unabridged', abridged: false, dramatized: false,
    size: { ebook: [0.05, 80], audio: [20, 20000] }, library: { ebook: 1, audio: 2 }, schema: '{Author}/{Series}/{Volume} - {Title} ({Year})',
    libraries: [{ id: 1, name: 'Bücher', kind: 'ebook', path: '/books/ebooks', writable: true, write_problem: '' }, { id: 2, name: 'Hörbücher', kind: 'audio', path: '/books/hoerbuecher', writable: true, write_problem: '' }, { id: 3, name: 'Kinder', kind: 'both', path: '/books/kinder', writable: true, write_problem: '' }],
    known_ebook: ['epub', 'azw3', 'mobi', 'pdf'], known_audio: ['flac', 'm4b_aac', 'mp3_320', 'mp3_192', 'mp3_128', 'mp3_64'],
    known_languages: ['mixed', 'de', 'en', 'fr', 'es'], samples: [{ author: 'Ada Example', title: 'The Lantern Road', series: 'Lantern Saga', position: 2, year: '2019' }, { author: 'Ben Sample', title: 'Standalone Tale', series: '', position: null, year: '2021' }],
    ...over,
  }
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'GET /api/v1/procure/formats': formats(), 'POST /api/v1/procure/formats/preview': { ebook: '/books/ebooks/Ada Example/Lantern Saga/02 - The Lantern Road (2019).epub', audio: '/books/hoerbuecher/Ada Example/Lantern Saga/02 - The Lantern Road (2019)/', sample: 0 } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

/** A button by its name, however a person's name inside it is set apart from the sentence. */
const named = (label: string) => [...document.querySelectorAll('button')].find((item) => plain(item.getAttribute('aria-label')) === label || plain(item.textContent) === label) as HTMLButtonElement
const body = () => server.calls.filter((c) => c.method === 'PUT' && c.url === '/api/v1/procure/formats').at(-1)!.body as Record<string, unknown>
const rows = (kind: string) => [...document.querySelectorAll(`[data-testid="rank-${kind}"] > li > div[draggable]`)].map((row) => plain(row.querySelector('b')!.textContent))

describe('the ranking', () => {
  it('lists the formats top down with their hints, what is taken first and what is never loaded', async () => {
    await page.show(<FormatsPart />)
    expect(rows('ebook')).toEqual(['EPUB', 'AZW3', 'MOBI', 'PDF'])
    expect(rows('audio')).toEqual(['FLAC', 'M4B/AAC hoch', 'MP3 320', 'MP3 192', 'MP3 128', 'MP3 64'])
    const first = plain(document.querySelector('[data-testid="rank-ebook-epub"]')!.textContent)
    expect(first).toContain('01')
    expect(first).toContain('Fließtext, passt sich an · wird zuerst genommen')
    expect(plain(document.querySelector('[data-testid="rank-ebook-pdf"]')!.textContent)).toContain('feste Seiten, oft Scans · wird nie geladen')
    expect(plain(document.querySelector('[data-testid="rank-audio-mp3_64"]')!.textContent)).toContain('64 kbit/s, klingt dünn · wird nie geladen')
  })

  it('draws the goal line below the goal', async () => {
    await page.show(<FormatsPart />)
    const line = document.querySelector('[data-testid="goal-line-ebook"]')!
    expect(line.previousElementSibling?.getAttribute('data-testid')).toBe('rank-ebook-epub')
    expect(plain(line.getAttribute('aria-label'))).toBe('Ziel EPUB: darüber ist es gut genug, darunter sucht nexbooks weiter nach Besserem')
    expect(plain(document.body.textContent)).toContain('Hat ein Buch EPUB oder etwas, das weiter oben steht, sucht nexbooks nicht weiter nach Besserem.')
  })

  // The label stood under the line, right over the next format, and read as if the formats below
  // were good enough. Now the line has a text on each side: above it what is good enough, below it what is looked past.
  it('says above the goal line that it is good enough and below it that nexbooks looks on', async () => {
    server.answers['GET /api/v1/procure/formats'] = formats({ target: { ebook: 'epub', audio: 'mp3_128' } })
    await page.show(<FormatsPart />)
    const line = document.querySelector('[data-testid="goal-line-audio"]')!
    // The line follows the goal, MP3 128, and comes before MP3 64.
    expect(line.previousElementSibling?.getAttribute('data-testid')).toBe('rank-audio-mp3_128')
    expect(line.parentElement!.nextElementSibling?.querySelector('[data-testid="rank-audio-mp3_64"]')).not.toBeNull()
    const parts = [...line.children].map((child) => child.getAttribute('data-testid'))
    expect(parts).toEqual(['goal-above-audio', 'goal-rule-audio'])
    expect(plain(line.querySelector('[data-testid="goal-above-audio"]')!.textContent)).toBe('↑ gut genug, Ziel erreicht')
    expect(line.querySelector('[data-testid="goal-rule-audio"]')!.className).toContain('border-dashed')
    expect(plain(document.body.textContent)).toContain('Hat ein Buch MP3 128 oder etwas, das weiter oben steht, sucht nexbooks nicht weiter nach Besserem.')
    // Only MP3 64 lies below, and it is not ticked: nothing below is fetched, so nothing is said of looking on.
    expect(line.querySelector('[data-testid="goal-below-audio"]')).toBeNull()
    page.close()
    server.answers['GET /api/v1/procure/formats'] = formats({ target: { ebook: 'epub', audio: 'mp3_320' } })
    await page.show(<FormatsPart />)
    const again = document.querySelector('[data-testid="goal-line-audio"]')!
    expect(again.previousElementSibling?.getAttribute('data-testid')).toBe('rank-audio-mp3_320')
    expect([...again.children].map((child) => child.getAttribute('data-testid'))).toEqual(['goal-above-audio', 'goal-rule-audio', 'goal-below-audio'])
    expect(plain(again.querySelector('[data-testid="goal-below-audio"]')!.textContent)).toBe('↓ darunter sucht nexbooks weiter nach Besserem')
    page.close()
    await changeLanguage('en', false)
    await page.show(<FormatsPart />)
    const english = document.querySelector('[data-testid="goal-line-audio"]')!
    expect(plain(english.querySelector('[data-testid="goal-above-audio"]')!.textContent)).toBe('↑ good enough, goal reached')
    expect(plain(english.querySelector('[data-testid="goal-below-audio"]')!.textContent)).toBe('↓ below this nexbooks keeps looking for something better')
  })

  it('moves a format up with the arrow and saves at once', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats({ ebook: [{ f: 'azw3', on: true }, { f: 'epub', on: true }, { f: 'mobi', on: true }, { f: 'pdf', on: false }] })
    await page.show(<FormatsPart />)
    await page.click(page.button('AZW3 nach oben'))
    expect((body().ebook as { f: string }[]).map((item) => item.f)).toEqual(['azw3', 'epub', 'mobi', 'pdf'])
    expect(rows('ebook')[0]).toBe('AZW3')
    expect(page.text).toContain('Gespeichert.')
  })

  it('cannot move the first line up or the last one down', async () => {
    await page.show(<FormatsPart />)
    expect((page.button('EPUB nach oben') as HTMLButtonElement).disabled).toBe(true)
    expect((page.button('PDF nach unten') as HTMLButtonElement).disabled).toBe(true)
  })

  it('moves a row by dragging it', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    const list = document.querySelector('[data-testid="rank-ebook"]')!
    const from = list.querySelector('[data-testid="rank-ebook-mobi"]') as HTMLElement
    const to = list.querySelector('[data-testid="rank-ebook-epub"]') as HTMLElement
    const { act } = await import('react')
    await act(async () => {
      from.dispatchEvent(new Event('dragstart', { bubbles: true }))
      to.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }))
      to.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }))
    })
    expect((body().ebook as { f: string }[]).map((item) => item.f)).toEqual(['mobi', 'epub', 'azw3', 'pdf'])
  })

  it('ticks a format on and off, and keeps at least one allowed', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    await page.click(document.querySelector('input[aria-label="PDF erlaubt"]') as HTMLElement)
    expect((body().ebook as { f: string; on: boolean }[]).find((item) => item.f === 'pdf')!.on).toBe(true)
    server.answers['GET /api/v1/procure/formats'] = formats({ ebook: [{ f: 'epub', on: true }, { f: 'azw3', on: false }, { f: 'mobi', on: false }, { f: 'pdf', on: false }] })
    page.close()
    await page.show(<FormatsPart />)
    const before = server.calls.filter((c) => c.method === 'PUT').length
    await page.click(document.querySelector('input[aria-label="EPUB erlaubt"]') as HTMLElement)
    expect(server.calls.filter((c) => c.method === 'PUT').length).toBe(before)
    expect(page.text).toContain('Mindestens ein Format muss erlaubt bleiben.')
  })

  it('moves the goal to the top allowed format when the goal itself is unticked', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    await page.click(document.querySelector('input[aria-label="EPUB erlaubt"]') as HTMLElement)
    expect((body().target as { ebook: string }).ebook).toBe('azw3')
  })

  it('offers only allowed formats as the goal', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    const select = document.querySelector('#formats-ebook select') as HTMLSelectElement
    expect([...select.options].map((option) => option.value)).toEqual(['epub', 'azw3', 'mobi'])
    await page.choose(select, 'mobi')
    expect((body().target as { ebook: string }).ebook).toBe('mobi')
  })

  it('saves one change after the other, so a slow answer never overtakes a later click', async () => {
    const events: string[] = []
    const sent: { audio: { f: string; on: boolean }[] }[] = []
    const asked = globalThis.fetch
    let puts = 0
    // The first save answers late, the second at once; the server keeps what it was sent last, as the real one does.
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method !== 'PUT' || url !== '/api/v1/procure/formats') return asked(url, init)
      const number = ++puts
      events.push(`start ${number}`)
      sent.push(JSON.parse(String(init.body)))
      if (number === 1) await new Promise((resolve) => setTimeout(resolve, 60))
      events.push(`end ${number}`)
      return new Response(JSON.stringify(formats(JSON.parse(String(init.body)))), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    await page.show(<FormatsPart />)
    const { act } = await import('react')
    const tick = (name: string) => document.querySelector(`input[aria-label="${name}"]`) as HTMLInputElement
    await act(async () => {
      tick('MP3 192 erlaubt').click()
      tick('MP3 128 erlaubt').click()
      await new Promise((resolve) => setTimeout(resolve, 200))
    })
    expect(events).toEqual(['start 1', 'end 1', 'start 2', 'end 2'])
    const on = (name: string) => tick(name).checked
    expect([on('MP3 192 erlaubt'), on('MP3 128 erlaubt')]).toEqual([false, false])
    const last = sent.at(-1)!.audio.filter((item) => ['mp3_192', 'mp3_128'].includes(item.f)).map((item) => item.on)
    expect(last, 'the last request holds both clicks').toEqual([false, false])
  })

  it('goes back to what was saved when the server says no', async () => {
    server.fails['PUT /api/v1/procure/formats'] = { status: 422, code: 'invalid_input' }
    await page.show(<FormatsPart />)
    await page.click(page.button('AZW3 nach oben'))
    expect(rows('ebook')).toEqual(['EPUB', 'AZW3', 'MOBI', 'PDF'])
    expect(document.querySelector('[data-testid="formats-feedback"] [role="alert"]')).toBeTruthy()
  })
})

describe('the labels and the tint of the design', () => {
  it('names the languages and the version above their choices, and tints the chosen version', async () => {
    await page.show(<FormatsPart />)
    const languages = document.querySelector('#formats-languages')!
    expect([...languages.querySelectorAll('span.font-semibold')].map((item) => plain(item.textContent))).toContain('Sprachen')
    const variant = document.querySelector('#formats-variant')!
    expect([...variant.querySelectorAll('span.font-semibold')].map((item) => plain(item.textContent))).toContain('Fassung')
    expect(variant.querySelector('[role="radio"][aria-checked="true"]')!.className).toContain('bg-accent-soft')
  })
})

describe('the other cards', () => {
  it('ranks the languages, the original one at its place, and adds and takes them off', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    const list = document.querySelector('[data-testid="rank-languages"]')!
    // An older choice without the place of the original language: it stands last.
    expect([...list.querySelectorAll('li b')].map((item) => plain(item.textContent))).toEqual(['Deutsch', 'Englisch', 'Originalsprache'])
    expect(plain(list.querySelector('li small')!.textContent)).toBe('zuerst gesucht')
    await page.click(document.querySelector('[aria-label="Originalsprache nach oben"]') as HTMLElement)
    expect(body().languages).toEqual(['de', 'original', 'en'])
    expect(body().original).toBe(true)
    const group = document.querySelector('#formats-languages [role="group"]')!
    expect([...group.querySelectorAll('button')].map((b) => plain(b.textContent))).toEqual(['Französisch', 'Spanisch'])
    await page.click(page.button('Französisch', group as HTMLElement))
    expect(body().languages).toEqual(['de', 'en', 'original', 'fr'])
    await page.click(document.querySelector('[aria-label="Originalsprache erlaubt"]') as HTMLElement)
    expect(body().languages).toEqual(['de', 'en'])
    expect(body().original).toBe(false)
  })

  it('keeps abridged versions off while only unabridged ones are wanted', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats({ variant: 'prefer' })
    await page.show(<FormatsPart />)
    const abridged = document.querySelector('[role="switch"][aria-label="Gekürzte Fassungen erlauben"]') as HTMLButtonElement
    expect(abridged.disabled).toBe(true)
    await page.click(document.querySelector('[role="radio"][aria-label], #formats-variant [role="radio"]:nth-of-type(2)') as HTMLElement)
    expect(body().variant).toBe('prefer')
  })

  it('saves a size when its field is left', async () => {
    server.answers['PUT /api/v1/procure/formats'] = formats()
    await page.show(<FormatsPart />)
    const field = document.querySelector('input[aria-label="Höchstgröße E-Book, Größe eines Releases in MB"]') as HTMLInputElement
    await page.type(field, '120')
    const { act } = await import('react')
    await act(async () => field.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
    expect((body().size as { ebook: number[] }).ebook).toEqual([0.05, 120])
  })

  it('chooses the library for each kind from those that fit, and checks that it can be written to', async () => {
    const check: LibraryCheck = { writable: true, reason: '', hardlink: true, downloads: [{ client: 'SABnzbd', folder: '/downloads/complete/nexbooks', seen: true, same_filesystem: false }] }
    server.answers['POST /api/v1/procure/formats/check-library'] = check
    await page.show(<FormatsPart />)
    const select = document.querySelector('select[aria-label="E-Books legt nexbooks in"]') as HTMLSelectElement
    expect([...select.options].map((option) => plain(option.textContent))).toEqual(['Noch keine gewählt', 'Bücher', 'Kinder'])
    await page.click(page.button('Prüfen', select.parentElement!))
    expect(server.called('POST', '/api/v1/procure/formats/check-library')!.body).toEqual({ library_id: 1 })
    expect(plain(document.querySelector('[data-testid="check-ebook"]')!.textContent)).toContain('/books/ebooks beschreibbar, Hardlinks möglich')
    expect(plain(document.querySelector('[data-testid="check-ebook"]')!.textContent)).toContain('Der Download-Ordner von SABnzbd liegt auf einem anderen Laufwerk: nexbooks kopiert statt zu verlinken.')
  })

  it.each([
    ['read_only', 'ist schreibgeschützt eingebunden'],
    ['no_permission', 'nur lesbar. nexbooks kann dort nichts ablegen. Gib dem Container Schreibrecht.'],
    ['missing', 'ist nicht erreichbar.'],
  ])('says why a place cannot be written to: %s', async (reason, sentence) => {
    server.answers['POST /api/v1/procure/formats/check-library'] = { writable: false, reason, hardlink: null, downloads: [] }
    await page.show(<FormatsPart />)
    await page.click(page.button('Prüfen', (document.querySelector('select[aria-label="E-Books legt nexbooks in"]') as HTMLElement).parentElement!))
    expect(plain(document.querySelector('[data-testid="check-ebook"]')!.textContent)).toContain(sentence)
  })

  it('says at once, without a check, that the chosen library is read-only, and which libraries are writable', async () => {
    const base = formats()
    server.answers['GET /api/v1/procure/formats'] = formats({ libraries: [{ ...base.libraries[0], writable: false, write_problem: 'read_only' }, base.libraries[1], base.libraries[2]] })
    await page.show(<FormatsPart />)
    const hint = plain(document.querySelector('[data-testid="unwritable-ebook"]')!.textContent)
    expect(hint).toContain('„Bücher“ ist schreibgeschützt eingebunden (bei Docker mit :ro). nexbooks kann dort nichts ablegen.')
    expect(hint).toContain('Beschreibbar sind: Kinder.')
    expect(document.querySelector('[data-testid="unwritable-audio"]')).toBeNull()
    expect(document.querySelector('[data-testid="place-none-writable"]')).toBeNull()
  })

  // A library nexbooks cannot write into stays in the list, greyed out and with its reason; the one
  // chosen already stays choosable, the others cannot be picked.
  it('greys out the libraries it cannot write into, with the reason, and keeps the chosen one', async () => {
    const base = formats()
    server.answers['GET /api/v1/procure/formats'] = formats({ libraries: [{ ...base.libraries[0], writable: false, write_problem: 'read_only' }, base.libraries[1], { ...base.libraries[2], writable: false, write_problem: 'no_permission' }] })
    await page.show(<FormatsPart />)
    const ebook = document.querySelector('[aria-label="E-Books legt nexbooks in"]') as HTMLSelectElement
    expect([...ebook.options].map((item) => [item.textContent, item.disabled])).toEqual([
      ['Noch keine gewählt', false],
      ['Bücher (nicht beschreibbar: schreibgeschützt eingebunden)', false],
      ['Kinder (nicht beschreibbar: kein Schreibrecht)', true],
    ])
    const audio = document.querySelector('[aria-label="Hörbücher legt nexbooks in"]') as HTMLSelectElement
    expect([...audio.options].map((item) => [item.textContent, item.disabled])).toEqual([
      ['Noch keine gewählt', false],
      ['Hörbücher', false],
      ['Kinder (nicht beschreibbar: kein Schreibrecht)', true],
    ])
  })

  it('says that nothing is loaded when no library is writable', async () => {
    const base = formats()
    server.answers['GET /api/v1/procure/formats'] = formats({ libraries: base.libraries.map((item) => ({ ...item, writable: false, write_problem: 'no_permission' })) })
    await page.show(<FormatsPart />)
    expect(plain(document.querySelector('[data-testid="place-none-writable"]')!.textContent)).toContain('Keine Bibliothek ist beschreibbar.')
    expect(document.querySelector('[data-testid="unwritable-ebook"]')!.textContent).not.toContain('Beschreibbar sind')
  })

  it('has no check to run while no library is chosen', async () => {
    server.answers['GET /api/v1/procure/formats'] = formats({ library: { ebook: null, audio: null } })
    await page.show(<FormatsPart />)
    expect((page.button('Prüfen', (document.querySelector('select[aria-label="E-Books legt nexbooks in"]') as HTMLElement).parentElement!) as HTMLButtonElement).disabled).toBe(true)
  })

  it('previews the naming and says what is wrong with a scheme that cannot be used', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      await page.show(<FormatsPart />)
      const { act } = await import('react')
      await act(async () => vi.advanceTimersByTimeAsync(400))
      expect(plain(document.querySelector('[data-testid="preview-ebook"]')!.textContent)).toBe('/books/ebooks/Ada Example/Lantern Saga/02 - The Lantern Road (2019).epub')
      expect(plain(document.querySelector('[data-testid="preview-audio"]')!.textContent)).toBe('/books/hoerbuecher/Ada Example/Lantern Saga/02 - The Lantern Road (2019)/')
      server.fails['POST /api/v1/procure/formats/preview'] = { status: 422, code: 'scheme_invalid', values: { reason: 'backslash' } }
      await page.type(document.querySelector('#formats-naming input') as HTMLInputElement, '{Author}\\{Title}')
      await act(async () => vi.advanceTimersByTimeAsync(400))
      expect(plain(document.querySelector('[data-testid="naming-problem"]')!.textContent)).toBe('Statt \\ bitte / nehmen.')
      // A scheme that cannot be used is never saved.
      const input = document.querySelector('#formats-naming input') as HTMLInputElement
      await act(async () => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
      expect(server.calls.filter((c) => c.method === 'PUT')).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  it('adds a variable to the scheme with a press', async () => {
    await page.show(<FormatsPart />)
    await page.click(page.button('{Autor}'))
    expect((document.querySelector('#formats-naming input') as HTMLInputElement).value).toBe('{Author}/{Series}/{Volume} - {Title} ({Year}){Autor}')
  })
})

// ---- Automation ------------------------------------------------------------------------------------------------------

const STATUS = { enabled: true, last_run_at: null, next_run_at: null, due: 0, waiting_for_a_day: 0, wishes_open: 0, indexers: [], day_ends_at: '2026-10-17T00:00:00Z', zone: 'UTC', runs: [] }
const AUTO: AutomationConfig = { enabled: true, rss_minutes: 15, retry_hours: 6, replace: true, replace_max: 3, seed: 'ratio', retention_days: 0 }
function indexer(id: number, over: Partial<IndexerInfo> = {}): IndexerInfo {
  return { id, name: `Bücherei ${id}`, kind: 'newznab', url: 'https://x.example.com/api', key_set: true, enabled: true, priority: id, daily_limit: 200, state: 'ready', used_today: 42, grabs: 0, grabs_today: 0, rss_checked_at: null, rss_error: '', day_ends_at: '2026-10-17T00:00:00Z', via_prowlarr: false, categories: [], resting_until: null, resting_cause: '', last_error: '', api_current: null, api_max: null, grab_current: null, grab_max: null, supported_params: { search: [], book: [], audio: [], book_search: false, audio_search: false }, caps_checked_at: null, ...over }
}

describe('the automation', () => {
  beforeEach(() => {
    server.answers = { 'GET /api/v1/procure/automation': AUTO, 'GET /api/v1/procure/automation/status?limit=12': STATUS, 'GET /api/v1/procure/indexers': { indexers: [indexer(1), indexer(2, { daily_limit: 0, used_today: 6 })] } }
  })
  const put = () => server.calls.filter((c) => c.method === 'PUT' && c.url === '/api/v1/procure/automation').at(-1)!.body

  it('is on from the start and says so', async () => {
    await page.show(<AutomationPart />)
    expect(document.querySelector<HTMLElement>('#auto-main [role="switch"]')!.getAttribute('aria-checked')).toBe('true')
    expect(page.text).toContain('An. Noch kein Lauf.')
    expect(document.querySelector('[data-testid="auto-rest"]')!.getAttribute('aria-disabled')).toBe('false')
  })

  it('switches off with the main switch, saved at once, and dims the rest', async () => {
    server.answers['PUT /api/v1/procure/automation'] = { ...AUTO, enabled: false }
    await page.show(<AutomationPart />)
    await page.click(document.querySelector('#auto-main [role="switch"]') as HTMLElement)
    expect(put()).toEqual({ ...AUTO, enabled: false })
    expect(page.text).toContain('Aus. Wünsche warten auf „Jetzt suchen“.')
    expect(document.querySelector('[data-testid="auto-rest"]')!.getAttribute('aria-disabled')).toBe('true')
  })

  it('chooses the rhythm of the newest entries and of searching again', async () => {
    server.answers['PUT /api/v1/procure/automation'] = AUTO
    await page.show(<AutomationPart />)
    await page.click(page.button('60 Min'))
    expect(put()).toEqual({ ...AUTO, rss_minutes: 60 })
    await page.click(page.button('24 Std'))
    expect(put()).toMatchObject({ retry_hours: 24 })
  })

  it('shows the limit of each indexer and saves it when the field is left', async () => {
    server.answers['PATCH /api/v1/procure/indexers/1'] = indexer(1, { daily_limit: 90 })
    await page.show(<AutomationPart />)
    expect(plain(document.querySelector('[data-testid="limit-1"]')!.textContent)).toContain('heute 42 von 200')
    expect(plain(document.querySelector('[data-testid="limit-2"]')!.textContent)).toContain('heute 6')
    const field = document.querySelector('input[aria-label="Tagesgrenze Bücherei 1"]') as HTMLInputElement
    await page.type(field, '90')
    const { act } = await import('react')
    await act(async () => field.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
    expect(server.called('PATCH', '/api/v1/procure/indexers/1')!.body).toEqual({ daily_limit: 90 })
  })

  it('takes a number of tries only while a replacement is wanted', async () => {
    server.answers['PUT /api/v1/procure/automation'] = { ...AUTO, replace: false }
    await page.show(<AutomationPart />)
    const tries = document.querySelector('#auto-replace input[type="number"]') as HTMLInputElement
    expect(tries.disabled).toBe(false)
    await page.click(document.querySelector('#auto-replace [role="switch"]') as HTMLElement)
    expect((document.querySelector('#auto-replace input[type="number"]') as HTMLInputElement).disabled).toBe(true)
  })

  it('goes back to what was saved when the server says no', async () => {
    server.fails['PUT /api/v1/procure/automation'] = { status: 422, code: 'invalid_input' }
    await page.show(<AutomationPart />)
    await page.click(page.button('5 Min'))
    expect(document.querySelector('#auto-rss [role="radio"][aria-checked="true"]')!.textContent).toBe('15 Min')
  })

  it('names the seed goals', async () => {
    server.answers['PUT /api/v1/procure/automation'] = AUTO
    await page.show(<AutomationPart />)
    const select = document.querySelector('#auto-seed select') as HTMLSelectElement
    expect([...select.options].map((option) => plain(option.textContent))).toEqual(['Verhältnis 1,0', '7 Tage', 'gar nicht'])
    await page.choose(select, '7d')
    expect(put()).toMatchObject({ seed: '7d' })
  })

  it('says next to the goals that private trackers often ask for a ratio when nothing is seeded', async () => {
    await page.show(<AutomationPart />)
    expect(plain(document.querySelector('#auto-seed [data-testid="seed-hint"]')!.textContent)).toBe(
      'Bei „gar nicht“ geht der Torrent sofort, auch wenn der Tracker ein Verhältnis oder eine Mindestzeit verlangt. Private Tracker tun das oft: Wähle dann eines der beiden anderen.',
    )
    expect(plain(document.querySelector('#auto-seed')!.textContent)).toContain('Ab Werk verlässt der Torrent das Programm, sobald das Buch eingeordnet ist.')
  })
})

// ---- Rights ----------------------------------------------------------------------------------------------------------

function library(id: number, name: string, members: LibraryAdmin['members']): LibraryAdmin {
  return { id, name, kind: 'both', my_right: 'manage', state: 'idle', counts: { ebooks: 0, audiobooks: 0, files: 0, bytes: 0, problems: 0, review: 0, entries: 0, pending: 0 }, progress: { done: 0, total: 0 }, matching: 'idle', resting: [], path: `/books/${name}`, scan_enabled: true, scanned_at: null, report: null, members }
}
const account = (id: number, name: string, role: 'operator' | 'member') => ({ id, name, display_name: '', role })

describe('the rights', () => {
  beforeEach(() => {
    server.answers = {
      'GET /api/v1/libraries/admin': [
        library(1, 'Bücher', [{ user_id: 1, name: 'mira', display_name: '', right: 'manage' }, { user_id: 2, name: 'tom', display_name: '', right: 'acquire' }, { user_id: 3, name: 'lena', display_name: '', right: 'read' }]),
        library(2, 'Kinder', [{ user_id: 1, name: 'mira', display_name: '', right: 'manage' }]),
      ],
      'GET /api/v1/accounts': [account(1, 'mira', 'operator'), account(2, 'tom', 'member'), account(3, 'lena', 'member'), account(4, 'oskar', 'member')],
    }
  })
  const rightOf = (name: string) => [...document.querySelectorAll(`[data-testid="right-${name}"] [role="radio"]`)].map((radio) => `${plain(radio.textContent)}:${radio.getAttribute('aria-checked')}`).join(' ')

  it('shows every account with its right in the library, and none for who is not in it', async () => {
    await page.show(<RightsPart />)
    expect(rightOf('tom')).toBe('Lesen:false Beschaffen:true Verwalten:false')
    expect(rightOf('lena')).toBe('Lesen:true Beschaffen:false Verwalten:false')
    expect(rightOf('oskar')).toBe('Lesen:false Beschaffen:false Verwalten:false')
    expect(plain(document.querySelector('[data-testid="right-mira"]')!.textContent)).toContain('Betreiber')
    expect(plain(document.querySelector('[data-testid="rights-legend"]')!.textContent)).toContain('Beschaffen zusätzlich Wünsche anlegen, Suche und Downloads steuern.')
  })

  it('gives a right and sends only that change, with the right the page saw', async () => {
    server.answers['PATCH /api/v1/libraries/1/members'] = library(1, 'Bücher', [])
    await page.show(<RightsPart />)
    await page.click(document.querySelector('[data-testid="right-oskar"] [role="radio"]:nth-of-type(2)') as HTMLElement)
    expect(server.called('PATCH', '/api/v1/libraries/1/members')!.body).toEqual({ changes: [{ user_id: 4, was: null, right: 'acquire' }] })
    expect(server.called('PUT', '/api/v1/libraries/1/members')).toBeUndefined()
  })

  it('moves a person up from reading to managing', async () => {
    server.answers['PATCH /api/v1/libraries/1/members'] = library(1, 'Bücher', [])
    await page.show(<RightsPart />)
    await page.click(document.querySelector('[data-testid="right-lena"] [role="radio"]:nth-of-type(3)') as HTMLElement)
    expect(server.called('PATCH', '/api/v1/libraries/1/members')!.body).toEqual({ changes: [{ user_id: 3, was: 'read', right: 'manage' }] })
  })

  it('says so and reads the list again when somebody else changed the rights meanwhile', async () => {
    server.fails['PATCH /api/v1/libraries/1/members'] = { status: 409, code: 'members_changed' }
    await page.show(<RightsPart />)
    const reads = () => server.calls.filter((c) => c.method === 'GET' && c.url === '/api/v1/libraries/admin').length
    expect(reads()).toBe(1)
    await page.click(document.querySelector('[data-testid="right-lena"] [role="radio"]:nth-of-type(3)') as HTMLElement)
    expect(page.text).toContain('Jemand hat die Rechte inzwischen geändert.')
    expect(reads()).toBe(2)
  })

  it('shows the chosen right tinted in the colour of the accent', async () => {
    await page.show(<RightsPart />)
    const chosen = document.querySelector('[data-testid="right-tom"] [role="radio"][aria-checked="true"]')!
    expect(chosen.className).toContain('bg-accent-soft')
    expect(chosen.className).not.toContain('shadow')
  })

  it('looks at one library at a time', async () => {
    await page.show(<RightsPart />)
    await page.click(named('Kinder'))
    expect(rightOf('tom')).toBe('Lesen:false Beschaffen:false Verwalten:false')
    expect(page.text).toContain('Mitglieder von „Kinder“')
  })

  it('asks before it takes access away, and says what stays', async () => {
    server.answers['PATCH /api/v1/libraries/1/members'] = library(1, 'Bücher', [])
    await page.show(<RightsPart />)
    await page.click(named('Zugriff von tom entziehen'))
    expect(plain(page.dialog!.textContent)).toContain('tom sieht die Bibliothek „Bücher“ nicht mehr. Fortschritt und Merkliste bleiben erhalten. Du kannst den Zugriff später wieder geben.')
    expect(server.called('PATCH', '/api/v1/libraries/1/members')).toBeUndefined()
    await page.inDialog('Abbrechen')
    expect(server.called('PATCH', '/api/v1/libraries/1/members')).toBeUndefined()
    await page.click(named('Zugriff von tom entziehen'))
    await page.inDialog('Zugriff entziehen')
    expect(server.called('PATCH', '/api/v1/libraries/1/members')!.body).toEqual({ changes: [{ user_id: 2, was: 'acquire', right: null }] })
  })

  it('cannot take access from somebody who has none', async () => {
    await page.show(<RightsPart />)
    expect(named('Zugriff von oskar entziehen').disabled).toBe(true)
  })

  it('says to make a library first when there is none', async () => {
    server.answers['GET /api/v1/libraries/admin'] = []
    await page.show(<RightsPart />)
    expect(page.text).toContain('Lege zuerst unter Bibliotheken einen Ordner an.')
  })
})
