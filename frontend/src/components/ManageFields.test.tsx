/**
 * The subtitle and the narrators in the correction of a book: the subtitle for every edition, the narrators only for a
 * recording; only what changed is sent, after the question.
 */
import type { EditionOut, WorkOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { ebook, edition, recording, workOut } from '../test/books'
import { Page, Server } from '../test/harness'
import { ManageDialog } from './ManageBook'

const server = new Server()
const page = new Page()

function open(made: EditionOut) {
  const work: WorkOut = workOut(1, [made])
  return page.show(<ManageDialog work={work} editions={[made]} onClose={() => undefined} onChanged={() => undefined} />)
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'PATCH /api/v1/editions/5': { work_id: 1 } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the subtitle and the narrators', () => {
  it('are corrected for a recording, and only what changed is sent', async () => {
    await open(edition(recording(5), { subtitle: 'Ein Fall' }))
    expect((document.querySelector('#fix-subtitle') as HTMLInputElement).value).toBe('Ein Fall')
    expect((document.querySelector('#fix-narrators') as HTMLTextAreaElement).value).toBe('Ben Hollister')
    await page.type(document.querySelector('#fix-subtitle') as HTMLInputElement, ' Ein Fall im Moor ')
    await page.type(document.querySelector('#fix-narrators') as unknown as HTMLInputElement, 'Ida Brenner\n\nBen Hollister')
    await page.click(page.button('Speichern', page.dialog!))
    expect(server.changes()).toEqual([])
    await page.inDialog('Speichern')
    expect(server.called('PATCH', '/api/v1/editions/5')!.body).toEqual({ subtitle: 'Ein Fall im Moor', narrators: ['Ida Brenner', 'Ben Hollister'] })
  })

  it('offers no narrators for an e-book', async () => {
    await open(edition(ebook(5)))
    expect(document.querySelector('#fix-subtitle')).not.toBeNull()
    expect(document.querySelector('#fix-narrators')).toBeNull()
  })
})
