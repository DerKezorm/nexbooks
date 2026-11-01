/**
 * The review list: the books the matching is not sure about, each with the proposals of the sources and the
 * reasons for and against them; taking a proposal, looking for others, leaving a book as it is.
 */
import type { Library, Proposal, ReviewItem } from '../api/client'
import { changeLanguage } from '../i18n'
import { Page, Server, plain } from '../test/harness'
import { eventually } from '../test/wait'
import { ReviewPage } from './ReviewPage'

const server = new Server()
const page = new Page()

function proposal(over: Partial<Proposal> = {}): Proposal {
  return {
    id: 'openlibrary:OL1W:', source: 'openlibrary', title: 'Der stille Obstgarten', subtitle: '', authors: ['Mara Lindqvist'], narrators: [], year: 2021,
    languages: ['de', 'en'], series: 'Obstgarten-Reihe', series_pos: 1, runtime_min: null, variant: '', market: '', score: 68,
    why: [{ code: 'title_exact', text: 'x' }, { code: 'author_weak', text: 'x' }, { code: 'language_ok', text: 'x' }], missing: [], against: [], detail: {}, lacks: [], lacks_join: 'or', cap: '',
    fatal: false, found: false,
    cover_url: '/api/v1/review/7/cover?id=openlibrary:OL1W:', ...over,
  }
}
function item(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: 7, edition_id: 70, work_id: 700, library_id: 1, state: 'maybe', reason: 'author_weak', reason_text: 'x', kind: 'ebook', title: 'Der stille Obstgarten',
    subtitle: '', authors: ['Mara Lindqvist'], language: 'de', year: null, series: '', series_pos: null, runtime_min: null, trash_days: 30, file: 'Lindqvist/Obstgarten.epub', cover_url: null, gap: null,
    proposals: [proposal(), proposal({ id: 'hardcover:7001:', source: 'hardcover', title: 'The Quiet Orchard', fatal: true, why: [{ code: 'title_differs', text: 'x' }], cover_url: null, score: 12 })],
    ...over,
  }
}
const manage = (id: number, name: string, review: number): Library => ({ id, name, kind: 'both', my_right: 'manage', state: 'idle', counts: { ebooks: 3, audiobooks: 0, files: 3, bytes: 1000, problems: 0, review, entries: 0, pending: 0 }, progress: { done: 0, total: 0 }, matching: 'idle', resting: [] })

