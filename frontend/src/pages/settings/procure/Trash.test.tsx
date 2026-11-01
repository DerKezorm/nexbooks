/**
 * The trash in the settings: the entries with their titles and how long they stay, bringing one back and
 * deleting one or all only after the question; the days are saved with the formats.
 */
import { changeLanguage } from '../../../i18n'
import { Page, Server, plain } from '../../../test/harness'
import { TrashCard } from './TrashCard'

const server = new Server()
const page = new Page()

const entry = (id: number, title: string) => ({ id, library: { id: 1, name: 'Bücher' }, work_id: 3, kind: 'ebook', title, author: 'Ada Feldkamp', format: 'pdf', files: 1, size: 2_000_000, created_at: '2026-10-01T10:00:00Z', expires_at: '2026-10-31T10:00:00Z' })

function serve(entries = [entry(1, 'Feldnotizen'), entry(2, 'Zweites Buch')]) {
  server.answers = {
    'GET /api/v1/procure/trash': { entries, days: 30, size: 4_000_000 },
    'POST /api/v1/procure/trash/1/restore': 204,
    'DELETE /api/v1/procure/trash/2': 204,
    'DELETE /api/v1/procure/trash': { deleted: 2 },
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

describe('the trash', () => {
  it('lists what lies in it, and brings back or deletes only after the question', async () => {
    serve()
    const days = vi.fn(async () => true)
    await page.show(<TrashCard days={30} onDays={days} />)
    const rows = [...document.querySelectorAll('[data-trash]')].map((row) => plain(row.textContent))
    expect(rows[0]).toContain('Feldnotizen')
    expect(rows[0]).toContain('PDF')
    expect(rows[0]).toContain('bleibt bis 31.10.2026')
    await page.click(document.querySelector('[data-trash="2"] [data-act="trash-delete"]'))
    expect(page.dialog!.textContent).toContain('Das lässt sich nicht zurückholen.')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(document.querySelector('[data-trash="2"] [data-act="trash-delete"]'))
    await page.inDialog('Löschen')
    await page.click(document.querySelector('[data-trash="1"] [data-act="trash-restore"]'))
    await page.inDialog('Zurückholen')
    await page.click(document.querySelector('[data-act="trash-empty"]'))
    expect(page.dialog!.textContent).toContain('2 Einträge werden endgültig gelöscht.')
    await page.inDialog('Leeren')
    expect(server.changes()).toEqual(['DELETE /api/v1/procure/trash/2', 'POST /api/v1/procure/trash/1/restore', 'DELETE /api/v1/procure/trash'])
  })

  it('saves the days it keeps things, between 1 and 365', async () => {
    serve([])
    const days = vi.fn(async () => true)
    await page.show(<TrashCard days={30} onDays={days} />)
    expect(page.text).toContain('Der Papierkorb ist leer.')
    const input = document.querySelector<HTMLInputElement>('[data-testid="trash-days"]')!
    await page.type(input, '400')
    input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    await page.type(input, '14')
    await page.click(document.body)
    input.focus()
    input.blur()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(days).toHaveBeenCalledWith(14)
    expect(days).not.toHaveBeenCalledWith(400)
  })
})
