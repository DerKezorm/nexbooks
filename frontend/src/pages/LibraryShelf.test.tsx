/**
 * The library as a shelf: a section for each genre with the number the server gave, the genres as chips, the
 * choice between the shelf and the covers kept in the profile, the tip over a spine, the way to a book, and the way a hit of
 * the search leads to its spine.
 */
import { act } from 'react'
import { Route, Routes, useLocation, type Location } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { library, work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { LibraryPage } from './LibraryPage'

const baseMe = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', profile: { view: 'shelf' } } as unknown as Me
const auth = { me: baseMe as Me | null, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const stats = { works: 4, editions: 4, ebook_editions: 4, audio_editions: 0, works_with_ebook: 4, works_with_audio: 0, genres: { roman: 2, krimi: 1, none: 1 }, languages: {} }
const books = [
  work(1, { title: 'Eins', genre: 'roman' }),
  work(2, { title: 'Zwei', genre: 'roman', series: { id: 3, name: 'Deich', pos: 2 } }),
  work(3, { title: 'Drei', genre: 'krimi' }),
  work(4, { title: 'Vier', genre: '' }),
]

function serve(list = books, over: Partial<typeof stats> = {}) {
  server.answers = {
    'GET /api/v1/libraries': [library()],
    'GET /api/v1/works?page=1&per_page=200': { items: list, total: list.length, page: 1, per_page: 200 },
    'GET /api/v1/works/stats': { ...stats, ...over },
    'PUT /api/v1/me/preferences': { view: 'grid' },
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = baseMe
  auth.setMe.mockClear()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the shelf', () => {
  it('is the first look at the library: a section for each genre, with the number the server gave, and a spine for each book', async () => {
    serve()
    await page.show(<LibraryPage />)
    expect(document.querySelector('[data-testid="grid"]')).toBeNull()
    const sections = [...document.querySelectorAll('section.section')]
    expect(sections.map((s) => s.querySelector('h2')!.textContent)).toEqual(['Romane 2', 'Krimi & Thriller 1', 'Ohne Genre 1'])
    expect(document.querySelectorAll('.spine').length).toBe(4)
  })

  it('has the genres as chips with their numbers, all first, and none for a genre without books', async () => {
    serve()
    await page.show(<LibraryPage />)
    const chips = [...document.querySelectorAll('.genres .g')].map((chip) => plain(chip.textContent))
    expect(chips).toEqual(['Alle 4', 'Romane 2', 'Krimi & Thriller 1', 'Ohne Genre 1'])
    expect(document.querySelector('.genres .g.on')!.textContent).toContain('Alle')
  })

  it('shows only the section of the genre that was chosen, and all again with "Alle"', async () => {
    serve()
    await page.show(<LibraryPage />)
    await page.click(page.button('Krimi & Thriller 1'))
    expect([...document.querySelectorAll('section.section')].map((s) => s.getAttribute('data-genre'))).toEqual(['krimi'])
    expect(page.button('Krimi & Thriller 1')!.getAttribute('aria-pressed')).toBe('true')
    await page.click(page.button('Alle 4'))
    expect(document.querySelectorAll('section.section').length).toBe(3)
  })

  it('shows the same books in the same order as covers', async () => {
    serve()
    await page.show(<LibraryPage />)
    const shelf = [...document.querySelectorAll('.spine')].map((s) => s.getAttribute('data-work'))
    await page.click(page.button('Cover'))
    expect([...document.querySelectorAll('[data-testid="book"]')].map((b) => b.getAttribute('data-work'))).toEqual(shelf)
  })
})

describe('the choice between the shelf and the covers', () => {
  it('is kept in the profile, and the grid shows when it is chosen', async () => {
    serve()
    await page.show(<LibraryPage />)
    await page.click(page.button('Cover'))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ view: 'grid' })
    expect(document.querySelector('[data-testid="grid"]')).toBeTruthy()
    expect(document.querySelector('.spine')).toBeNull()
    expect(auth.setMe).toHaveBeenCalledTimes(1)
    expect(page.button('Cover')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('goes back to what it was when the server refuses', async () => {
    serve()
    server.fails = { 'PUT /api/v1/me/preferences': { status: 500, code: 'internal_error' } }
    await page.show(<LibraryPage />)
    await page.click(page.button('Cover'))
    expect(document.querySelector('.spine')).toBeTruthy()
    expect(page.button('Regal')!.getAttribute('aria-pressed')).toBe('true')
    expect(auth.setMe).not.toHaveBeenCalled()
  })

  it('starts as the profile says', async () => {
    auth.me = { ...baseMe, profile: { view: 'grid' } } as unknown as Me
    serve()
    await page.show(<LibraryPage />)
    expect(document.querySelectorAll('[data-testid="book"]').length).toBe(4)
    expect(page.button('Cover')!.getAttribute('aria-pressed')).toBe('true')
  })
})

describe('the tip over a spine', () => {
  it('shows the title, the author and the series, follows the mouse, and goes when the mouse leaves the spines', async () => {
    serve()
    await page.show(<LibraryPage />)
    const spine = document.querySelector('.spine[data-work="2"]') as HTMLElement
    await act(async () => void spine.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: 100, clientY: 200 })))
    const tip = document.querySelector('[data-testid="spine-tip"]') as HTMLElement
    expect(plain(tip.querySelector('b')!.textContent)).toBe('Zwei')
    expect(plain(tip.querySelector('span')!.textContent)).toBe('Ada Feldkamp · Deich 2')
    // Two lines only, like #tip in the design: chips in there were drawn as full-width blocks by `.tip span`.
    expect(tip.children.length).toBe(2)
    expect(tip.querySelector('.chip')).toBeNull()
    expect(tip.style.transform).toBe('translate(114px, 216px)')
    await act(async () => void spine.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 300, clientY: 400 })))
    expect((document.querySelector('[data-testid="spine-tip"]') as HTMLElement).style.transform).toBe('translate(314px, 416px)')
    await act(async () => void document.querySelector('section h2')!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })))
    expect(document.querySelector('[data-testid="spine-tip"]')).toBeNull()
  })
})

