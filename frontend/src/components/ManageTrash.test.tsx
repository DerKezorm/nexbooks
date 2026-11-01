/**
 * "Fassung in den Papierkorb" on the page of a book: offered only to who may (the server tells how long the trash keeps
 * it), asks first and says what happens and that it can be taken back, sends nothing before the one click, and says what
 * became of the book instead of closing silently.
 */
import type { EditionOut, WorkOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { ebook, edition, workOut } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { ManageDialog } from './ManageBook'

const server = new Server()
const page = new Page()

function open(over: Partial<EditionOut> = {}, handlers: { onChanged?: (workId: number, said: string) => void; onGone?: () => void; onClose?: () => void } = {}) {
  const work: WorkOut = workOut(1, [ebook(5)])
  const editions = [edition(ebook(5), { trash_days: 30, ...over })]
  return page.show(<ManageDialog work={work} editions={editions} onClose={handlers.onClose ?? (() => undefined)} onChanged={handlers.onChanged ?? (() => undefined)} onGone={handlers.onGone} />)
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'POST /api/v1/editions/5/trash': { trash_id: 4, work_id: 1, missing: [], people: 2 } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('an edition in the trash', () => {
  it('is offered only to who may, also for a book nexbooks did not fetch', async () => {
    await open({ delivery: null })
    expect(document.querySelector('[data-testid="trash-row"]')).not.toBeNull()
    page.close()
    await open({ trash_days: 0 })
    expect(document.querySelector('[data-testid="trash-row"]')).toBeNull()
  })

  it('asks first, and sends nothing before the one click', async () => {
    await open()
    await page.click(page.button('In den Papierkorb'))
    const question = plain(page.dialog!.textContent)
    expect(question).toContain('Diese Fassung in den Papierkorb legen?')
    expect(question).toContain('Er hebt sie 30 Tage auf')
    expect(question).toContain('kommen zurück')
    expect(question).not.toContain('gesperrt')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
  })

  it('puts it in the trash on the click and says what became of it', async () => {
    const changed: [number, string][] = []
    await open({}, { onChanged: (workId, said) => changed.push([workId, said]) })
    await page.click(page.button('In den Papierkorb'))
    await page.inDialog('In den Papierkorb')
    expect(server.changes()).toEqual(['POST /api/v1/editions/5/trash'])
    const done = plain(document.querySelector('[data-testid="trash-done"]')!.textContent)
    expect(done).toContain('Die Fassung ist aus dem Regal.')
    expect(done).toContain('von 2 Personen')
    expect(done).toContain('Papierkorb')
    await page.click(page.button('Zur Seite des Buchs'))
    expect(changed).toEqual([[1, 'Die Fassung ist aus dem Regal.']])
  })

  it('goes to the shelf when the book went with its edition', async () => {
    server.answers['POST /api/v1/editions/5/trash'] = { trash_id: 4, work_id: null, missing: [], people: 0 }
    let gone = 0
    await open({}, { onGone: () => (gone += 1) })
    await page.click(page.button('In den Papierkorb'))
    await page.inDialog('In den Papierkorb')
    expect(plain(document.querySelector('[data-testid="trash-done"]')!.textContent)).toContain('und das Buch mit ihr')
    await page.click(page.button('Zum Regal'))
    expect(gone).toBe(1)
  })

  it('keeps the dialog when the server refuses', async () => {
    server.fails = { 'POST /api/v1/editions/5/trash': { status: 409, code: 'trash_failed' } }
    await open()
    await page.click(page.button('In den Papierkorb'))
    await page.inDialog('In den Papierkorb')
    expect(document.querySelector('[data-testid="trash-done"]')).toBeNull()
    expect(page.dialog).toBeTruthy()
  })
})