function serve(items: ReviewItem[] = [item()], libraries: Library[] = [manage(1, 'Familie', items.length)]) {
  server.answers = {
    'GET /api/v1/libraries': libraries,
    'GET /api/v1/libraries/1/review?limit=50&offset=0': { total: items.length, items },
    'GET /api/v1/libraries/2/review?limit=50&offset=0': { total: 1, items: [item({ id: 9, library_id: 2, title: 'Club Buch', proposals: [] })] },
    'GET /api/v1/review?limit=50&offset=0': { total: items.length + 1, items: [...items, item({ id: 9, library_id: 2, title: 'Club Buch', proposals: [] })] },
    'POST /api/v1/review/7/accept': { to_look_at: 0 },
    'POST /api/v1/review/7/keep': { to_look_at: 0 },
    'POST /api/v1/editions/70/trash': { trash_id: 1, work_id: null, missing: [], people: 0 },
  }
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.fails = {}
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('an entry', () => {
  it('shows the book as the files describe it and why the matching is unsure', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    const entry = document.querySelector('[data-testid="review-item"]')!
    expect(plain(entry.querySelector('h2')!.textContent)).toBe('Der stille Obstgarten')
    expect(plain(entry.textContent)).toContain('Mara Lindqvist')
    expect(plain(entry.textContent)).toContain('DE')
    expect(plain(document.querySelector('[data-testid="review-file"]')!.textContent)).toBe('Lindqvist/Obstgarten.epub')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe('Vielleicht · Nur der Nachname des Autors passt.')
  })

  it('lists the proposals with source, details, points and the reasons for and against, in words', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    const rows = [...document.querySelectorAll('[data-testid="proposal"]')]
    expect(rows.length).toBe(2)
    const first = plain(rows[0].textContent)
    expect(first).toContain('Der stille Obstgarten')
    expect(first).toContain('2021 · DE EN · Obstgarten-Reihe 1')
    expect(first).toContain('Open Library')
    expect(first).toContain('68 Punkte')
    const reasons = [...rows[0].querySelectorAll('.why')].map((chip) => [chip.getAttribute('data-code'), chip.className.includes('is-for') ? 'for' : chip.className.includes('is-against') ? 'against' : 'careful'])
    expect(reasons).toEqual([['title_exact', 'for'], ['author_weak', 'careful'], ['language_ok', 'for']])
    expect(plain(rows[0].textContent)).toContain('Gleicher Titel')
    expect(rows[1].className).toContain('is-fatal')
    expect(rows[1].querySelector('.why')!.className).toContain('is-against')
    expect(page.button('Trotzdem übernehmen', rows[1] as HTMLElement)).toBeTruthy()
    expect(page.button('Übernehmen', rows[0] as HTMLElement)).toBeTruthy()
  })

  it('puts a proposal that only shares the ISBN under the reasons against, in words', async () => {
    serve([item({ reason: 'isbn_conflict', proposals: [proposal({ why: [{ code: 'isbn_match', text: 'x' }, { code: 'isbn_conflict', text: 'x' }, { code: 'author_differs', text: 'x' }] })] })])
    await page.show(<ReviewPage />, '/review?library=1')
    const row = document.querySelector('[data-testid="proposal"]') as HTMLElement
    const chips = [...row.querySelectorAll('.why')].map((chip) => [chip.getAttribute('data-code'), chip.className.includes('is-for') ? 'for' : chip.className.includes('is-against') ? 'against' : 'careful'])
    expect(chips).toEqual([['isbn_match', 'for'], ['isbn_conflict', 'against'], ['author_differs', 'against']])
    expect(plain(row.textContent)).toContain('ISBN gleich, sonst ein anderes Buch')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toContain('Die ISBN stimmt, aber Titel oder Autor passen nicht dazu.')
  })

  it('loads the picture of a proposal from nexbooks, never from the source', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    const sources = [...document.querySelectorAll('[data-testid="proposal"] img')].map((image) => image.getAttribute('src'))
    expect(sources).toEqual(['/api/v1/review/7/cover?id=openlibrary:OL1W:'])
    expect([...document.querySelectorAll('img')].every((image) => image.getAttribute('src')!.startsWith('/api/v1/'))).toBe(true)
  })

  it('says when the sources know nothing', async () => {
    serve([item({ proposals: [], state: 'open', reason: 'nothing_found' })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="review-no-proposals"]')!.textContent)).toContain('Die Quellen kennen dieses Buch nicht')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe('Offen · Die Quellen kennen nichts unter diesem Titel.')
  })

  it('throws a book that fits nothing away after a question, and it leaves the list', async () => {
    serve([item()])
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(document.querySelector('[data-testid="review-trash"]'))
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain('30 Tage')
    expect(server.changes()).toEqual([])
    const button = [...document.querySelectorAll('[role="dialog"] button')].find((item) => item.textContent === 'In den Papierkorb')
    await page.click(button as HTMLElement)
    await eventually(() => expect(document.querySelector('[data-testid="review-item"]')).toBeNull())
    expect(server.called('POST', '/api/v1/editions/70/trash')).toBeTruthy()
  })

  it('writes the running time of the files beside those of the proposals', async () => {
    serve([item({ kind: 'audio', runtime_min: 506 })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="review-item"]')!.textContent)).toContain('DE · 506 Min. · Hörbuch')
  })

  it('writes the running time and the narrators of a recording', async () => {
    serve([item({ kind: 'audio', proposals: [proposal({ id: 'audible:B0:de', source: 'audible', market: 'de', runtime_min: 600, variant: 'dramatized', narrators: ['Ben Hollister'], languages: ['de'], series: '', series_pos: null })] })])
    await page.show(<ReviewPage />, '/review?library=1')
    const text = plain(document.querySelector('[data-testid="proposal"]')!.textContent)
    expect(text).toContain('2021 · DE · 600 Min. · Hörspiel · Ben Hollister')
    expect(text).toContain('Audible · DE')
  })
})

