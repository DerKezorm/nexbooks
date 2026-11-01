/**
 * The page of an author: the other names (pen names) the person is known by, and the way to split one off. It asks
 * first, says that it cannot be taken back here, and sends nothing before the one click on the button of the question;
 * whoever may not split sees the names and no button.
 */
import { Route, Routes } from 'react-router-dom'

import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { WishProvider } from '../state/wish'
import { PersonPage } from './PersonPage'

vi.mock('../state/auth', () => ({ useAuth: () => ({ status: 'signedIn', me: { role: 'member' } }) }))

const server = new Server()
const page = new Page()

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="person/:id" element={<PersonPage />} />
      </Routes>
    </WishProvider>
  )
}

const person = { id: 1, name: 'Mira Klarstein', wrote: [work(1)], narrated: [], photo_url: null, credit: null, bio: null }

function serve(canSplit: boolean, language = 'de') {
  server.answers = {
    [`GET /api/v1/people/1?language=${language}`]: { ...person, aliases: ['Nele Beispielname'], pen_names: [{ id: 7, name: 'Nele Beispielname' }], can_split: canSplit },
    'POST /api/v1/people/1/split': { ...person, aliases: [], pen_names: [], can_split: false },
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

describe('the other names of a person', () => {
  it('asks before it splits a name off, sends nothing when the question is cancelled, and once when it is answered', async () => {
    serve(true)
    await page.show(app(), '/person/1')
    const names = document.querySelector('[data-testid="pen-names"]')!
    expect(plain(names.textContent)).toContain('Schreibt auch als Nele Beispielname.')
    await page.click(page.button('„Nele Beispielname“ von Mira Klarstein trennen'))
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich hier nicht zurücknehmen.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('„Nele Beispielname“ von Mira Klarstein trennen'))
    await page.inDialog('Trennen')
    expect(server.changes()).toEqual(['POST /api/v1/people/1/split'])
    expect(server.called('POST', '/api/v1/people/1/split')!.body).toEqual({ alias_id: 7 })
    expect(document.querySelector('[data-testid="pen-names"]')).toBeNull()
    expect(plain(document.body.textContent)).toContain('„Nele Beispielname“ ist jetzt eine eigene Person.')
  })

  it('shows the names without a way to split them to whoever may not', async () => {
    serve(false)
    await page.show(app(), '/person/1')
    expect(document.querySelector('[data-testid="pen-names"]')).toBeNull()
    expect(page.button('Trennen')).toBeUndefined()
    expect(plain(document.body.textContent)).toContain('Schreibt auch als Nele Beispielname.')
  })

  it('says it in English too', async () => {
    await changeLanguage('en', false)
    serve(true, 'en')
    await page.show(app(), '/person/1')
    expect(plain(document.querySelector('[data-testid="pen-names"]')!.textContent)).toContain('Also writes as Nele Beispielname.')
  })
})
