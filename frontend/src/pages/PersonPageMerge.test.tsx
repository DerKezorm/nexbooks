/**
 * The page of an author: "Mit anderem Autor zusammenführen". The button is for whoever may do it; the dialog looks for the
 * other author by name (never the author themselves), asks which name stays and what happens, and sends nothing before the
 * one click on the last button; a refusal of the server is told in the dialog, which stays open; the author that stays is
 * shown afterwards (it may be the other one).
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import { MERGE_SEARCH_MS } from '../components/MergeAuthorDialog'
import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { WishProvider } from '../state/wish'
import { PersonPage } from './PersonPage'

vi.mock('../state/auth', () => ({ useAuth: () => ({ status: 'signedIn', me: { role: 'member' } }) }))

const server = new Server()
const page = new Page()

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="person/:id" element={<PersonPage />} />
      </Routes>
      <Where />
    </WishProvider>
  )
}

const person = { id: 1, name: 'Rune Falkner', wrote: [work(1)], narrated: [], photo_url: null, credit: null, bio: null, aliases: [], pen_names: [], can_split: false }
const card = (id: number, name: string, works: number) => ({ id, name, sort_name: name, works, audio: 0, genre: '', added_at: null, photo_url: null })
const SEARCH = 'GET /api/v1/authors?q=klar&sort=name&per_page=12'

function serve(canMerge: boolean, language = 'de') {
  server.answers = {
    [`GET /api/v1/people/1?language=${language}`]: { ...person, can_merge: canMerge },
    [`GET /api/v1/people/2?language=${language}`]: { ...person, id: 2, name: 'Mira Klarstein', aliases: ['Rune Falkner'], pen_names: [{ id: 5, name: 'Rune Falkner' }], can_split: true, can_merge: true },
    [SEARCH]: { items: [card(1, 'Rune Falkner', 1), card(2, 'Mira Klarstein', 3)], total: 2, page: 1, per_page: 12, genres: {} },
    'GET /api/v1/authors?q=zzz&sort=name&per_page=12': { items: [], total: 0, page: 1, per_page: 12, genres: {} },
    'POST /api/v1/people/1/merge': { ...person, id: 2, name: 'Mira Klarstein', aliases: ['Rune Falkner'], pen_names: [{ id: 5, name: 'Rune Falkner' }], can_split: true, can_merge: true },
  }
  server.fails = {}
  server.install()
}

/** Types into the search field and waits for the answer. */
async function search(text: string) {
  await page.type(page.dialog!.querySelector<HTMLInputElement>('input[type="search"]'), text)
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, MERGE_SEARCH_MS + 60))
  })
  await idle()
}

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('joining two authors by hand', () => {
  it('offers the button to whoever may, and to nobody else', async () => {
    serve(false)
    await page.show(app(), '/person/1')
    expect(document.querySelector('[data-testid="merge-open"]')).toBeNull()
    page.close()
    serve(true)
    await page.show(app(), '/person/1')
    expect(plain(document.querySelector('[data-testid="merge-open"]')!.textContent)).toContain('Mit anderem Autor zusammenführen')
  })

  it('looks for the other author, never offers the author themselves, and sends nothing before the last click', async () => {
    serve(true)
    await page.show(app(), '/person/1')
    await page.click(document.querySelector('[data-testid="merge-open"]'))
    expect(plain(page.dialog!.textContent)).toContain('Tippe mindestens zwei Buchstaben')
    await search('klar')
    const found = [...page.dialog!.querySelectorAll('[data-testid="merge-found"] button')].map((item) => plain(item.textContent))
    expect(found).toEqual(['Mira Klarstein3 Werke'])
    await page.click(page.dialog!.querySelector('[data-testid="merge-found"] button'))
    expect(plain(page.dialog!.textContent)).toContain('Welcher Name soll bleiben?')
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich mit „Trennen“ auf der Seite des Autors zurücknehmen.')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Zurück', page.dialog!))
    expect(page.dialog!.querySelector('[data-testid="merge-found"]')).toBeTruthy()
    expect(server.changes()).toEqual([])
  })

  it('asks nothing for less than two letters', async () => {
    serve(true)
    await page.show(app(), '/person/1')
    await page.click(document.querySelector('[data-testid="merge-open"]'))
    await search('k')
    expect(server.calls.filter((call) => call.url.startsWith('/api/v1/authors'))).toEqual([])
    expect(plain(page.dialog!.textContent)).toContain('Tippe mindestens zwei Buchstaben')
  })

  it('tells when nobody has that name', async () => {
    serve(true)
    await page.show(app(), '/person/1')
    await page.click(document.querySelector('[data-testid="merge-open"]'))
    await search('zzz')
    expect(plain(page.dialog!.querySelector('[data-testid="merge-none"]')!.textContent)).toBe('Keinen Autor mit diesem Namen gefunden.')
  })

  it('joins them once, with the name that was chosen, and shows the author that stays', async () => {
    serve(true)
    await page.show(app(), '/person/1')
    await page.click(document.querySelector('[data-testid="merge-open"]'))
    await search('klar')
    await page.click(page.dialog!.querySelector('[data-testid="merge-found"] button'))
    const radios = [...page.dialog!.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
    expect(radios.map((item) => item.checked)).toEqual([true, false])
    await page.click(radios[1])
    await page.click(page.button('Zusammenführen', page.dialog!))
    expect(server.changes()).toEqual(['POST /api/v1/people/1/merge'])
    expect(server.called('POST', '/api/v1/people/1/merge')!.body).toEqual({ other_id: 2, keep: 'other' })
    expect(page.dialog).toBeNull()
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/person/2')
    expect(plain(document.querySelector('h1')!.textContent)).toBe('Mira Klarstein')
    expect(page.text).toContain('„Mira Klarstein“ und „Rune Falkner“ sind jetzt ein Autor.')
  })

  it('keeps the dialog open and says why when the server does not join them', async () => {
    serve(true)
    server.fails['POST /api/v1/people/1/merge'] = { status: 409, code: 'cannot_join' }
    await page.show(app(), '/person/1')
    await page.click(document.querySelector('[data-testid="merge-open"]'))
    await search('klar')
    await page.click(page.dialog!.querySelector('[data-testid="merge-found"] button'))
    await page.click(page.button('Zusammenführen', page.dialog!))
    expect(plain(page.dialog!.querySelector('[role="alert"]')!.textContent)).toContain('Diese beiden lassen sich nicht zusammenführen')
    expect(page.button('Zusammenführen', page.dialog!)!.disabled).toBe(false)
  })

  it('says it in English too', async () => {
    await changeLanguage('en', false)
    serve(true, 'en')
    await page.show(app(), '/person/1')
    expect(plain(document.querySelector('[data-testid="merge-open"]')!.textContent)).toContain('Join with another author')
  })
})
