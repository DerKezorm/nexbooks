/**
 * The check after the download under "Braucht dich" and in the history: the questions of the check with
 * their answers (the running time, the bundle, files nexbooks does not recognise), the release turned down for its
 * language, the note of a place carried over by its share, and a job nobody asked for given to a wish. Every answer that
 * deletes or blocks asks first, and without the confirmation nothing is sent.
 */
import { Route, Routes } from 'react-router-dom'

import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { ProcureProvider } from '../../state/procure'
import { Page, Server, plain } from '../../test/harness'
import { counts, foreign, line, problem, wish } from '../../test/procure'
import { eventually } from '../../test/wait'
import { AcquirePage } from '../AcquirePage'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'operator', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

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
    'GET /api/v1/procure/counts': counts({ needs: 1 }),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [] },
    'GET /api/v1/procure/history': { entries: [] },
    'GET /api/v1/procure/blocklist': { entries: [] },
    ...answers,
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const card = (id: number) => document.querySelector(`[data-problem="${id}"]`)!

describe('the questions of the check', () => {
  it('shows the running times side by side and keeps the recording as abridged', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'abridged', loaded: 15120, unabridged: 34380, abridged: 14940 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/1/decide': { id: 1, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    const text = plain(card(1).textContent)
    // The mark says what the running time fits (the words of the model), not only that it does not.
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Laufzeit passt zu einer gekürzten Fassung')
    expect(text).toContain('Das Hörbuch dauert 4 Std 12 Min. Ungekürzt wären es 9 Std 33 Min.')
    expect([...card(1).querySelectorAll('.rt small')].map((item) => item.textContent)).toEqual(['Geladen', 'Gekürzt laut Katalog', 'Ungekürzt laut Katalog'])
    await page.click(card(1).querySelector('[data-act="keep"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/1/decide')!.body).toEqual({ action: 'keep' })
  })

  it('asks about a recording a little too short for the unabridged one, and keeps it as unabridged', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'shorter', loaded: 37080, unabridged: 42240 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/1/decide': { id: 1, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Laufzeit zu kurz für ungekürzt')
    expect(plain(card(1).textContent)).toContain('Das Hörbuch dauert 10 Std 18 Min. Ungekürzt wären es 11 Std 44 Min.')
    expect(plain(card(1).querySelector('[data-act="keep"]')!.textContent)).toBe('Als ungekürzt übernehmen')
  })

  it('asks about a radio play where a reading is known, and keeps it as a play', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'dramatized', loaded: 19320, unabridged: 48660 } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Hörspiel statt Lesung')
    expect(plain(card(1).querySelector('[data-act="keep"]')!.textContent)).toBe('Als Hörspiel übernehmen')
  })

  it('tells of a job that stays paused in its program', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'paused', kind: 'audio', values: { client: 'SABnzbd' } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Im Programm angehalten')
    expect(plain(card(1).textContent)).toContain('SABnzbd hat diesen Auftrag seit Stunden angehalten.')
    // The program names no reason: the card says so, and offers to block the release or to cancel.
    expect(plain(card(1).querySelector('[data-testid="paused-why"]')!.textContent)).toBe('Das Programm nennt keinen Grund.')
    expect([...card(1).querySelectorAll('[data-act]')].map((item) => item.getAttribute('data-act'))).toEqual(['block', 'cancel'])
  })

  it('words the reasons a program gives for a paused job, and shows an unknown one as it is written', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'paused', kind: 'audio', values: { why: ['DUPLICATE', 'TOO LARGE', 'PROPAGATING 3 min', 'queue_paused', 'SOMETHING NEW'] } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    const lines = [...card(1).querySelectorAll('[data-testid="paused-why"] li')].map((item) => plain(item.textContent))
    expect(lines).toEqual([
      'Das Programm hält den Auftrag für einen doppelten, es kennt ihn schon.',
      'Der Auftrag ist größer als die Grenze, die im Programm eingestellt ist.',
      'Die Beiträge sind noch zu neu für die News-Server. Das Programm wartet, bis sie überall liegen.',
      'Das Programm hat die ganze Warteschlange angehalten, nicht nur diesen Auftrag.',
      'Das Programm nennt: SOMETHING NEW.',
    ])
  })

  it('blocks the release of a paused job after asking, which cancels the job in its program', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'paused', kind: 'audio', values: { why: ['ENCRYPTED'] } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/1/cancel': { id: 1, state: 'removed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(card(1).querySelector('[data-act="block"]'))
    // Nothing is sent before the question is answered.
    expect(server.called('POST', '/api/v1/procure/downloads/1/cancel')).toBeUndefined()
    expect(page.dialog!.textContent).toContain('Der Auftrag wird im Programm abgebrochen und seine Dateien dort gelöscht.')
    expect(page.dialog!.textContent).toContain('Entsperren kannst du es später in der Sperrliste.')
    await page.inDialog('Sperren und weitersuchen')
    expect(server.called('POST', '/api/v1/procure/downloads/1/cancel')!.body).toEqual({ block: true })
  })

  it('opens the question to cancel a paused job', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'paused', kind: 'audio', values: { why: [] } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(card(1).querySelector('[data-act="cancel"]'))
    expect(page.dialog!.querySelector('[data-act="cancel-only"]')).not.toBeNull()
    expect(server.called('POST', '/api/v1/procure/downloads/1/cancel')).toBeUndefined()
  })

  it('asks when nothing proves the version of a recording, shows only what it knows and keeps it as unabridged', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'unproven', loaded: 15120 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/1/decide': { id: 1, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Fassung nicht belegt')
    expect(plain(card(1).querySelector('h3')!.textContent)).toBe('Das Hörbuch dauert 4 Std 12 Min. nexbooks kann nicht prüfen, ob es ungekürzt ist.')
    expect(plain(card(1).querySelector('.pb-text')!.textContent)).toContain('Ohne eine Laufzeit aus einem Katalog')
    // No catalogue names a running time and no page is known: the card says so, as a second line under the bar.
    expect([...card(1).querySelectorAll('.rt small')].map((item) => item.textContent)).toEqual(['Geladen', 'Erwartet: unbekannt, kein Katalog nennt eine Laufzeit'])
    expect(card(1).querySelector('[data-testid="runtime-estimate"]')).toBeNull()
    expect(card(1).querySelector('[data-act="keep"]')!.textContent).toBe('Als ungekürzt übernehmen')
    await page.click(card(1).querySelector('[data-act="keep"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/1/decide')!.body).toEqual({ action: 'keep' })
    // Blocking it asks first, as for every question.
    await page.click(card(1).querySelector('[data-act="block"]'))
    expect(page.dialog!.textContent).toContain('Das geladene Hörbuch wird gelöscht und das Release gesperrt.')
  })

  it('shows a rough running time from the pages of the book when no catalogue names one', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'unproven', loaded: 3480, pages: 400, estimate: 40800 } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect([...card(1).querySelectorAll('.rt small')].map((item) => item.textContent)).toEqual(['Geladen', 'Geschätzt aus 400 Seiten'])
    const line = card(1).querySelector('[data-testid="runtime-estimate"]')!
    expect(plain(line.querySelector('b')!.textContent)).toBe('ca. 11 Std 20 Min')
    // The estimate is the longer bar, the recording that came the shorter one.
    const widths = [...card(1).querySelectorAll<HTMLElement>('.rt i')].map((item) => item.style.width)
    expect(widths).toEqual(['9%', '100%'])
    expect(card(1).querySelector('[data-testid="runtime-unknown"]')).toBeNull()
  })

  it('leaves the line of the estimate out where a catalogue names the unabridged time', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'shorter', loaded: 37080, unabridged: 42240 } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(1).querySelector('[data-testid="runtime-estimate"]')).toBeNull()
    expect(card(1).querySelector('[data-testid="runtime-unknown"]')).toBeNull()
  })

  it('shows the catalogue time and not the estimate when both are sent, and says nothing of unknown beside an abridged time', async () => {
    serve({
      'GET /api/v1/procure/problems': {
        problems: [
          problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'shorter', loaded: 37080, unabridged: 42240, pages: 400, estimate: 40800 } }),
          problem(2, { code: 'runtime', kind: 'audio', values: { reason: 'abridged', loaded: 15120, abridged: 14940 } }),
        ],
        foreign: [],
        notes: [],
      },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(1).querySelector('[data-testid="runtime-estimate"]')).toBeNull()
    expect([...card(2).querySelectorAll('.rt small')].map((item) => item.textContent)).toEqual(['Geladen', 'Gekürzt laut Katalog'])
    expect(card(2).querySelector('[data-testid="runtime-unknown"]')).toBeNull()
  })

  it('marks a recording far longer than the catalogue as not fitting, not as an abridged one', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'too_long', loaded: 90000, unabridged: 34380 } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(1).querySelector('.rchip')!.textContent)).toBe('Laufzeit passt nicht')
  })

  it('blocks a question only after the question, and sends nothing when the person says no', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(1, { code: 'runtime', kind: 'audio', values: { reason: 'abridged', loaded: 100, unabridged: 300 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/1/decide': { id: 1, state: 'removed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(card(1).querySelector('[data-act="block"]'))
    expect(page.dialog!.textContent).toContain('Das geladene Hörbuch wird gelöscht und das Release gesperrt.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(card(1).querySelector('[data-act="block"]'))
    await page.inDialog('Sperren und weitersuchen')
    expect(server.called('POST', '/api/v1/procure/downloads/1/decide')!.body).toEqual({ action: 'block' })
  })

  it('offers the books of a bundle and keeps the one picked', async () => {
    const units = [
      { unit: 'a'.repeat(16), title: 'Der Anfang', authors: ['Rune Falkner'], volume: 1, series: 'Nebelchor', format: 'epub', files: 1 },
      { unit: 'b'.repeat(16), title: 'Die Laternenstraße', authors: ['Rune Falkner'], volume: 2, series: 'Nebelchor', format: 'epub', files: 1 },
    ]
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(2, { code: 'bundle', values: { units, wanted: 2 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/2/decide': { id: 2, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(2).querySelector('h3')!.textContent)).toBe('Das Paket enthält 2 Bücher. Welches ist das richtige?')
    expect(plain(card(2).querySelector('.pb-text')!.textContent)).toBe('Gewünscht ist Band 2. Die anderen wirft nexbooks weg.')
    // The wanted volume is chosen first.
    expect(card(2).querySelector('[data-act="pick"]')!.textContent).toBe('Band 2 behalten')
    await page.click(card(2).querySelectorAll<HTMLInputElement>('input[type="radio"]')[0])
    expect(card(2).querySelector('[data-act="pick"]')!.textContent).toBe('Band 1 behalten')
    await page.click(card(2).querySelector('[data-act="pick"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/2/decide')!.body).toEqual({ action: 'pick', unit: 'a'.repeat(16) })
    // Throwing the bundle away asks first.
    await page.click(card(2).querySelector('[data-act="block"]'))
    expect(page.dialog!.textContent).toContain('nexbooks löscht das ganze Paket und sperrt das Release.')
  })

  it('lets a person say that the folders of a release are the parts of one book', async () => {
    const units = [
      { unit: 'a'.repeat(16), title: 'Die Laternenstraße', authors: ['Rune Falkner'], volume: null, series: '', format: 'mp3', files: 3 },
      { unit: 'b'.repeat(16), title: 'Die Laternenstraße', authors: ['Rune Falkner'], volume: null, series: '', format: 'mp3', files: 3 },
    ]
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(2, { code: 'bundle', values: { units, wanted: null, whole: true } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/2/decide': { id: 2, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(2).querySelector('.pb-text')!.textContent)).toContain('Das können die Teile eines Hörbuchs sein')
    expect(card(2).querySelector('[data-act="whole"]')!.textContent).toBe('Alles ist ein Buch')
    await page.click(card(2).querySelector('[data-act="whole"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/2/decide')!.body).toEqual({ action: 'whole' })
  })

  it('offers no answer of one book where the question does not', async () => {
    const units = [{ unit: 'a'.repeat(16), title: 'Der Anfang', authors: ['Rune Falkner'], volume: 1, series: 'Nebelchor', format: 'epub', files: 1 }]
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(2, { code: 'bundle', values: { units, wanted: 2 } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(2).querySelector('[data-act="whole"]')).toBeNull()
  })

  it('asks about a reading whose genre says radio play, and keeps it as the unabridged reading', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'runtime', values: { reason: 'genre', loaded: 36000, unabridged: 36000 } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/5/decide': { id: 5, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe('Das Hörbuch dauert 10 Std 0 Min und trägt das Genre Hörspiel.')
    expect(card(5).querySelector('[data-act="keep"]')!.textContent).toBe('Als ungekürzt übernehmen')
    await page.click(card(5).querySelector('[data-act="keep"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/5/decide')!.body).toEqual({ action: 'keep' })
  })

  it('says what stays out when a question is answered', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(4, { code: 'runtime', values: { reason: 'shorter', loaded: 3000, unabridged: 3600, left_out: [{ title: 'Interview mit dem Autor', files: 1 }, { title: '', files: 2 }] } })], foreign: [], notes: [] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(4).querySelector('[data-left-out]')!.textContent)).toBe('Nicht dabei: Interview mit dem Autor, kein Titel. Das wirft nexbooks weg, wenn du es übernimmst.')
  })

  it('says nothing of what stays out when all of it goes in', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(4, { code: 'runtime', values: { reason: 'shorter', loaded: 3000, unabridged: 3600 } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(4).querySelector('[data-left-out]')).toBeNull()
  })

  it('says what the files say of a book it does not recognise, and files them when asked', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(3, { code: 'files_unassigned', values: { title: 'Ein Titel aus dem Verlag', authors: ['Rune Falkner'] } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/3/decide': { id: 3, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(3).textContent)).toContain('In den Dateien steht: Ein Titel aus dem Verlag · Rune Falkner')
    await page.click(card(3).querySelector('[data-act="take"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/3/decide')!.body).toEqual({ action: 'take' })
  })

  it('lays a file whose package names another language than its text before the person, and files it when asked', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'es', text: 'de', name: 'de', wanted: 'de' } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/5/decide': { id: 5, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe('Die Datei sagt Spanisch, der Text liest sich wie Deutsch.')
    expect(plain(card(5).querySelector('[data-testid="language-unclear"]')!.textContent)).toBe('dc:language = es Der Text liest sich wie Deutsch. Der Name des Releases sagt Deutsch.')
    expect(card(5).querySelector('[data-act="block"]')).not.toBeNull()
    await page.click(card(5).querySelector('[data-act="take"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/5/decide')!.body).toEqual({ action: 'take' })
  })

  it.each([
    ['mixed', 'Der Text ist in zwei Sprachen.'],
    ['other', 'Der Text ist in einer Sprache, die nexbooks nicht erkennt.'],
  ])('says of a text in two languages or in one it does not know what it is (%s)', async (text, said) => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'de', text, name: '', wanted: 'de' } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('[data-testid="language-unclear"]')!.textContent)).toBe(`dc:language = de ${said}`)
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe(said.replace('Der Text ist', 'Der Text des Buchs ist'))
  })

  it('says in plain sentences what happened when the file names English and the text reads German, and nothing in the release names a language', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'en', text: 'de', name: '', folder: '', wanted: 'de' } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe('Die Datei sagt Englisch, der Text liest sich wie Deutsch.')
    expect(plain(card(5).querySelector('.pb-text')!.textContent)).toBe(
      'Gesucht wurde auf Deutsch. Der Name des Releases nannte keine Sprache. Oft ist nur die Angabe in der Datei falsch. Sieh ins Buch: Ist es das richtige Buch auf Deutsch, ordne es ein. Sonst sperre das Release, dann sucht nexbooks ein anderes.',
    )
  })

  it('names the release when it names a language, and says the same in English', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'en', text: 'de', name: 'fr', wanted: 'de' } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('.pb-text')!.textContent)).toContain('Der Name des Releases nennt Französisch.')
    page.close()
    await changeLanguage('en', false)
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe('The file says English, but the text reads like German.')
    expect(plain(card(5).querySelector('.pb-text')!.textContent)).toContain('The search was for German. The name of the release says French. Often only the entry in the file is wrong.')
  })

  it('tells what disagrees when the file is not against the text, and which one speaks when only one does', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'de', text: '', name: 'en', folder: '', wanted: 'de' } }), problem(6, { code: 'language_unclear', values: { file: '', text: 'en', name: '', folder: '', wanted: 'de' } })], foreign: [], notes: [] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(5).querySelector('h3')!.textContent)).toBe('Die Hinweise auf die Sprache widersprechen sich.')
    expect(plain(card(6).querySelector('h3')!.textContent)).toBe('Nur der Text nennt eine Sprache: Englisch. Gesucht wurde auf Deutsch.')
  })

  const LOOK = {
    download_id: 5, kind: 'ebook', folder: '',
    files: [{ id: 51, name: 'Falkner, Rune - Laternenstrasse.epub', folder: '', format: 'epub', size: 1000, part: 1, duration: 0, pages: 120, chapters: 3, protected: false }],
    says: { title: 'Die Laternenstrasse', subtitle: '', authors: ['Rune Falkner'], narrators: [], series: '', series_pos: null, isbn: '', asin: '', publisher: '', year: 2019, language: 'en', text_language: 'de', genre: '', subjects: [], pages: 120, pages_counted: false, duration: 0, chapters: 3 },
    chapters: [],
  }

  it('lets a person look into the files of an e-book that waits for an answer: read it, and see what the files say', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'en', text: 'de', name: '', wanted: 'de' } })], foreign: [], notes: [] },
      'GET /api/v1/procure/downloads/5/look': LOOK,
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(5).querySelector('[data-testid="look-read"]')).not.toBeNull()
    expect(card(5).querySelector('[data-testid="look-listen"]')).toBeNull()
    await page.click(card(5).querySelector('[data-testid="look-facts"]'))
    await eventually(() => expect(card(5).querySelector('[data-testid="look-facts-panel"]')).not.toBeNull())
    const panel = plain(card(5).querySelector('[data-testid="look-facts-panel"]')!.textContent)
    expect(panel).toContain('Falkner, Rune - Laternenstrasse.epub')
    expect(panel).toContain('oberste Ebene des Downloads')
    expect(panel).toContain('EN')
    // Looking changes nothing: nothing was sent but the questions.
    expect(server.changes()).toEqual([])
  })

  it('reads the first pages from the file of the download, not from a file of a library', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'en', text: 'de', name: '', wanted: 'de' } })], foreign: [], notes: [] },
      'GET /api/v1/procure/downloads/5/look': LOOK,
      'GET /api/v1/procure/downloads/5/files/51/content': {},
    })
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(card(5).querySelector('[data-testid="look-read"]'))
    await eventually(() => expect(document.querySelector('[data-testid="peek-dialog"]')).not.toBeNull())
    await eventually(() => expect(server.called('GET', '/api/v1/procure/downloads/5/files/51/content')).toBeTruthy())
    expect(server.calls.some((call) => /\/api\/v1\/files\/\d+\/content/.test(call.url))).toBe(false)
  })

  it('says so when the files of the download are not there to look at any more', async () => {
    serve({ 'GET /api/v1/procure/problems': { problems: [problem(5, { code: 'language_unclear', values: { file: 'en', text: 'de', name: '', wanted: 'de' } })], foreign: [], notes: [] } })
    server.fails = { 'GET /api/v1/procure/downloads/5/look': { status: 404, code: 'not_found' } }
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(card(5).querySelector('[data-testid="look-facts"]'))
    await eventually(() => expect(card(5).querySelector('[data-testid="look-gone"]')).not.toBeNull())
    expect(plain(card(5).querySelector('[data-testid="look-gone"]')!.textContent)).toBe('Die Dateien dieses Downloads sind nicht mehr zu sehen.')
  })

  it('offers listening, not reading, for a recording, and asks the files of the download for it', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(6, { kind: 'audio', code: 'runtime', values: { reason: 'abridged', loaded: 60, unabridged: 180, abridged: 60 } })], foreign: [], notes: [] },
      'GET /api/v1/procure/downloads/6/look': { ...LOOK, download_id: 6, kind: 'audio', files: [{ ...LOOK.files[0], id: 61, name: '01.mp3', format: 'mp3', part: 1, duration: 60 }], says: { ...LOOK.says, duration: 60 } },
    })
    vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) => (type.startsWith('audio/mpeg') ? 'probably' : ''))
    await page.show(app(), '/acquire?reiter=needs')
    expect(card(6).querySelector('[data-testid="look-read"]')).toBeNull()
    await page.click(card(6).querySelector('[data-testid="look-listen"]'))
    await eventually(() => expect((document.querySelector('[role="dialog"] [data-testid="peek-play"]') as HTMLButtonElement).disabled).toBe(false))
    expect(server.called('GET', '/api/v1/procure/downloads/6/look')).toBeTruthy()
    expect(server.changes()).toEqual([])
  })

  it('asks about a recording nothing names the language of, and files it when asked', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(6, { kind: 'audio', code: 'language_unknown', values: { file: '', text: '', name: '', folder: '', wanted: 'de' } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/6/decide': { id: 6, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(6).querySelector('h3')!.textContent)).toBe('Nichts sagt, in welcher Sprache das Buch ist.')
    expect(card(6).querySelector('[data-act="block"]')).not.toBeNull()
    await page.click(card(6).querySelector('[data-act="take"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/6/decide')!.body).toEqual({ action: 'take' })
  })

  it('says which language the file is in, and the note goes when it is read', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(4, { code: 'language', values: { file: 'en', wanted: 'de' } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/4/decide': { id: 4, state: 'rejected' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(4).querySelector('h3')!.textContent)).toBe('Die Datei ist auf Englisch. Gewünscht war Deutsch.')
    expect(plain(card(4).textContent)).toContain('dc:language = en')
    expect(card(4).querySelector('[data-act="releases"]')).not.toBeNull()
    await page.click(card(4).querySelector('[data-act="ok"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/4/decide')!.body).toEqual({ action: 'acknowledge' })
  })

  it('tells a person their place was carried over by its share, and brings the old format back only after the question', async () => {
    const note = { id: 9, code: 'upgrade_position', book: problem(1).book, edition_id: 3, detail: { from: 'pdf', to: 'epub', fraction: 0.46, page: 142, pages: 310 }, can_restore: true, release: 'Rune.Falkner.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE', at: new Date().toISOString() }
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] },
      'POST /api/v1/procure/notes/9/acknowledge': 204,
      'POST /api/v1/procure/notes/9/restore': 204,
    })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="9"]')!
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('Das E-Book ist jetzt ein EPUB statt PDF. Dein Lesestand ist nur ungefähr übertragen.')
    expect(plain(shown.textContent)).toContain('Du warst bei Seite 142.')
    expect(plain(shown.textContent)).toContain('Seite 142 von 310')
    expect(plain(shown.textContent)).toContain('etwa 46 %')
    // The release the new format came with is named, as the model does.
    expect(plain(shown.querySelector('.rel')!.textContent)).toBe('Rune.Falkner.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE')
    await page.click(shown.querySelector('[data-act="restore"]'))
    expect(page.dialog!.textContent).toContain('nexbooks holt die PDF aus dem Papierkorb zurück und legt das EPUB dort hinein.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(shown.querySelector('[data-act="restore"]'))
    await page.inDialog('PDF zurückholen')
    await page.click(document.querySelector('[data-note="9"] [data-act="ok"]'))
    expect(server.changes()).toEqual(['POST /api/v1/procure/notes/9/restore', 'POST /api/v1/procure/notes/9/acknowledge'])
  })

  it('says "a MOBI" and "an EPUB" in English, as one says them', async () => {
    await changeLanguage('en', false)
    const note = { id: 9, code: 'upgrade_position', book: problem(1).book, edition_id: 3, detail: { from: 'mobi', to: 'epub', fraction: 0.2, page: null, pages: null }, can_restore: false, release: '', at: new Date().toISOString() }
    const other = { ...note, id: 10, detail: { from: 'pdf', to: 'mobi', fraction: 0.5, page: 12, pages: 100 } }
    serve({ 'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note, other] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(document.querySelector('[data-note="9"] h3')!.textContent)).toBe('The e-book is an EPUB now instead of MOBI. Your reading position was carried over roughly.')
    expect(plain(document.querySelector('[data-note="10"] .pb-text')!.textContent)).toBe('A PDF has fixed pages, a MOBI does not. You were on page 12. nexbooks set you to about 50 %, a position in flowing text, not an exact one.')
    expect(document.querySelector('[data-note="9"] .rel')).toBeNull()
  })

  it('tells a person the program put the book into a shared folder, in both languages, and the note goes once read', async () => {
    const note = { id: 11, code: 'folder_kept', book: problem(1).book, edition_id: 3, detail: { client: 'SABnzbd' }, can_restore: false, release: 'Rune.Falkner.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE', at: new Date().toISOString() }
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] },
      'POST /api/v1/procure/notes/11/acknowledge': 204,
    })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="11"]')!
    expect(plain(shown.querySelector('.rchip')!.textContent)).toBe('Ordner nicht aufgeräumt')
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('SABnzbd hat das Buch in einem Ordner abgelegt, den es mit anderen Downloads teilt.')
    expect(plain(shown.querySelector('.pb-text')!.textContent)).toContain('in diesem Ordner nichts gelöscht')
    expect(shown.querySelector('[data-act="restore"]')).toBeNull()
    expect(plain(shown.textContent)).not.toContain('Lesestand')
    await page.click(shown.querySelector('[data-act="ok"]'))
    expect(server.changes()).toEqual(['POST /api/v1/procure/notes/11/acknowledge'])
    await changeLanguage('en', false)
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(document.querySelector('[data-note="11"] h3')!.textContent)).toBe('SABnzbd put the book into a folder it shares with other downloads.')
  })

  it.each([
    [31, 'mail', 'Der Mailserver hat die Mail auch nach mehreren Versuchen nicht angenommen.', 'The mail server did not take the mail, even after several tries.'],
    [32, 'no_address', 'Deine Kindle-Adresse war weg', 'Your Kindle address was gone'],
    [33, 'off', 'Der Betreiber hat E-Reader ausgeschaltet', 'The operator switched e-readers off'],
    [34, 'not_sendable', 'Die Datei ließ sich nicht mehr senden.', 'The file could not be sent any more.'],
    [35, 'unreadable', 'Die Datei ließ sich nicht lesen.', 'The file could not be read.'],
  ])('tells a person a mail to their Kindle that never went, with the reason (%s), and the note goes once read', async (id, reason, german, english) => {
    const note = { id, code: 'kindle_failed', book: problem(1).book, edition_id: 3, detail: { reason }, can_restore: false, release: '', at: new Date().toISOString() }
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] },
      [`POST /api/v1/procure/notes/${id}/acknowledge`]: 204,
    })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector(`[data-note="${id}"]`)!
    expect(plain(shown.querySelector('.rchip')!.textContent)).toBe('Kindle')
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('Das Buch ist nicht auf deinem Kindle angekommen')
    expect(plain(shown.querySelector('.pb-text')!.textContent)).toContain(german)
    expect(shown.querySelector('[data-act="restore"]')).toBeNull()
    expect(plain(shown.textContent)).not.toMatch(/[–—]/)
    await page.click(shown.querySelector('[data-act="ok"]'))
    expect(server.changes()).toEqual([`POST /api/v1/procure/notes/${id}/acknowledge`])
    await changeLanguage('en', false)
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(document.querySelector(`[data-note="${id}"] h3`)!.textContent)).toBe('The book did not reach your Kindle')
    expect(plain(document.querySelector(`[data-note="${id}"] .pb-text`)!.textContent)).toContain(english)
  })

  it('takes a reason it does not know for a mail that did not go', async () => {
    const note = { id: 36, code: 'kindle_failed', book: problem(1).book, edition_id: 3, detail: { reason: 'something new' }, can_restore: false, release: '', at: new Date().toISOString() }
    serve({ 'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] } })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(document.querySelector('[data-note="36"] .pb-text')!.textContent)).toContain('Mailserver')
  })

  it('gives a job nobody asked for to an open wish', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [foreign(7)], notes: [] },
      'GET /api/v1/procure/wishes': { wishes: [wish(11, { state: 'not_found' }), wish(12, { state: 'loading' })] },
      'POST /api/v1/procure/foreign/7/assign': { id: 30, state: 'queued' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    await page.click(document.querySelector('[data-foreign="7"] [data-act="assign"]'))
    // A wish whose release runs already is not offered.
    expect(page.dialog!.querySelectorAll('.opt').length).toBe(1)
    await page.click(page.dialog!.querySelector('[data-act="assign-go"]'))
    expect(server.called('POST', '/api/v1/procure/foreign/7/assign')!.body).toEqual({ wish_id: 11 })
  })

  it.each([
    ['edition', 'Ausgabe mit Zusätzen', 'In den Dateien steht: Die Laternenstraße (Schulausgabe mit Materialien)'],
    ['partial', 'Vielleicht nur ein Teil', 'In den Dateien steht: Die Laternenstraße, Teil 1'],
  ])('asks about files that are a special edition or a part (%s), and files them as the book when asked', async (code, label, says) => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(8, { code, values: { title: says.replace('In den Dateien steht: ', ''), authors: ['Rune Falkner'] } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/8/decide': { id: 8, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(8).querySelector('.rchip')!.textContent)).toBe(label)
    expect(plain(card(8).querySelector('[data-testid="special-says"]')!.textContent)).toBe(says)
    expect(card(8).querySelector('[data-act="block"]')).not.toBeNull()
    await page.click(card(8).querySelector('[data-act="take"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/8/decide')!.body).toEqual({ action: 'take' })
  })

  it('asks about files that name another author than the release, and blocks only after the confirmation', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(9, { code: 'pen_name', values: { found: ['Ida Leser'], wanted: ['Rune Falkner'], title: 'Die Laternenstraße' } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/9/decide': { id: 9, state: 'rejected' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(9).querySelector('[data-testid="pen-name-says"]')!.textContent)).toBe('In den Dateien: Ida Leser · gesucht: Rune Falkner')
    expect(plain(card(9).querySelector('[data-act="take"]')!.textContent)).toBe('Das ist das Buch, einordnen')
    await page.click(card(9).querySelector('[data-act="block"]'))
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(card(9).querySelector('[data-act="block"]'))
    await page.inDialog('Sperren und weitersuchen')
    expect(server.called('POST', '/api/v1/procure/downloads/9/decide')!.body).toEqual({ action: 'block' })
  })

  it('asks whether a better format is the same edition, replaces it or keeps both, and blocks only after the question', async () => {
    serve({
      'GET /api/v1/procure/problems': { problems: [problem(12, { code: 'same_edition', values: { have: 'mobi', found: 'epub', title: 'Die Laternenstraße', authors: ['Rune Falkner'] } })], foreign: [], notes: [] },
      'POST /api/v1/procure/downloads/12/decide': { id: 12, state: 'completed' },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(plain(card(12).querySelector('.rchip')!.textContent)).toBe('Dieselbe Ausgabe?')
    expect(plain(card(12).querySelector('.pb-text')!.textContent)).toContain('Im Regal liegt das Buch als MOBI, geladen wurde EPUB.')
    expect(plain(card(12).querySelector('[data-act="take"]')!.textContent)).toBe('Das ist dieselbe Ausgabe, ersetzen')
    expect(plain(card(12).querySelector('[data-act="keep"]')!.textContent)).toBe('Das ist eine andere Ausgabe, beide behalten')
    await page.click(card(12).querySelector('[data-act="block"]'))
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(card(12).querySelector('[data-act="keep"]'))
    expect(server.called('POST', '/api/v1/procure/downloads/12/decide')!.body).toEqual({ action: 'keep' })
  })

  it('tells the person when the scan put an edition into another book, and splits it only after the question', async () => {
    const note = { id: 21, code: 'works_joined', book: problem(1).book, edition_id: 7, detail: { title: 'Laternenstraße', into: 'Die Laternenstraße' }, can_restore: false, release: '', at: new Date().toISOString() }
    serve({
      'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] },
      'POST /api/v1/editions/7/split': { work_id: 9 },
      'POST /api/v1/procure/notes/21/acknowledge': 204,
    })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="21"]')!
    expect(plain(shown.querySelector('.rchip')!.textContent)).toBe('Zusammengelegt')
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('„Laternenstraße“ steht jetzt bei „Die Laternenstraße“.')
    expect(shown.querySelector('[data-act="restore"]')).toBeNull()
    await page.click(shown.querySelector('[data-act="split"]'))
    expect(page.dialog!.textContent).toContain('„Laternenstraße“ wird wieder ein eigenes Buch')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(shown.querySelector('[data-act="split"]'))
    await page.inDialog('Trennen')
    expect(server.called('POST', '/api/v1/editions/7/split')).toBeTruthy()
    expect(server.called('POST', '/api/v1/procure/notes/21/acknowledge')).toBeTruthy()
  })

  it('names an edition the library holds twice, removes nothing and is read with one click', async () => {
    const note = { id: 23, code: 'duplicate', book: problem(1).book, edition_id: 8, detail: { why: 'contains', title: 'Moorland Box', other_title: 'Die Nebelwacht', here: 'Ada Feldkamp/Moorland Box', there: 'Ada Feldkamp/Die Nebelwacht', other_edition_id: 7, other_work_id: 4 }, can_restore: false, release: '', at: new Date().toISOString() }
    serve({ 'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] }, 'POST /api/v1/procure/notes/23/acknowledge': 204 })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="23"]')!
    expect(plain(shown.querySelector('.rchip')!.textContent)).toBe('Doppelt')
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('Diese Fassung enthält „Die Nebelwacht“ noch einmal.')
    expect(plain(shown.textContent)).toContain('Ada Feldkamp/Moorland Box · Ada Feldkamp/Die Nebelwacht')
    expect(plain(shown.textContent)).toContain('nexbooks löscht davon nichts')
    expect(server.changes()).toEqual([])
    await page.click(shown.querySelector('[data-act="ok"]'))
    expect(server.changes()).toEqual(['POST /api/v1/procure/notes/23/acknowledge'])
  })

  it('lets a person choose which of two different files goes, after a question, and offers no third way', async () => {
    const note = { id: 24, code: 'duplicate', book: problem(1).book, edition_id: 8, detail: { why: 'same_format', title: 'Die Nebelwacht', other_title: 'Die Nebelwacht', here: 'Ada Feldkamp/Nebelwacht (2008)', there: 'Ada Feldkamp/Nebelwacht (2018)' } }
    serve({ 'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] }, 'POST /api/v1/procure/notes/24/drop': 204 })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="24"]')!
    const buttons = [...shown.querySelectorAll('[data-act="drop"]')]
    expect(buttons.map((button) => plain(button.textContent))).toEqual(['„Nebelwacht (2008)“ löschen', '„Nebelwacht (2018)“ löschen'])
    expect(shown.querySelector('[data-act="ok"]')).toBeNull()
    await page.click(buttons[1])
    expect(server.changes()).toEqual([])
    await page.inDialog('In den Papierkorb')
    expect(server.called('POST', '/api/v1/procure/notes/24/drop')?.body).toEqual({ folder: 'Ada Feldkamp/Nebelwacht (2018)' })
  })

  it('tells the person who wished it that the old format went into the trash, also without a place in it', async () => {
    const note = { id: 22, code: 'upgrade_replaced', book: problem(1).book, edition_id: 3, detail: { from: 'mobi', to: 'epub' }, can_restore: true, release: 'Rune.Falkner.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE', at: new Date().toISOString() }
    serve({ 'GET /api/v1/procure/problems': { problems: [], foreign: [], notes: [note] }, 'POST /api/v1/procure/notes/22/restore': 204 })
    await page.show(app(), '/acquire?reiter=needs')
    const shown = document.querySelector('[data-note="22"]')!
    expect(plain(shown.querySelector('h3')!.textContent)).toBe('Das Buch liegt jetzt als EPUB vor, nicht mehr als MOBI.')
    await page.click(shown.querySelector('[data-act="restore"]'))
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
  })

  it('asks nobody about a wish whose releases only have doubts', async () => {
    serve({
      'GET /api/v1/procure/wishes': { wishes: [wish(21, { state: 'not_found', detail: { total: 2, look: 2, reasons: [{ code: 'maybe_other_volume', count: 2 }] } }), wish(22, { state: 'not_found', detail: { total: 1, reasons: [{ code: 'sample', count: 1 }] } })] },
    })
    await page.show(app(), '/acquire?reiter=needs')
    expect(document.querySelector('[data-code="look"]')).toBeNull()
  })
})

describe('the history of the filing', () => {
  it('names an edition the scan put into a book, with "Trennen" only for who may and after the question', async () => {
    serve({
      'GET /api/v1/procure/history': {
        entries: [
          line(5, { result: 'joined', release: '', work_id: 4, note: { edition_id: 7, title: 'Laternenstraße' }, can_split: true }),
          line(6, { result: 'joined', release: '', work_id: 4, note: { edition_id: 8, title: 'Nebelchor' }, can_split: false }),
        ],
      },
      'POST /api/v1/editions/7/split': { work_id: 9 },
    })
    await page.show(app(), '/acquire?reiter=history')
    const rows = [...document.querySelectorAll('.hr[data-result="joined"]')]
    expect(rows).toHaveLength(2)
    expect(plain(rows[0].querySelector('.hres')!.textContent)).toBe('zusammengelegt„Laternenstraße“ steht jetzt bei diesem Buch')
    expect(rows[1].querySelector('[data-act="split"]')).toBeNull()
    await page.click(rows[0].querySelector('[data-act="split"]'))
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(rows[0].querySelector('[data-act="split"]'))
    await page.inDialog('Trennen')
    expect(server.called('POST', '/api/v1/editions/7/split')).toBeTruthy()
  })

  it('says what an upgrade replaced and why a release was turned down', async () => {
    serve({
      'GET /api/v1/procure/history': {
        entries: [
          line(1, { result: 'done', note: { library: 'Hörbücher', upgrade: { from: 'mp3_128', to: 'flac' } } }),
          line(2, { result: 'rejected', note: { code: 'other_language', file: 'en', wanted: 'de' } }),
          line(3, { result: 'done', note: { library: 'Bücher' } }),
        ],
      },
    })
    await page.show(app(), '/acquire?reiter=history')
    const rows = [...document.querySelectorAll('.hr')].map((row) => plain(row.textContent))
    expect(rows[0]).toContain('Upgrade eingespielt')
    expect(rows[0]).toContain('ersetzt MP3 128 durch FLAC, das alte liegt im Papierkorb')
    expect(rows[1]).toContain('Sprache in der Datei: Englisch, gewünscht Deutsch')
    expect(rows[2]).toContain('eingeordnet in Bücher')
  })

  it('names what was wished beside what went in, and marks a book that is not the one wished', async () => {
    const wished = (title: string) => ({ title, authors: ['Rune Falkner'], kind: 'ebook' as const, language: 'de' })
    serve({
      'GET /api/v1/procure/history': {
        entries: [
          line(1, { book_title: 'Wir wollen Ruß sehen', wished: wished('Ruß'), filed_as: 'Wir wollen Ruß sehen', differs: true, note: { library: 'Bücher', other_book: true } }),
          line(2, { result: 'rejected', wished: wished('Ruß'), note: { code: 'title_mismatch', title: 'Wir wollen Ruß sehen' } }),
          line(3, { book_title: 'Die Brandung', wished: wished('Die Brandung'), filed_as: 'Die Brandung', differs: false, note: { library: 'Bücher' } }),
          line(4, { note: { library: 'Bücher' } }),
        ],
      },
    })
    await page.show(app(), '/acquire?reiter=history')
    const all = [...document.querySelectorAll('.hr')]
    const rows = all.map((row) => plain(row.textContent))
    expect(rows[0]).toContain('Gewünscht: Ruß · Rune Falkner')
    expect(rows[0]).toContain('anderes Buch als gewünscht')
    expect(rows[0]).toContain('eingeordnet als „Wir wollen Ruß sehen“ in Bücher, gewünscht war „Ruß“')
    expect(all[0].getAttribute('data-differs')).toBe('true')
    expect(all[0].classList.contains('differs')).toBe(true)
    expect(rows[1]).toContain('Gewünscht: Ruß · Rune Falkner')
    expect(rows[1]).toContain('Die Datei ist ein anderes Buch: Wir wollen Ruß sehen')
    expect(rows[2]).toContain('Gewünscht: Die Brandung · Rune Falkner')
    expect(rows[2]).toContain('eingeordnet als „Die Brandung“ in Bücher')
    expect(rows[2]).not.toContain('anderes Buch')
    expect(all[2].getAttribute('data-differs')).toBeNull()
    // A line from before there was a record of the wish names nothing it does not know.
    expect(rows[3]).not.toContain('Gewünscht')
    expect(rows[3]).toContain('eingeordnet in Bücher')
    for (const row of rows) expect(row).not.toMatch(/[–—]|\{\{/)
  })

  it('names what was wished in English too', async () => {
    await changeLanguage('en', false)
    serve({
      'GET /api/v1/procure/history': {
        entries: [line(1, { wished: { title: 'Ruß', authors: [], kind: 'ebook', language: 'de' }, filed_as: 'Wir wollen Ruß sehen', differs: true, note: { library: 'Books' } })],
      },
    })
    await page.show(app(), '/acquire?reiter=history')
    const row = plain(document.querySelector('.hr')!.textContent)
    expect(row).toContain('Wished: Ruß')
    expect(row).toContain('not the book wished')
    expect(row).toContain('filed as “Wir wollen Ruß sehen” into Books, the wish was “Ruß”')
  })
})
