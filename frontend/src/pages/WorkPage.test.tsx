/**
 * The page of a book: its words and facts in their order, the way back and Escape, the focus, the
 * small shelves of the series and of the author, and the hand of the person who looks after the library: every change asks
 * first, says whether it can be taken back, and sends nothing before the one click on the button of the question.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import { changeLanguage } from '../i18n'
import { ebook, edition, library, recording, work, workOut } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { MOVE_POLL_MS } from '../components/MoveDialog'
import { forgetEreaders } from '../state/ereaders'
import { WorkPage } from './WorkPage'

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname}</div>
}

function app() {
  return (
    <Routes>
      <Route path="work/:id" element={<WorkPage />} />
      <Route path="*" element={<Where />} />
    </Routes>
  )
}

function serve(over: { right?: 'read' | 'manage'; work?: ReturnType<typeof workOut> } = {}) {
  const book =
    over.work ??
    workOut(1, [ebook(1, { formats: ['epub', 'azw3'] }), recording(2)], {
      title: 'Die Salzwiese',
      series: { id: 3, name: 'Deich-Krimis', pos: 2 },
      year: 2019,
      pages: 320,
      genre: 'krimi',
      description: 'Zwischen Ebbe und Flut beginnt eine Suche.',
    })
  server.answers = {
    'GET /api/v1/works/1': book,
    'GET /api/v1/libraries': [library({ my_right: over.right ?? 'read' })],
    'GET /api/v1/series/3': { id: 3, name: 'Deich-Krimis', works: [work(8, { title: 'Band Eins', series: { id: 3, name: 'Deich-Krimis', pos: 1 } }), work(1, { title: 'Die Salzwiese' }), work(9, { title: 'Band Drei' })] },
    'GET /api/v1/people/1': { id: 1, name: 'Ada Feldkamp', wrote: [work(1), work(11, { title: 'Mehr eins' }), work(12, { title: 'Mehr zwei' })], narrated: [] },
    'PATCH /api/v1/editions/1': { work_id: 1 },
    'POST /api/v1/editions/1/unlink': { to_look_at: 0 },
    'POST /api/v1/editions/1/split': { work_id: 77 },
    'GET /api/v1/works/5': workOut(5, [ebook(1), ebook(9)], { title: 'Das Haus am Nordkanal' }),
    'GET /api/v1/works/77': workOut(77, [ebook(1)], { title: 'Jetzt allein' }),
    'POST /api/v1/editions/1/join': { work_id: 5 },
    'GET /api/v1/search?q=nordkanal&limit=8': { query: 'nordkanal', items: [work(1), work(5, { title: 'Das Haus am Nordkanal' })] },
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  // Nothing turns here (the test runner has no animations): the page appears at once and closes at once.
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the words of the page', () => {
  it('says quietly that the genre is a guess, and only then', async () => {
    serve({ work: workOut(1, [ebook(1)], { title: 'Allein', genre: 'krimi', genre_guess: true, series: null }) })
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('.d-eyebrow')!.textContent)).toBe('Krimi & Thriller (geschätzt)')
    const note = document.querySelector('[data-testid="genre-guess"]')!
    expect(note.getAttribute('title')).toContain('Weder die Datei noch eine Quelle nennen ein Genre')
    page.close()
    serve({ work: workOut(1, [ebook(1)], { title: 'Allein', genre: 'krimi', genre_guess: false, series: null }) })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="genre-guess"]')).toBeNull()
    expect(plain(document.querySelector('.d-eyebrow')!.textContent)).toBe('Krimi & Thriller')
  })

  it('names the genre and the series with the volume, the title, the author, the description and the facts', async () => {
    serve()
    await page.show(app(), '/work/1')
    expect(document.querySelector('.d-eyebrow')!.textContent).toBe('Krimi & Thriller · Deich-Krimis, Band 2')
    expect(document.querySelector('.d-title')!.textContent).toBe('Die Salzwiese')
    expect(plain(document.querySelector('.d-by')!.textContent)).toBe('von Ada Feldkamp')
    expect(document.querySelector('.d-by a')!.getAttribute('href')).toBe('/person/1')
    expect(document.querySelector('.d-blurb')!.textContent).toBe('Zwischen Ebbe und Flut beginnt eine Suche.')
    expect([...document.querySelectorAll('.d-facts span')].map((s) => s.textContent)).toEqual(['2019erschienen', '320Seiten', '2Fassungen', '10 Std 0 MinHörzeit'])
    expect(document.querySelector('.d-h')!.textContent).toBe('Ein Buch, mehrere Fassungen')
  })

  it('names the pen name of an author beside the name, and nothing beside an author who has none', async () => {
    serve({ work: workOut(1, [ebook(1)], { title: 'Allein', authors: [{ id: 1, name: 'Ada Feldkamp', aliases: ['Nele Beispielname'] }, { id: 2, name: 'Ben Hollister' }] }) })
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('.d-by')!.textContent)).toBe('von Ada Feldkamp als Nele Beispielname, Ben Hollister')
    expect(document.querySelectorAll('.d-alias')).toHaveLength(1)
    page.close()
    await changeLanguage('en', false)
    serve({ work: workOut(1, [ebook(1)], { title: 'Allein', authors: [{ id: 1, name: 'Ada Feldkamp', aliases: ['Nele Beispielname'] }] }) })
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('.d-by')!.textContent)).toBe('by Ada Feldkamp as Nele Beispielname')
  })

  it('tells the listening time of the recording the card shows: the unabridged one, wherever it stands in the list', async () => {
    serve({ work: workOut(1, [ebook(1), recording(2, { variant: 'abridged', duration: 22_200 }), recording(3, { variant: 'unabridged', duration: 50_580 })], { title: 'Zweimal gehört' }) })
    await page.show(app(), '/work/1')
    expect([...document.querySelectorAll('.d-facts span')].map((s) => s.textContent).at(-1)).toBe('14 Std 3 MinHörzeit')
    const card = document.querySelector('.twin[data-kind="audio"]')!
    expect(card.textContent).toContain('14 Std 3 Min')
    // Only a shortened one: its time.
    page.close()
    serve({ work: workOut(1, [ebook(1), recording(2, { variant: 'abridged', duration: 22_200 })], { title: 'Nur gekürzt' }) })
    await page.show(app(), '/work/1')
    expect([...document.querySelectorAll('.d-facts span')].map((s) => s.textContent).at(-1)).toBe('6 Std 10 MinHörzeit')
  })

  it('says one edition for a book with one, and leaves out what it does not know', async () => {
    serve({ work: workOut(1, [ebook(1)], { title: 'Allein', year: null, pages: 0, genre: '', series: null, description: '' }) })
    await page.show(app(), '/work/1')
    expect(document.querySelector('.d-eyebrow')).toBeNull()
    expect(document.querySelector('.d-blurb')).toBeNull()
    expect([...document.querySelectorAll('.d-facts span')].map((s) => s.textContent)).toEqual(['1Fassung'])
    expect(document.querySelector('.d-h')!.textContent).toBe('Ein Buch, eine Fassung')
  })

  it('sets a description in paragraphs: a blank line starts one, a line break of the file is a space', async () => {
    serve({ work: workOut(1, [ebook(1)], { title: 'Mit Absätzen', description: 'Erste Zeile\nzweite Zeile.\n\nNeuer Absatz.' }) })
    await page.show(app(), '/work/1')
    expect([...document.querySelectorAll('.d-blurb p')].map((p) => p.textContent)).toEqual(['Erste Zeile zweite Zeile.', 'Neuer Absatz.'])
  })

  it('shows the series as a small shelf with the current volume marked, and more of the author without the book itself', async () => {
    serve()
    await page.show(app(), '/work/1')
    const shelves = [...document.querySelectorAll('.mini-shelf')]
    expect(shelves.length).toBe(2)
    expect([...shelves[0]!.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))).toEqual(['8', '1', '9'])
    expect(shelves[0]!.querySelector('.spine.current')!.getAttribute('data-work')).toBe('1')
    expect([...shelves[1]!.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))).toEqual(['11', '12'])
    expect([...document.querySelectorAll('.d-h')].map((h) => h.textContent)).toEqual(['Ein Buch, mehrere Fassungen', 'Reihe · Deich-Krimis', 'Mehr von Ada Feldkamp'])
  })

  it('leaves the volumes of the series above out of "More by", and the heading too when nothing else is left', async () => {
    serve()
    server.answers['GET /api/v1/people/1'] = { id: 1, name: 'Ada Feldkamp', wrote: [work(1), work(8, { title: 'Band Eins' }), work(9, { title: 'Band Drei' }), work(11, { title: 'Mehr eins' })], narrated: [] }
    await page.show(app(), '/work/1')
    let shelves = [...document.querySelectorAll('.mini-shelf')]
    expect([...shelves[1]!.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))).toEqual(['11'])
    page.close()
    server.answers['GET /api/v1/people/1'] = { id: 1, name: 'Ada Feldkamp', wrote: [work(1), work(8, { title: 'Band Eins' }), work(9, { title: 'Band Drei' })], narrated: [] }
    await page.show(app(), '/work/1')
    shelves = [...document.querySelectorAll('.mini-shelf')]
    expect(shelves).toHaveLength(1)
    expect([...document.querySelectorAll('.d-h')].map((h) => h.textContent)).toEqual(['Ein Buch, mehrere Fassungen', 'Reihe · Deich-Krimis'])
  })

  it('keeps the other books of the author when the series has only one volume of ours', async () => {
    serve()
    server.answers['GET /api/v1/series/3'] = { id: 3, name: 'Deich-Krimis', works: [work(1)] }
    server.answers['GET /api/v1/people/1'] = { id: 1, name: 'Ada Feldkamp', wrote: [work(1), work(8), work(11)], narrated: [] }
    await page.show(app(), '/work/1')
    const shelves = [...document.querySelectorAll('.mini-shelf')]
    expect(shelves).toHaveLength(1)
    expect([...shelves[0]!.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))).toEqual(['8', '11'])
  })

  it('has no shelf of a series with only one volume of ours, and none of an author with no other book', async () => {
    serve()
    server.answers['GET /api/v1/series/3'] = { id: 3, name: 'Deich-Krimis', works: [work(1)] }
    server.answers['GET /api/v1/people/1'] = { id: 1, name: 'Ada Feldkamp', wrote: [work(1)], narrated: [] }
    await page.show(app(), '/work/1')
    expect(document.querySelector('.mini-shelf')).toBeNull()
  })

  it('tells that there is no such book, in words, with the way back', async () => {
    serve()
    server.fails = { 'GET /api/v1/works/1': { status: 404, code: 'not_found' } }
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('[data-testid="work-failure"]')!.textContent)).toContain('Dieses Buch gibt es nicht')
    expect(document.querySelector('.d-back')).toBeTruthy()
  })

  it('has the focus on the page, locks the page behind it, and gives the lock back', async () => {
    serve()
    await page.show(app(), '/work/1')
    const detail = document.querySelector('[data-testid="work"]') as HTMLElement
    expect(detail.getAttribute('role')).toBe('dialog')
    expect(detail.getAttribute('aria-modal')).toBe('true')
    expect(document.activeElement).toBe(detail)
    expect(document.body.classList.contains('lock')).toBe(true)
    page.close()
    expect(document.body.classList.contains('lock')).toBe(false)
  })

  it('keeps the Tab key on the page: from the last thing on it to the first, and back with Shift', async () => {
    serve()
    await page.show(app(), '/work/1')
    const layer = document.querySelector('[data-testid="work"]') as HTMLElement
    const items = [...layer.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')]
    expect(items.length).toBeGreaterThan(3)
    items[items.length - 1]!.focus()
    const forward = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    items[items.length - 1]!.dispatchEvent(forward)
    expect(forward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(items[0])
    const backward = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    items[0]!.dispatchEvent(backward)
    expect(backward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(items[items.length - 1])
    // A Tab in the middle is left to the browser.
    items[1]!.focus()
    const middle = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    items[1]!.dispatchEvent(middle)
    expect(middle.defaultPrevented).toBe(false)
  })

  it('speaks English too', async () => {
    serve()
    await changeLanguage('en', false)
    await page.show(app(), '/work/1')
    expect(document.querySelector('.d-eyebrow')!.textContent).toBe('Crime & Thrillers · Deich-Krimis, book 2')
    expect(plain(document.querySelector('.d-by')!.textContent)).toBe('by Ada Feldkamp')
    expect(document.querySelector('.d-h')!.textContent).toBe('One book, several editions')
    expect(document.querySelector('.d-back')!.textContent).toBe('Library')
  })
})

describe('fetching the book', () => {
  it('asks what may be looked for in the languages allowed and opens the search of a missing kind', async () => {
    serve({ work: workOut(1, [ebook(1, { formats: ['epub'] })], { title: 'Die Salzwiese' }) })
    server.answers['GET /api/v1/procure/works/1'] = { allowed: true, kinds: [{ kind: 'audio', library: 'Hörbücher', have: null, goal: 'flac', upgrade: false }] }
    server.answers['POST /api/v1/procure/searches'] = { id: 4, state: 'queued', error: '', language: 'de', kinds: ['audio'], work_id: 1, book: null, title: 'Die Salzwiese', author: '', created_at: '2026-10-15T10:00:00Z', finished_at: null, indexers: [], cards: [], releases: [] }
    await page.show(app(), '/work/1')
    expect(server.called('GET', '/api/v1/procure/works/1')).toBeTruthy()
    const button = document.querySelector<HTMLButtonElement>('.twin.missing [data-testid="releases"]')!
    expect(button.textContent).toBe('Releases ansehen')
    await page.click(button)
    expect(server.called('POST', '/api/v1/procure/searches')!.body).toEqual({ work_id: 1 })
    expect(page.dialog!.getAttribute('aria-label')).toBe('Releases ansehen')
  })

  it('offers to upload the missing kind, into a library that takes it', async () => {
    serve({ work: workOut(1, [ebook(1, { formats: ['epub'] })], { title: 'Die Salzwiese' }) })
    server.answers['GET /api/v1/procure/works/1'] = { allowed: true, kinds: [{ kind: 'audio', library: 'Hörbücher', have: null, goal: 'flac', upgrade: false }] }
    server.answers['GET /api/v1/procure/targets'] = { targets: [{ id: 1, name: 'Bücher', kind: 'ebook' }, { id: 2, name: 'Hörbücher', kind: 'audio' }] }
    await page.show(app(), '/work/1')
    const button = document.querySelector<HTMLButtonElement>('.twin.missing [data-testid="upload"]')!
    expect(button.textContent).toBe('Hochladen')
    await page.click(button)
    expect(page.dialog!.getAttribute('aria-label')).toBe('Bücher hochladen')
    expect(page.dialog!.textContent).toContain('Zieh die Dateien für das Hörbuch hierher')
    // One library takes a recording: nothing to choose.
    expect(page.dialog!.querySelectorAll('.opt')).toHaveLength(0)
  })

  it('offers nothing to who may not fetch books', async () => {
    serve({ work: workOut(1, [ebook(1, { formats: ['epub'] })], { title: 'Die Salzwiese' }) })
    server.answers['GET /api/v1/procure/works/1'] = { allowed: false, kinds: [] }
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="releases"]')).toBeNull()
    expect(document.querySelector('[data-testid="upload"]')).toBeNull()
  })
})

describe('the way back', () => {
  it('goes to the library with the button and with Escape, and not twice', async () => {
    serve()
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('.d-back') as HTMLElement)
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/')
    page.close()
    serve()
    await page.show(app(), '/work/1')
    const { act } = await import('react')
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/')
  })

  it('names where it comes from: Library when opened by its address', async () => {
    serve()
    await page.show(app(), '/work/1')
    expect(document.querySelector('.d-back')!.textContent).toBe('Bibliothek')
  })
})

describe('the hand of the person who looks after the library', () => {
  it('is offered to a member with manage in the library of an edition, and to nobody else', async () => {
    serve({ right: 'read' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="edit-book"]')).toBeNull()
    page.close()
    serve({ right: 'manage' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="edit-book"]')!.textContent).toBe('Bearbeiten')
  })

  async function openEditing() {
    serve({ right: 'manage' })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="edit-book"]') as HTMLElement)
    expect(page.dialog).toBeTruthy()
  }

  it('asks before it saves a correction, sends only what changed, and sends nothing when the question is cancelled', async () => {
    await openEditing()
    const title = document.querySelector('#fix-title') as HTMLInputElement
    expect(title.value).toBe('Buch')
    await page.type(title, 'Die Salzwiese, neu')
    await page.click(page.button('Speichern', page.dialog!))
    // The question: nothing has been sent yet.
    expect(plain(page.dialog!.textContent)).toContain('Du kannst jede einzelne später zurücknehmen')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Speichern', page.dialog!))
    await page.inDialog('Speichern')
    expect(server.changes()).toEqual(['PATCH /api/v1/editions/1'])
    expect(server.called('PATCH', '/api/v1/editions/1')!.body).toEqual({ title: 'Die Salzwiese, neu' })
    expect(document.querySelector('[data-testid="work-status"]')!.textContent).toBe('Gespeichert.')
  })

  it('names the field in the question when the server refuses an input', async () => {
    await openEditing()
    await page.type(document.querySelector('#fix-title') as HTMLInputElement, '   ')
    await page.click(page.button('Speichern', page.dialog!))
    server.fails = { 'PATCH /api/v1/editions/1': { status: 422, code: 'invalid_input', values: { field: 'title' } } }
    await page.inDialog('Speichern')
    expect(plain(page.dialog!.querySelector('[role="alert"]')!.textContent)).toBe('Die Eingabe ist nicht gültig: Titel.')
  })

  it('says that nothing changed instead of asking, when nothing did', async () => {
    await openEditing()
    await page.click(page.button('Speichern', page.dialog!))
    expect(plain(page.dialog!.textContent)).toContain('Nichts geändert.')
    expect(server.changes()).toEqual([])
  })

  it('takes a correction back only after asking, and says it can be entered again', async () => {
    serve({ right: 'manage' })
    const book = workOut(1, [ebook(1)], { title: 'Mit Korrektur' })
    book.editions[0] = edition(ebook(1), { corrected: ['title'], title: 'Mit Korrektur' })
    server.answers['GET /api/v1/works/1'] = book
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="edit-book"]') as HTMLElement)
    expect(plain(document.querySelector('.fix-hand')!.textContent)).toContain('von dir korrigiert')
    await page.click(document.querySelector('.fix-hand button') as HTMLElement)
    expect(plain(page.dialog!.textContent)).toContain('Du kannst es jederzeit neu eingeben')
    expect(server.changes()).toEqual([])
    await page.inDialog('Zurücknehmen')
    expect(server.called('PATCH', '/api/v1/editions/1')!.body).toEqual({ title: null })
  })

  it('asks before it unlinks, says that this cannot be undone with one click, and then sends it', async () => {
    serve({ right: 'manage' })
    const book = workOut(1, [ebook(1)], { title: 'Zugeordnet' })
    book.editions[0] = edition(ebook(1), { matched_by: ['hardcover', 'openlibrary'] })
    server.answers['GET /api/v1/works/1'] = book
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="edit-book"]') as HTMLElement)
    expect(plain(page.dialog!.textContent)).toContain('Bekannt von Hardcover, Open Library')
    await page.click([...page.dialog!.querySelectorAll('button')].find((b) => b.textContent === 'Zuordnung lösen') as HTMLElement)
    expect(plain(page.dialog!.textContent)).toContain('nicht mit einem Klick zurücknehmen')
    expect(server.changes()).toEqual([])
    await page.inDialog('Zuordnung lösen')
    expect(server.changes()).toEqual(['POST /api/v1/editions/1/unlink'])
  })

  it('offers to unlink only a book that the sources know', async () => {
    await openEditing()
    expect(page.dialog!.textContent).not.toContain('Zuordnung lösen')
  })

  it('asks before it makes an edition a book of its own, and says that this can be taken back', async () => {
    await openEditing()
    await page.click([...page.dialog!.querySelectorAll('button')].find((b) => b.textContent === 'Ist ein anderes Buch') as HTMLElement)
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich zurücknehmen')
    expect(server.changes()).toEqual([])
    await page.inDialog('Eigenes Buch machen')
    expect(server.changes()).toEqual(['POST /api/v1/editions/1/split'])
    // The book has a number of its own now: the page follows it.
    expect(document.querySelector('[data-testid="work-status"]')!.textContent).toBe('Erledigt.')
    expect(server.called('GET', '/api/v1/works/77')).toBeTruthy()
  })

  it('does not offer to split a book that has only one edition', async () => {
    serve({ right: 'manage', work: workOut(1, [ebook(1)], { title: 'Allein' }) })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="edit-book"]') as HTMLElement)
    expect(plain(page.dialog!.textContent)).not.toContain('Ist ein anderes Buch')
  })

  it('searches the own library for the book to join to, never offers the book itself, asks, and sends the number', async () => {
    await openEditing()
    await page.click([...page.dialog!.querySelectorAll('button')].find((b) => b.textContent === 'Gehört zu …') as HTMLElement)
    const search = page.dialog!.querySelector('input[aria-label="Titel, Autor oder Reihe des anderen Buchs"]') as HTMLInputElement
    const { act } = await import('react')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await page.type(search, 'nordkanal')
    await act(async () => void (await vi.advanceTimersByTimeAsync(300)))
    vi.useRealTimers()
    const { idle } = await import('../test/wait')
    await idle()
    const rows = [...page.dialog!.querySelectorAll('button.fix-row')]
    expect(rows.map((r) => plain(r.textContent))).toEqual(['Das Haus am NordkanalAda Feldkamp'])
    await page.click(rows[0] as HTMLElement)
    expect(plain(page.dialog!.textContent)).toContain('Zu „Das Haus am Nordkanal“ stellen?')
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich zurücknehmen')
    expect(server.changes()).toEqual([])
    await page.inDialog('Zusammenführen')
    expect(server.called('POST', '/api/v1/editions/1/join')!.body).toEqual({ work_id: 5 })
  })

  it('tells in the question what the server refused, and keeps the question open', async () => {
    await openEditing()
    server.fails = { 'POST /api/v1/editions/1/split': { status: 403, code: 'forbidden' } }
    await page.click([...page.dialog!.querySelectorAll('button')].find((b) => b.textContent === 'Ist ein anderes Buch') as HTMLElement)
    await page.inDialog('Eigenes Buch machen')
    expect(page.dialog!.querySelector('[role="alert"]')!.textContent).toBeTruthy()
    expect(page.dialog!.textContent).toContain('Ist das ein anderes Buch?')
  })
})

describe('the volumes of the series the person lacks (Discover)', () => {
  const gaps = (over = {}) => ({
    series_id: 3,
    name: 'Deich-Krimis',
    author: 'Ada Feldkamp',
    have: 2,
    out: 4,
    missing: 1,
    soon: 1,
    pending: false,
    volumes: [
      { pos: 1, state: 'own', work: work(8, { title: 'Band Eins', series: { id: 3, name: 'Deich-Krimis', pos: 1 } }), book: null },
      { pos: 2, state: 'own', work: work(1, { title: 'Die Salzwiese', series: { id: 3, name: 'Deich-Krimis', pos: 2 } }), book: null },
      { pos: 3, state: 'missing', work: null, book: { ...fromSource('m3', 'Band Drei'), series_pos: 3 } },
      { pos: 4, state: 'soon', work: null, book: { ...fromSource('m4', 'Band Vier'), series_pos: 4, upcoming: true, released: '2027-01-15' } },
    ],
    ...over,
  })

  function fromSource(token: string, title: string) {
    return {
      token, title, authors: ['Ada Feldkamp'], rank: null, kinds: ['ebook'], forms: [], language: 'de', released: '2025-02-01', upcoming: false, genre: 'krimi',
      color: '#1f304b', cover_url: null, sources: ['hardcover'], owned_work_id: null, wished: false, series: 'Deich-Krimis', series_pos: null,
    }
  }

  it('stand beside the spines as ghosts: dashed, FEHLT for one that is out, BALD for one that is yet to come', async () => {
    serve()
    server.answers['GET /api/v1/discover/series/3'] = gaps()
    await page.show(app(), '/work/1')
    const shelf = document.querySelector('.mini-shelf')!
    const spines = [...shelf.querySelectorAll<HTMLElement>('.spine')]
    expect(spines.map((s) => s.classList.contains('ghost'))).toEqual([false, false, true, true])
    expect(spines.map((s) => s.classList.contains('soon'))).toEqual([false, false, false, true])
    expect(spines.slice(2).map((s) => plain(s.querySelector('.t')!.textContent))).toEqual(['Band 3 · Band Drei', 'Band 4 · Band Vier'])
    expect(spines.slice(2).map((s) => s.querySelector('.a')!.textContent)).toEqual(['FEHLT', 'BALD'])
    expect(shelf.querySelector('.spine.current')!.getAttribute('data-work')).toBe('1')
  })

  it('lead from a ghost to the page of that book', async () => {
    serve()
    server.answers['GET /api/v1/discover/series/3'] = gaps()
    await page.show(app(), '/work/1')
    await page.click(document.querySelectorAll<HTMLElement>('.mini-shelf .spine.ghost')[0]!)
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/discover/book/m3')
  })

  it('keep the small shelf of the library when the sources said nothing, or none of the volumes is lacking', async () => {
    serve()
    await page.show(app(), '/work/1')
    expect(document.querySelectorAll('.mini-shelf .spine.ghost')).toHaveLength(0)
    expect(document.querySelectorAll('.mini-shelf')[0]!.querySelectorAll('.spine')).toHaveLength(3)
    page.close()
    serve()
    server.fails = { 'GET /api/v1/discover/series/3': { status: 404, code: 'not_found' } }
    await page.show(app(), '/work/1')
    expect(document.querySelectorAll('.mini-shelf .spine.ghost')).toHaveLength(0)
  })

  it('show a ghost shelf also for a series of which the person has one volume only', async () => {
    serve()
    server.answers['GET /api/v1/series/3'] = { id: 3, name: 'Deich-Krimis', works: [work(1)] }
    server.answers['GET /api/v1/discover/series/3'] = gaps({ volumes: gaps().volumes.filter((v) => v.pos !== 1) })
    await page.show(app(), '/work/1')
    expect(document.querySelectorAll('.mini-shelf .spine.ghost')).toHaveLength(2)
  })
})

describe('a recording whose reading nothing proves', () => {
  const unproven = (over: Partial<ReturnType<typeof recording>> = {}) =>
    workOut(1, [ebook(1), recording(2, { variant: 'unabridged', variant_uncertain: true, ...over })], { title: 'Nachtglas' })

  it('says so quietly on the card of the recording, and nowhere else', async () => {
    serve({ work: unproven() })
    await page.show(app(), '/work/1')
    const note = document.querySelector('[data-testid="variant-unproven"]')!
    expect(plain(note.textContent)).toContain('Fassung nicht belegt')
    expect(note.closest('[data-kind="audio"]')).toBeTruthy()
    expect(document.querySelectorAll('[data-testid="variant-unproven"]')).toHaveLength(1)
    // The card does not name a reading nothing proves.
    expect(document.querySelector('.twin[data-kind="audio"] h4')!.textContent).toBe('Hörbuch')
    page.close()
    serve({ work: workOut(1, [ebook(1), recording(2)], { title: 'Nachtglas' }) })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="variant-unproven"]')).toBeNull()
    expect(document.querySelector('.twin[data-kind="audio"] h4')!.textContent).toBe('Hörbuch · ungekürzt')
  })

  it('offers the choice to a person who looks after the library and to no one else', async () => {
    serve({ work: unproven(), right: 'read' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="variant-pick"]')).toBeNull()
    page.close()
    serve({ work: unproven(), right: 'manage' })
    await page.show(app(), '/work/1')
    const pick = page.select('Lesung')!
    expect([...pick.options].map((option) => option.textContent)).toEqual(['Nicht belegt', 'ungekürzt', 'gekürzt', 'Hörspiel'])
  })

  it('sets the reading by hand with one request and nothing else', async () => {
    serve({ work: unproven(), right: 'manage' })
    server.answers['PATCH /api/v1/editions/2'] = { work_id: 1 }
    await page.show(app(), '/work/1')
    await page.choose(page.select('Lesung'), 'abridged')
    expect(server.changes()).toEqual(['PATCH /api/v1/editions/2'])
    expect(server.called('PATCH', '/api/v1/editions/2')!.body).toEqual({ variant: 'abridged' })
    expect(document.querySelector('[data-testid="work-status"]')!.textContent).toBe('Gesetzt.')
  })

  it('lets a person who looks after the library confirm a language that was only worked out', async () => {
    serve({ work: unproven({ language: 'de', language_guess: true }), right: 'manage' })
    server.answers['PATCH /api/v1/editions/2'] = { work_id: 1 }
    await page.show(app(), '/work/1')
    const confirm = document.querySelector('[data-testid="language-confirm"]') as HTMLElement
    expect(plain(confirm.textContent)).toBe('Stimmt, es ist Deutsch')
    await page.click(confirm)
    expect(server.called('PATCH', '/api/v1/editions/2')!.body).toEqual({ language: 'de' })
    expect(document.querySelector('[data-testid="work-status"]')!.textContent).toBe('Gesetzt.')
  })

  it('does not offer to confirm a language to a person who only reads, nor a language that is certain', async () => {
    serve({ work: unproven({ language: 'de', language_guess: true }), right: 'read' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="language-confirm"]')).toBeNull()
    page.close()
    serve({ work: unproven({ language: 'de', language_guess: false }), right: 'manage' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="language-confirm"]')).toBeNull()
  })

  it('shows a reading set by hand without the note and takes it back with one click', async () => {
    serve({ work: unproven({ variant: 'abridged', variant_uncertain: false }), right: 'manage' })
    const book = server.answers['GET /api/v1/works/1'] as ReturnType<typeof workOut>
    book.editions[1]!.corrected = ['variant']
    server.answers['PATCH /api/v1/editions/2'] = { work_id: 1 }
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('[data-testid="variant-unproven"]')!.textContent)).toBe('Von Hand gesetzt · zurücknehmen')
    expect(document.querySelector('[data-testid="variant-pick"]')).toBeNull()
    await page.click(document.querySelector('[data-testid="variant-takeback"]') as HTMLElement)
    expect(server.called('PATCH', '/api/v1/editions/2')!.body).toEqual({ variant: null })
  })

  it('says it in English too, and that a language was worked out', async () => {
    await changeLanguage('en', false)
    serve({ work: unproven({ language_guess: true }), right: 'manage' })
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('[data-testid="variant-unproven"]')!.textContent)).toContain('Reading not proven')
    expect(plain(document.querySelector('.twin[data-kind="audio"] .tw-sub')!.textContent)).toContain('Language probably German')
    expect(page.select('Reading')).toBeTruthy()
  })
})

describe('recordings that are probably one recording twice', () => {
  it('says so in a sentence under the editions and merges nothing', async () => {
    serve({ work: workOut(1, [ebook(1), recording(2, { duration: 27_660 }), recording(3, { duration: 27_720 })], { title: 'Sturmzeit', duplicates: [[2, 3]] }) })
    await page.show(app(), '/work/1')
    expect(plain(document.querySelector('[data-testid="duplicates"]')!.textContent)).toBe('Mehrere Hörbuch-Fassungen haben fast dieselbe Laufzeit: vermutlich dieselbe Aufnahme zweimal. Es wurde nichts zusammengeführt.')
    expect(server.changes()).toEqual([])
  })

  it('is not there without duplicates', async () => {
    serve()
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="duplicates"]')).toBeNull()
  })
})

describe('moving an edition into another library', () => {
  const movable = (can = true) => {
    const book = workOut(1, [ebook(1, { formats: ['epub', 'pdf'] }), recording(2)], { title: 'Die Salzwiese', series: null })
    book.editions[0] = { ...book.editions[0], can_move: can }
    return book
  }
  const target = (over = {}) => ({ id: 2, name: 'Annex', kind: 'both', writable: true, write_problem: '', preview: ['Ada Feldkamp/Die Salzwiese.epub', 'Ada Feldkamp/Die Salzwiese.pdf'], more: 0, folder: '', copies: false, problem: '', ...over })
  const options = (over = {}) => ({
    edition_id: 1,
    kind: 'ebook',
    source: { id: 1, name: 'Familie', writable: true, write_problem: '' },
    files: 2,
    bytes: 24_000_000,
    targets: [target(), target({ id: 3, name: 'Archiv', writable: false, write_problem: 'read_only', preview: [] })],
    busy: false,
    latest: null,
    ...over,
  })
  const moved = (over = {}) => ({ id: 9, edition_id: 1, source: { id: 1, name: 'Familie' }, target: { id: 2, name: 'Annex' }, state: 'queued', step: 'place', error: '', files: 2, placed: 0, removed: 0, where: 'source', left: 0, created_at: '2026-10-17T10:00:00Z', started_at: null, finished_at: null, ...over })

  it('is offered on the card only where the server says the edition can move', async () => {
    serve({ work: movable(false), right: 'manage' })
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="move-edition"]')).toBeNull()
    page.close()
    serve({ work: movable(true), right: 'manage' })
    await page.show(app(), '/work/1')
    const buttons = document.querySelectorAll('[data-testid="move-edition"]')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].closest('.twin')!.getAttribute('data-kind')).toBe('ebook')
    expect(plain(buttons[0].textContent)).toBe('In andere Bibliothek verschieben')
  })

  it('shows the targets with the one it cannot write in greyed out, the path, what happens, and asks before it moves', async () => {
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options()
    server.answers['POST /api/v1/editions/1/move'] = moved()
    server.answers['GET /api/v1/moves/9'] = moved({ state: 'done', step: 'done', where: 'target', placed: 2, removed: 2 })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="move-edition"]'))
    const dialog = page.dialog!
    expect(plain(dialog.getAttribute('aria-label'))).toBe('In andere Bibliothek verschieben')
    const select = dialog.querySelector('[data-testid="move-target"]') as HTMLSelectElement
    expect([...select.options].map((item) => [plain(item.textContent), item.disabled])).toEqual([
      ['Annex', false],
      ['Archiv (nicht beschreibbar: schreibgeschützt eingebunden)', true],
    ])
    expect([...dialog.querySelectorAll('[data-testid="move-preview"] code')].map((item) => item.textContent)).toEqual(['Ada Feldkamp/Die Salzwiese.epub', 'Ada Feldkamp/Die Salzwiese.pdf'])
    expect(plain(dialog.querySelector('[data-testid="move-what"]')!.textContent)).toBe('nexbooks verschiebt 2 Dateien (24 MB) im Hintergrund. Die Fassung bleibt dieselbe: Lesestand, Lesezeichen, Verlauf, Zuordnung und Korrekturen bleiben erhalten. Nichts wird überschrieben.')
    // The question first; cancelled, nothing is sent.
    await page.click(dialog.querySelector('[data-act="move-go"]'))
    expect(plain(page.dialog!.textContent)).toContain('Nach „Annex“ verschieben?')
    expect(plain(page.dialog!.textContent)).toContain('Lesestand, Lesezeichen und Verlauf bleiben. Zurück geht es auf demselben Weg.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.dialog!.querySelector('[data-act="move-go"]'))
    await page.inDialog('Verschieben')
    expect(server.changes()).toEqual(['POST /api/v1/editions/1/move'])
    expect(server.called('POST', '/api/v1/editions/1/move')!.body).toEqual({ library_id: 2, confirm: true })
    expect(plain(page.dialog!.querySelector('[data-testid="move-state"]')!.textContent)).toContain('Wartet, bis nexbooks im Hintergrund frei ist.')
    const before = server.calls.filter((call) => call.url === '/api/v1/works/1').length
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, MOVE_POLL_MS + 200))
    })
    expect(plain(document.querySelector('[data-testid="work-status"]')!.textContent)).toBe('Verschoben nach „Annex“.')
    expect(server.calls.filter((call) => call.url === '/api/v1/works/1').length).toBeGreaterThan(before)
  })

  it('says where the files lie after a failure and tries again on a press', async () => {
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options({ latest: moved({ state: 'failed', error: 'no_space', where: 'source' }) })
    server.answers['POST /api/v1/moves/9/retry'] = moved({ state: 'queued' })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="move-edition"]'))
    const state = page.dialog!.querySelector('[data-testid="move-state"]')!
    expect(plain(state.textContent)).toContain('Das Verschieben hat nicht geklappt. Am Ziel ist nicht genug Platz.')
    expect(plain(state.querySelector('[data-testid="move-where"]')!.textContent)).toBe('Alle Dateien liegen noch in „Familie“, nichts ging verloren.')
    // Before the switch the book lies whole where it lay: another library may be chosen instead.
    await page.click(state.querySelector('[data-act="move-other"]'))
    expect(page.dialog!.querySelector('[data-testid="move-target"]')).toBeTruthy()
    expect(server.changes()).toEqual([])
    page.close()
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options({ latest: moved({ state: 'failed', error: 'no_space', where: 'source' }) })
    server.answers['POST /api/v1/moves/9/retry'] = moved({ state: 'queued' })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="move-edition"]'))
    await page.click(page.dialog!.querySelector('[data-act="move-retry"]'))
    expect(server.called('POST', '/api/v1/moves/9/retry')!.body).toEqual({ confirm: true })
    page.close()
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options({ latest: moved({ state: 'failed', step: 'clear', error: 'source_left', where: 'both', left: 1 }) })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="move-edition"]'))
    expect(plain(page.dialog!.querySelector('[data-testid="move-where"]')!.textContent)).toBe('Die Fassung liegt jetzt in „Annex“. In „Familie“ liegt noch 1 alte Datei.')
    // After the switch the book belongs to the target: only another try finishes it.
    expect(page.dialog!.querySelector('[data-act="move-other"]')).toBeNull()
  })

  it('is offered in the editing too, for every edition that can move, and speaks English', async () => {
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options({ targets: [] })
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="edit-book"]') as HTMLElement)
    await page.click(page.dialog!.querySelector('[data-act="move-edition"]'))
    expect(plain(page.dialog!.querySelector('[data-testid="move-none"]')!.textContent)).toBe('Keine andere Bibliothek, die du verwaltest, nimmt diese Fassung auf.')
    expect(page.dialog!.querySelector('[data-act="move-go"]')).toBeNull()
    page.close()
    await changeLanguage('en', false)
    serve({ work: movable(), right: 'manage' })
    server.answers['GET /api/v1/editions/1/move'] = options()
    await page.show(app(), '/work/1')
    await page.click(document.querySelector('[data-testid="move-edition"]'))
    expect(plain(page.dialog!.getAttribute('aria-label'))).toBe('Move to another library')
    expect(plain((page.dialog!.querySelector('[data-testid="move-target"]') as HTMLSelectElement).options[1].textContent)).toBe('Archiv (not writable: mounted read-only)')
  })
})

describe('sending the e-book to a Kindle', () => {
  afterEach(() => forgetEreaders())

  it('is offered on the card of the e-book that has an EPUB, and not on the recording', async () => {
    serve()
    server.answers['GET /api/v1/ereaders'] = { allowed: true, links: [], kindle: { address: 'ada_k1@example.com', mail_ready: true, sender: 'books@example.com', max_megabytes: 50 } }
    server.answers['POST /api/v1/files/10/kindle'] = { queued: true }
    forgetEreaders()
    server.install()
    await page.show(app(), '/work/1')
    const cards = [...document.querySelectorAll('[data-testid="twin"]')]
    expect(cards.map((card) => card.getAttribute('data-kind'))).toEqual(['ebook', 'audio'])
    expect(cards[0].querySelector('[data-testid="send-to-kindle"]')!.textContent).toBe('An Kindle senden')
    expect(cards[1].querySelector('[data-testid="send-to-kindle"]')).toBeNull()
    await page.click(cards[0].querySelector('[data-testid="send-to-kindle"]'))
    expect(server.changes()).toEqual(['POST /api/v1/files/10/kindle'])
  })

  it('is not offered where the operator has switched e-readers off', async () => {
    serve()
    server.answers['GET /api/v1/ereaders'] = { allowed: false, links: [], kindle: { address: '', mail_ready: false, sender: '', max_megabytes: 50 } }
    forgetEreaders()
    server.install()
    await page.show(app(), '/work/1')
    expect(document.querySelector('[data-testid="twin"]')).toBeTruthy()
    expect(document.querySelector('[data-testid="send-to-kindle"]')).toBeNull()
  })
})
