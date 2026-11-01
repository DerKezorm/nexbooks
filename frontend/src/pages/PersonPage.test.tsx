/**
 * The page of an author: the round photo with who made it and under which licence under it (the links leave the
 * page safely), the years of the life, a short text in the person's language with its source named, the series as small
 * shelves and all the books, and a circle with the initials where there is no photo.
 */
import { Route, Routes } from 'react-router-dom'

import type { PersonOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { catalogOf } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { WishProvider } from '../state/wish'
import { PersonPage } from './PersonPage'

vi.mock('../state/auth', () => ({ useAuth: () => ({ status: 'signedIn', me: { role: 'member' } }) }))

const server = new Server()
const page = new Page()

function person(over: Partial<PersonOut> = {}): PersonOut {
  return {
    id: 7,
    name: 'Ada Feldkamp',
    wrote: [
      work(1, { title: 'Erster', series: { id: 3, name: 'Deich', pos: 1 } }),
      work(2, { title: 'Zweiter', series: { id: 3, name: 'Deich', pos: 2 } }),
      work(3, { title: 'Allein', series: { id: 4, name: 'Solo', pos: 1 } }),
    ],
    narrated: [],
    photo_url: '/api/v1/people/7/photo?v=abcdefabcdef',
    credit: { source: 'wikidata', author: 'Pia Example', license: 'CC BY-SA 4.0', license_url: 'https://creativecommons.org/licenses/by-sa/4.0', page_url: 'https://commons.wikimedia.org/wiki/File:Example.jpg' },
    bio: { texts: [{ text: 'erfundene Schriftstellerin', language: 'de', source: 'wikidata', url: 'https://www.wikidata.org/wiki/Q1' }], born: 1971, died: null },
    ...over,
  }
}

function show() {
  return page.show(
    <WishProvider>
      <Routes>
        <Route path="/person/:id" element={<PersonPage />} />
      </Routes>
    </WishProvider>,
    '/person/7',
  )
}

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = { 'GET /api/v1/people/7?language=de': person() }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the photo', () => {
  it('is round, with who made it and the licence as links that open in a place of their own', async () => {
    await show()
    expect(document.querySelector('[data-testid="author-hero"] img')!.getAttribute('src')).toBe('/api/v1/people/7/photo?v=abcdefabcdef')
    const credit = document.querySelector('[data-testid="photo-credit"]')!
    expect(plain(credit.textContent)).toBe('Foto: Pia Example · CC BY-SA 4.0')
    const links = [...credit.querySelectorAll('a')]
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://commons.wikimedia.org/wiki/File:Example.jpg', 'https://creativecommons.org/licenses/by-sa/4.0'])
    for (const link of links) {
      expect(link.getAttribute('target')).toBe('_blank')
      expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    }
  })

  it('names the source when nobody is named, and says nothing for the library\'s own photo', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person({ credit: { source: 'wikidata', author: '', license: '', license_url: '', page_url: 'https://commons.wikimedia.org/wiki/File:Example.jpg' } }) }
    server.install()
    await show()
    expect(plain(document.querySelector('[data-testid="photo-credit"]')!.textContent)).toBe('Foto: Wikimedia Commons')
    page.close()
    server.answers = { 'GET /api/v1/people/7?language=de': person({ credit: null }) }
    server.install()
    await show()
    expect(document.querySelector('[data-testid="photo-credit"]')).toBeNull()
    expect(document.querySelector('[data-testid="author-hero"] img')).toBeTruthy()
  })

  it('is a circle with the initials where there is none', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person({ photo_url: null, credit: null }) }
    server.install()
    await show()
    expect(document.querySelector('[data-testid="author-hero"] img')).toBeNull()
    expect(plain(document.querySelector('[data-testid="author-initials"]')!.textContent)).toBe('AF')
  })
})