describe('the way to a book', () => {
  it('opens it over the shelf when a spine is pressed, handing it the shelf as the page behind and the book it knows', async () => {
    serve()
    let seen: { pathname: string; state: { background?: { pathname: string; state: unknown }; work?: { id: number } } } | null = null
    function Spy() {
      seen = useLocation() as unknown as typeof seen
      return null
    }
    await page.show(
      <>
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="*" element={<div />} />
        </Routes>
        <Spy />
      </>,
    )
    await page.click(document.querySelector('.spine[data-work="3"]') as HTMLElement)
    expect(seen!.pathname).toBe('/work/3')
    expect(seen!.state.background!.pathname).toBe('/')
    expect(seen!.state.background!.state).toBeNull()
    expect(seen!.state.work!.id).toBe(3)
  })
})

describe('a hit of the search', () => {
  function Where() {
    return <div data-testid="where">{useLocation().pathname}</div>
  }
  const where = () => document.querySelector('[data-testid="where"]')!.textContent

  beforeEach(() => {
    // Nothing glows or scrolls slowly here: the page goes on at once.
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
    Element.prototype.scrollIntoView = vi.fn()
  })

  async function arrive(goto: number) {
    serve()
    await page.show(
      <Routes>
        <Route path="/" element={<LibraryPage />} />
        <Route path="*" element={<Where />} />
      </Routes>,
      { pathname: '/', state: { goto, at: 1 } },
    )
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 80))))
  }

  it('scrolls to the spine and opens the book', async () => {
    await arrive(3)
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(where()).toBe('/work/3')
  })

  it('stays on the book: the shelf under it, which the book is drawn over, is not led away again by the number of the hit', async () => {
    // As the app draws it: the page under a book is the page it came from, the book is the second set of routes.
    function Shell() {
      const location = useLocation()
      const background = (location.state as { background?: Location } | null)?.background
      return (
        <>
          <Routes location={background ?? location}>
            <Route path="/" element={<LibraryPage />} />
            <Route path="*" element={<div />} />
          </Routes>
          {background && <Routes><Route path="work/:id" element={<Where />} /></Routes>}
          <div data-testid="real">{location.pathname}</div>
        </>
      )
    }
    serve()
    await page.show(<Shell />, { pathname: '/', state: { goto: 3, at: 1 } })
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 400))))
    expect(document.querySelector('[data-testid="real"]')!.textContent).toBe('/work/3')
    expect(where()).toBe('/work/3')
    expect(document.querySelector('.spine')).toBeTruthy()
  })

  it('opens a book the shelf does not hold by its number alone', async () => {
    await arrive(99)
    expect(where()).toBe('/work/99')
  })
})
