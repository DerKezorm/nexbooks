/**
 * "Falsches Buch" on the page of a book: offered only for an edition nexbooks fetched (the server names the release), asks
 * first and says what happens and that it can be taken back, sends nothing before the one click, and says what became of the
 * book instead of closing silently.
 */
import type { EditionOut, WorkOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { ebook, edition, workOut } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { ManageDialog } from './ManageBook'

const server = new Server()
const page = new Page()

const delivery = { release: 'Mara.Lindqvist-Der.stille.Obstgarten-EPUB-DE-2021-EXAMPLE', indexer: 'Shelf', delivered_at: '2026-10-20T10:00:00Z', trash_days: 30 }

function open(over: Partial<EditionOut> = {}, handlers: { onChanged?: (workId: number, said: string) => void; onGone?: () => void; onClose?: () => void } = {}) {
  const work: WorkOut = workOut(1, [ebook(5)])
  const editions = [edition(ebook(5), { delivery, ...over })]
  return page.show(<ManageDialog work={work} editions={editions} onClose={handlers.onClose ?? (() => undefined)} onChanged={handlers.onChanged ?? (() => undefined)} onGone={handlers.onGone} />)
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'POST /api/v1/editions/5/wrong-book': { trash_id: 4, replacement: 'searching', code: '', work_id: 1, missing: [], people: 0 } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the wrong book', () => {
  it('is offered only for an edition that came from a release, and names the release', async () => {
    await open()
    const row = document.querySelector('[data-testid="wrong-book-row"]') as HTMLElement
    expect(plain(row.textContent)).toContain('Mara.Lindqvist-Der.stille.Obstgarten-EPUB-DE-2021-EXAMPLE')
    page.close()
    await open({ delivery: null })
    expect(document.querySelector('[data-testid="wrong-book-row"]')).toBeNull()
  })

  it('asks first, and sends nothing before the one click', async () => {
    await open()
    await page.click(page.button('Falsches Buch'))
    const question = plain(page.dialog!.textContent)
    expect(question).toContain('Dieses Buch wegschicken?')
    expect(question).toContain('Papierkorb')
    expect(question).toContain('Sperrliste')
    // How long the trash keeps it, and that everybody's place, marks and history are kept and come back.
    expect(question).toContain('Er hebt sie 30 Tage auf')
    expect(question).toContain('Lesestand, Lesezeichen und Verlauf aller')
    expect(question).toContain('kommen zurück')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    expect(page.dialog && plain(page.dialog.textContent)).not.toContain('Dieses Buch wegschicken?')
  })

  it('sends it away on the click and says what became of the book', async () => {
    const changed: [number, string][] = []
    await open({}, { onChanged: (workId, said) => changed.push([workId, said]) })
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    expect(server.changes()).toEqual(['POST /api/v1/editions/5/wrong-book'])
    const done = document.querySelector('[data-testid="wrong-done"]') as HTMLElement
    expect(done.getAttribute('data-replacement')).toBe('searching')
    expect(plain(done.textContent)).toContain('nexbooks sucht jetzt ein anderes Release')
    expect(plain(done.textContent)).toContain('Papierkorb')
    await page.click(page.button('Zur Seite des Buchs'))
    expect(changed).toEqual([[1, 'Die Fassung ist weg. nexbooks sucht jetzt ein anderes Release für das Buch.']])
  })

  it('goes to the shelf when the book went with its edition', async () => {
    server.answers['POST /api/v1/editions/5/wrong-book'] = { trash_id: 4, replacement: 'none', code: '', work_id: null, missing: [], people: 0 }
    let gone = 0
    let closed = 0
    await open({}, { onGone: () => (gone += 1), onClose: () => (closed += 1) })
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    expect(plain(document.querySelector('[data-testid="wrong-done"]')!.textContent)).toContain('Such es über Entdecken noch einmal')
    await page.click(page.button('Zum Regal'))
    expect([gone, closed]).toEqual([1, 1])
  })

  it('shows the reason and keeps the dialog when the server refuses', async () => {
    server.fails = { 'POST /api/v1/editions/5/wrong-book': { status: 409, code: 'not_fetched' } }
    await open()
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    expect(document.querySelector('[data-testid="wrong-done"]')).toBeNull()
    expect(page.dialog).toBeTruthy()
  })

  it('names the days the trash keeps it, in the singular too', async () => {
    await open({ delivery: { ...delivery, trash_days: 1 } })
    await page.click(page.button('Falsches Buch'))
    expect(plain(page.dialog!.textContent)).toContain('Er hebt sie 1 Tag auf')
  })

  it('says nothing is searched while fetching by itself is off', async () => {
    server.answers['POST /api/v1/editions/5/wrong-book'] = { trash_id: 4, replacement: 'idle', code: '', work_id: 1, missing: [], people: 0 }
    await open()
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    const done = plain(document.querySelector('[data-testid="wrong-done"]')!.textContent)
    expect(done).toContain('Das automatische Beschaffen ist aus')
    expect(done).toContain('Jetzt suchen')
    expect(done).not.toContain('sucht jetzt ein anderes Release')
  })

  it('says why no new search was started, in a sentence and not as a code', async () => {
    server.answers['POST /api/v1/editions/5/wrong-book'] = { trash_id: 4, replacement: 'failed', code: 'no_indexers', work_id: 1, missing: [], people: 0 }
    await open()
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    const done = plain(document.querySelector('[data-testid="wrong-done"]')!.textContent)
    expect(done).toContain('Es ist kein Indexer eingeschaltet.')
    expect(done).not.toContain('no_indexers')
  })

  it('names the files that were gone, the people whose places are kept, and the case that no file could go', async () => {
    server.answers['POST /api/v1/editions/5/wrong-book'] = {
      trash_id: null,
      replacement: 'searching',
      code: '',
      work_id: 1,
      missing: ['Autor/Buch/01.mp3', 'Autor/Buch/02.mp3'],
      people: 3,
    }
    await open()
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    expect(plain(document.querySelector('[data-testid="wrong-missing"]')!.textContent)).toContain('2 Dateien lagen nicht mehr auf der Platte')
    expect(plain(document.querySelector('[data-testid="wrong-missing"]')!.textContent)).toContain('Autor/Buch/01.mp3')
    expect(plain(document.querySelector('[data-testid="wrong-people"]')!.textContent)).toContain('von 3 Personen')
    const done = plain(document.querySelector('[data-testid="wrong-done"]')!.textContent)
    expect(done).toContain('Es lag keine Datei mehr da')
    expect(done).not.toContain('Die Dateien liegen im Papierkorb')
  })

  it('says nothing of files or people when there were none', async () => {
    await open()
    await page.click(page.button('Falsches Buch'))
    await page.inDialog('Wegschicken')
    expect(document.querySelector('[data-testid="wrong-missing"]')).toBeNull()
    expect(document.querySelector('[data-testid="wrong-people"]')).toBeNull()
  })
})