describe('the text', () => {
  it('is short, in the language asked for, and names its source with a link', async () => {
    await show()
    expect(server.called('GET', '/api/v1/people/7?language=de')).toBeTruthy()
    const bio = document.querySelector('[data-testid="author-bio"]')!
    expect(plain(bio.querySelector('p')!.textContent)).toBe('erfundene Schriftstellerin')
    const source = bio.querySelector('a')!
    expect(plain(source.textContent)).toBe('Quelle: Wikidata')
    expect(source.getAttribute('rel')).toBe('noopener noreferrer')
    expect(plain(document.querySelector('[data-testid="author-life"]')!.textContent)).toBe('geboren 1971')
  })

  it('says both years of a life that is over, and nothing when nothing is known', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person({ bio: { texts: [{ text: 'x', language: 'de', source: 'wikidata', url: '' }], born: 1920, died: 1990 } }) }
    server.install()
    await show()
    expect(plain(document.querySelector('[data-testid="author-life"]')!.textContent)).toBe('1920 bis 1990')
    page.close()
    server.answers = { 'GET /api/v1/people/7?language=de': person({ bio: null }) }
    server.install()
    await show()
    expect(document.querySelector('[data-testid="author-bio"]')).toBeNull()
    expect(document.querySelector('[data-testid="author-life"]')).toBeNull()
  })

  it('does not follow a link of a source that has none', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person({ bio: { texts: [{ text: 'ohne Seite', language: 'de', source: 'openlibrary', url: '' }], born: null, died: null } }) }
    server.install()
    await show()
    expect(document.querySelector('[data-testid="author-bio"] a')).toBeNull()
  })

  it('is asked for in English when the page is', async () => {
    await changeLanguage('en', false)
    server.answers = { 'GET /api/v1/people/7?language=en': person() }
    server.install()
    await show()
    expect(server.called('GET', '/api/v1/people/7?language=en')).toBeTruthy()
  })
})

describe('the books', () => {
  it('stand in the sections of the works: each series of two books or more in its order, a single one with its kind', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person(), 'GET /api/v1/people/7/catalog': catalogOf(person()) }
    server.install()
    await show()
    const sections = [...document.querySelectorAll('[data-testid="author-section"]')]
    expect(sections.map((s) => plain(s.querySelector('h2')!.textContent))[0]).toBe('Reihe · Deich 2 · 2 im Regal')
    expect(sections.some((s) => plain(s.querySelector('h2')!.textContent).includes('Solo'))).toBe(false)
    expect([...document.querySelectorAll('[data-testid="work"]')].map((w) => w.getAttribute('data-key'))).toContain('own-3')
    expect([...sections[0]!.querySelectorAll('[data-testid="work"]')].map((w) => w.getAttribute('data-key'))).toEqual(['own-1', 'own-2'])
  })
})

describe('an author the person does not see', () => {
  it('is a page that says so', async () => {
    server.fails = { 'GET /api/v1/people/7?language=de': { status: 404, code: 'not_found' } }
    server.install()
    await show()
    expect(plain(document.querySelector('.empty')!.textContent)).toContain('Diese Person gibt es nicht')
    expect(document.querySelector('[data-testid="author-hero"]')).toBeNull()
  })
})

describe('two names, one person', () => {
  it('says under the name which other names the person writes under, beside the photo of the person', async () => {
    server.answers = { 'GET /api/v1/people/7?language=de': person({ aliases: ['Cy Dorn', 'A. F. Kamp'] }) }
    server.install()
    await show()
    expect(plain(document.querySelector('[data-testid="author-alias"]')!.textContent)).toBe('Schreibt auch als Cy Dorn, A. F. Kamp.')
    expect(document.querySelector('[data-testid="author-hero"] img')!.getAttribute('src')).toBe('/api/v1/people/7/photo?v=abcdefabcdef')
  })

  it('says nothing of other names when there are none', async () => {
    await show()
    expect(document.querySelector('[data-testid="author-alias"]')).toBeNull()
    page.close()
    server.answers = { 'GET /api/v1/people/7?language=de': person({ aliases: [] }) }
    server.install()
    await show()
    expect(document.querySelector('[data-testid="author-alias"]')).toBeNull()
  })
})