describe('what stands between a book and sure', () => {
  const lacking = proposal({
    score: 75, why: [{ code: 'title_main', text: 'x' }, { code: 'author_match', text: 'x' }, { code: 'language_ok', text: 'x' }],
    missing: [{ code: 'no_isbn', text: 'x' }, { code: 'one_source', text: 'x' }, { code: 'no_year', text: 'x' }],
    against: [{ code: 'year_off', text: 'x' }], detail: { year_off: { file: 2019, source: 2022 } }, lacks: ['isbn', 'second_source'],
  })

  it('shows what is missing in grey and dashed and what speaks against it in red, in words with the numbers', async () => {
    serve([item({ reason: 'low_score', proposals: [lacking], gap: { sure_at: 80, points: 75, missing: ['isbn', 'second_source'], join: 'or', cap: '', tie: null } })])
    await page.show(<ReviewPage />, '/review?library=1')
    const row = document.querySelector('[data-testid="proposal"]') as HTMLElement
    const chips = [...row.querySelectorAll('.why')].map((chip) => [chip.getAttribute('data-code'), chip.className.replace('why ', ''), plain(chip.textContent)])
    expect(chips).toEqual([
      ['title_main', 'is-careful', 'Titel bis auf den Untertitel gleich'],
      ['author_match', 'is-for', 'Gleicher Autor'],
      ['language_ok', 'is-for', 'Sprache passt'],
      ['year_off', 'is-against', 'Jahr weicht ab (2022 statt 2019)'],
      ['no_isbn', 'is-missing', 'Keine ISBN in der Datei'],
      ['one_source', 'is-missing', 'Nur eine Quelle'],
      ['no_year', 'is-missing', 'Jahr fehlt'],
    ])
  })

  it('says under the points how many it needs, how many it has and what would help', async () => {
    serve([item({ reason: 'low_score', proposals: [lacking], gap: { sure_at: 80, points: 75, missing: ['isbn', 'second_source'], join: 'or', cap: '', tie: null } })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="proposal-gap"]')!.textContent)).toBe(
      'Sicher ab 80 Punkten, hier 75. Es fehlt: eine ISBN in der Datei oder eine zweite Quelle, die das Buch kennt.')
    // Said once: beside the book stands the state only, not the same words a second time.
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe('Vielleicht')
    expect(document.body.textContent!.match(/Sicher ab 80 Punkten/g)).toHaveLength(1)
  })

  it('names what holds a proposal back whatever its points, and joins what is needed together with "und"', async () => {
    const held = proposal({ score: 85, cap: 'author_weak', lacks: ['isbn'], why: [{ code: 'title_exact', text: 'x' }, { code: 'author_weak', text: 'x' }] })
    const together = proposal({ id: 'b', score: 62, lacks: ['second_source', 'language'], lacks_join: 'and' })
    serve([item({ reason: 'author_weak', proposals: [held, together], gap: { sure_at: 80, points: 85, missing: ['isbn'], join: 'or', cap: 'author_weak', tie: null } })])
    await page.show(<ReviewPage />, '/review?library=1')
    const lines = [...document.querySelectorAll('[data-testid="proposal-gap"]')].map((node) => plain(node.textContent))
    expect(lines).toEqual([
      'Der Autor passt nur zum Nachnamen. Es fehlt: eine ISBN in der Datei.',
      'Sicher ab 80 Punkten, hier 62. Es fehlt: eine zweite Quelle, die das Buch kennt und die Sprache der Datei.',
    ])
  })

  it('says nothing about points under a proposal that cannot be the book, nor under one that is sure enough', async () => {
    serve([item({ proposals: [proposal({ score: 12, fatal: true }), proposal({ id: 'x', score: 84 })] })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(document.querySelectorAll('[data-testid="proposal-gap"]').length).toBe(0)
  })

  it('names the two proposals that fit equally well and what tells them apart', async () => {
    const gap = (difference: string, a: string | null, b: string | null) => ({ sure_at: 80, points: 82, missing: [], join: 'or' as const, cap: '', tie: { difference, a, b } })
    serve([item({ reason: 'ambiguous', proposals: [proposal(), proposal({ id: 'b' })], gap: gap('volume', '1', '2') })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe(
      'Vielleicht · Zwei Vorschläge passen gleich gut; sie unterscheiden sich im Band (1 und 2).')
    page.close()
    serve([item({ reason: 'ambiguous', proposals: [proposal(), proposal({ id: 'b' })], gap: gap('nothing', null, null) })])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe(
      'Vielleicht · Zwei Vorschläge passen gleich gut; in der Datei steht nichts, was sie unterscheidet.')
  })

  it('says it in English too, without a dash', async () => {
    await changeLanguage('en', false)
    serve([item({ reason: 'low_score', proposals: [lacking], gap: { sure_at: 80, points: 75, missing: ['isbn', 'second_source'], join: 'or', cap: '', tie: null } })])
    await page.show(<ReviewPage />, '/review?library=1')
    const text = plain(document.querySelector('[data-testid="proposal-gap"]')!.textContent)
    expect(text).toBe('Sure from 80 points, here 75. It lacks: an ISBN in the file or a second source that knows the book.')
    expect(plain(document.querySelector('.why[data-code="year_off"]')!.textContent)).toBe('Year differs (2022 instead of 2019)')
    expect(text).not.toMatch(/[\u2013\u2014]/)
  })
})

describe('deciding', () => {
  it('takes a proposal by its id, sends nothing else, and the book leaves the list', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    const row = document.querySelector('[data-testid="proposal"]') as HTMLElement
    await page.click(page.button('Übernehmen', row))
    expect(server.called('POST', '/api/v1/review/7/accept')!.body).toEqual({ proposal_id: 'openlibrary:OL1W:' })
    expect(document.querySelector('[data-testid="review-item"]')).toBeNull()
    expect(document.querySelector('[data-testid="review-empty"]')).toBeTruthy()
  })

  it('leaves a book as it is, with a line that says what that means', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    expect(page.text).toContain('Das Buch bleibt, wie die Dateien es beschreiben, und verschwindet aus dieser Liste.')
    await page.click(page.button('So lassen'))
    expect(server.called('POST', '/api/v1/review/7/keep')).toBeTruthy()
    expect(document.querySelector('[data-testid="review-item"]')).toBeNull()
  })

  it('asks for nothing before reading and sends nothing until a button is pressed', async () => {
    serve()
    await page.show(<ReviewPage />, '/review?library=1')
    expect(server.changes()).toEqual([])
  })

  it('tells what went wrong and keeps the book in the list', async () => {
    serve()
    server.fails['POST /api/v1/review/7/accept'] = { status: 404, code: 'proposal_gone' }
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Übernehmen', document.querySelector('[data-testid="proposal"]') as HTMLElement))
    expect(page.text).toContain('Diesen Vorschlag gibt es nicht mehr.')
    expect(document.querySelector('[data-testid="review-item"]')).toBeTruthy()
  })
})

describe('a book that changed meanwhile', () => {
  it('says so in words, keeps the book in the list, and the other buttons still work', async () => {
    serve()
    server.fails['POST /api/v1/review/7/keep'] = { status: 409, code: 'entry_changed' }
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('So lassen'))
    expect(page.text).toContain('Dieses Buch hat sich inzwischen geändert.')
    expect(document.querySelector('[data-testid="review-item"]')).toBeTruthy()
  })

  it('says when a person searches too often', async () => {
    serve()
    server.fails['POST /api/v1/review/7/search'] = { status: 429, code: 'review_search_too_often' }
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Anderes suchen'))
    await page.click(page.button('Suchen'))
    expect(page.text).toContain('Zu viele Suchen auf einmal.')
  })
})

describe('looking for others', () => {
  it('opens a form with the title and the author, sends them, and shows what was found as the person\'s own search', async () => {
    serve()
    const found = item({ proposals: [proposal({ id: 'openlibrary:OL5W:', title: 'Anderes Buch', found: true, score: 55 }), ...item().proposals] })
    server.answers['POST /api/v1/review/7/search'] = found
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Anderes suchen'))
    const inputs = [...document.querySelectorAll<HTMLInputElement>('form input')]
    expect(inputs.map((input) => input.value)).toEqual(['Der stille Obstgarten', 'Mara Lindqvist'])
    await page.type(inputs[0], 'Obstgarten Geschichten')
    await page.click(page.button('Suchen'))
    expect(server.called('POST', '/api/v1/review/7/search')!.body).toEqual({ title: 'Obstgarten Geschichten', author: 'Mara Lindqvist' })
    const rows = [...document.querySelectorAll('[data-testid="proposal"]')]
    expect(rows.length).toBe(3)
    expect(plain(rows[0].textContent)).toContain('von dir gesucht')
    expect(document.querySelector('form')).toBeNull()
  })

  it('says it when no source could be asked', async () => {
    serve()
    server.fails['POST /api/v1/review/7/search'] = { status: 409, code: 'sources_off' }
    await page.show(<ReviewPage />, '/review?library=1')
    await page.click(page.button('Anderes suchen'))
    await page.click(page.button('Suchen'))
    expect(page.text).toContain('Es ist keine Quelle eingeschaltet.')
  })
})

describe('the libraries', () => {
  it('lets a person who looks after several choose, with how many books wait in each', async () => {
    serve([item()], [manage(1, 'Familie', 1), manage(2, 'Club', 4)])
    await page.show(<ReviewPage />, '/review?library=1')
    const choice = document.querySelector('.seg')!
    expect(plain(choice.textContent)).toBe('Alle5Familie1Club4')
    await page.click(page.button('Club4', choice as HTMLElement))
    expect(plain(document.querySelector('[data-testid="review-item"] h2')!.textContent)).toBe('Club Buch')
  })

  it('offers only the libraries the person manages', async () => {
    serve([item()], [manage(1, 'Familie', 1), { ...manage(2, 'Lesen', 0), my_right: 'read' }])
    await page.show(<ReviewPage />, '/review?library=1')
    expect(document.querySelector('.seg')).toBeNull()
    expect(server.calls.some((call) => call.url.includes('/libraries/2/review'))).toBe(false)
  })

  it('shows every library the person looks after when none is named, each book with its library', async () => {
    serve([item()], [manage(1, 'Familie', 1), manage(2, 'Club', 1)])
    await page.show(<ReviewPage />, '/review')
    expect(server.calls.some((call) => call.url === '/api/v1/review?limit=50&offset=0')).toBe(true)
    expect(server.calls.some((call) => /\/libraries\/\d+\/review/.test(call.url))).toBe(false)
    expect(document.querySelector('.seg [aria-pressed="true"]')!.textContent).toContain('Alle')
    expect([...document.querySelectorAll('[data-testid="review-item"]')].map((card) => plain(card.querySelector('[data-testid="review-library"]')!.textContent))).toEqual(['Familie', 'Club'])
    await page.click(page.button('Familie1', document.querySelector('.seg') as HTMLElement))
    expect(document.querySelectorAll('[data-testid="review-item"]')).toHaveLength(1)
    expect(document.querySelector('[data-testid="review-library"]')).toBeNull()
  })

  it('says where to get the right when the person manages no library', async () => {
    serve([], [{ ...manage(1, 'Familie', 0), my_right: 'read' }])
    await page.show(<ReviewPage />, '/review')
    const note = document.querySelector('[data-testid="review-no-library"]')!
    expect(plain(note.textContent)).toContain('Du verwaltest keine Bibliothek.')
    expect(note.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=libraries')
    expect(server.calls.some((call) => call.url.includes('/review'))).toBe(false)
  })
})

describe('the words', () => {
  it('come in English with the same sentences, and never use a dash as a pause', async () => {
    serve()
    await changeLanguage('en', false)
    await page.show(<ReviewPage />, '/review?library=1')
    expect(plain(document.querySelector('[data-testid="review-reason"]')!.textContent)).toBe("Maybe · Only the author's last name fits.")
    expect(page.button('Leave as it is')).toBeTruthy()
    expect(page.button('Search for another')).toBeTruthy()
    expect(page.text).toContain('Same title')
    expect(page.text).not.toMatch(/[–—]/)
  })
})
