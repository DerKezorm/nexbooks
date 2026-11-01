/**
 * The page of a book of the sources leads to its author: the name is a
 * link to the page of the author, and "Über …" says how many works the catalogue knows, how many of them stand on the
 * person's shelf and how many are announced. Invented.
 */
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { WishProvider } from '../state/wish'
import { bookPage } from '../test/discover'
import { Page, Server, plain } from '../test/harness'
import { ForeignPage } from './ForeignPage'

const me = { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname}</div>
}

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="discover/book/:token" element={<ForeignPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </WishProvider>
  )
}

const about = { name: 'Edmund Halloran', person_id: null, token: 'cat-token', alias: '', works: 49, on_shelf: 4, soon: 2, photo_url: null, genre: '' }

function serve(over = {}) {
  const book = bookPage('b1', { title: 'Ruß', authors: ['Edmund Halloran'], about: [about], ...over })
  server.answers = { 'GET /api/v1/wishlist': { count: 0, items: [] }, 'GET /api/v1/discover/books/b1': book }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the author of a book of the sources', () => {
  it('is a link to the page of the author, and "Über …" gives the numbers', async () => {
    serve()
    await page.show(app(), '/discover/book/b1')
    expect(document.querySelector('[data-testid="author-link"]')!.getAttribute('href')).toBe('/author/cat-token')
    const card = document.querySelector('[data-testid="about-author"]')!
    expect(plain(card.querySelector('.d-h')!.textContent)).toBe('Über Edmund Halloran')
    expect(plain(card.querySelector('.ac-t small')!.textContent)).toBe('49 Werke · 4 im Regal · 2 angekündigt')
    await page.click(card.querySelector('a'))
    expect(plain(document.querySelector('[data-testid="where"]')!.textContent)).toBe('/author/cat-token')
  })

  it('leads to the person of the libraries when the person has books of theirs', async () => {
    serve({ about: [{ ...about, person_id: 7 }] })
    await page.show(app(), '/discover/book/b1')
    expect(document.querySelector('[data-testid="author-link"]')!.getAttribute('href')).toBe('/person/7')
  })

  it('says nothing more of an author the server named no page of', async () => {
    serve({ about: [] })
    await page.show(app(), '/discover/book/b1')
    expect(document.querySelector('[data-testid="author-link"]')).toBeNull()
    expect(document.querySelector('[data-testid="about-author"]')).toBeNull()
    expect(plain(document.querySelector('.d-by')!.textContent)).toBe('von Edmund Halloran')
  })
})
