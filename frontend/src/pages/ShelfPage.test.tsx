/**
 * One shelf opened: the filters of the design (section, language, format, age, sort, "hide what I have", the
 * topics of the shelf), kept in the address; the books on boards; a page at a time; and what the page says when there is
 * nothing to show.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { WishProvider } from '../state/wish'
import { shelfCard, shelfPage } from '../test/browse'
import { Page, Server, plain } from '../test/harness'
import { ShelfPage } from './ShelfPage'

const me = {
  id: 1,
  name: 'jule',
  display_name: 'Jule',
  role: 'operator',
  profile: { theme: 'light', view: 'shelf', market: 'de' },
} as unknown as Me
const auth = {
  status: 'signedIn',
  me: me as Me | null,
  setMe: vi.fn(),
  signOut: async () => undefined,
  refresh: async () => undefined,
}
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return (
    <div data-testid="where">
      {location.pathname}
      {location.search}
    </div>
  )
}

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="discover/shelf/:key" element={<ShelfPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </WishProvider>
  )
}

const books = [
  shelfCard('k1', {
    title: 'Nebelwacht',
    authors: ['Ilse Brandt'],
    released: '2026-09-20',
  }),
  shelfCard('k2', {
    title: 'Der lange Heimweg',
    authors: ['Hans Feldkamp'],
    award: 'Edgar Award',
    year: 1953,
    pages: 288,
    released: '',
  }),
  shelfCard('k3', {
    title: 'Die Salztöchter',
    authors: ['Juna Okafor'],
    year: 2025,
    released: '',
  }),
]
const FIRST = 'GET /api/v1/discover/shelves/krimi?market=de&hide_owned=true&limit=60'

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/wishlist': { count: 0, items: [] },
    [FIRST]: shelfPage(books),
    'PUT /api/v1/me/preferences': { theme: 'light', view: 'shelf' },
    ...over,
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  auth.setMe.mockClear()
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const q = (selector: string) => document.querySelector<HTMLElement>(selector)
const all = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)]
const texts = (selector: string) => all(selector).map((item) => plain(item.textContent).trim())
const filter = (name: string, value: string) => q(`[data-filter="${name}"][data-value="${value}"]`)

describe('the page of a shelf', () => {
  it('has the department, the name, the words, the count and the source of the design', async () => {
    serve()
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('.eyebrow')!.textContent).toBe('Belletristik')
    expect(q('h2.shp-t')!.textContent).toBe('Krimi & Thriller')
    expect(plain(q('.dsec-h p:not(.eyebrow)')!.textContent)).toBe('Ermittler, Psychothriller, Fälle an der Küste. 3 Bücher.')
    expect(q('[data-testid="source"]')!.textContent).toBe('Quelle: Apple Books Charts und Open Library · Deutschland')
    expect(texts('.bgrid .bcard b')).toEqual(['Nebelwacht', 'Der lange Heimweg', 'Die Salztöchter'])
    expect(all('.bgrid .bcv')).toHaveLength(3)
    expect(q('.back-link')!.getAttribute('href')).toBe('/discover')
    expect(texts('.back-link')).toEqual(['Stöbern'])
  })

  it('counts what the switch hides and what the person has', async () => {
    serve({ [FIRST]: shelfPage(books, { total: 17, owned: 2 }) })
    await page.show(app(), '/discover/shelf/krimi')
    expect(plain(q('.dsec-h p:not(.eyebrow)')!.textContent)).toContain('17 Bücher, die du nicht hast · 2 ausgeblendet, weil sie schon im Regal stehen.')
    page.close()
    serve({
      'GET /api/v1/discover/shelves/krimi?market=de&hide_owned=false&limit=60': shelfPage(books, { total: 19, owned: 2, hide_owned: false }),
    })
    auth.me = {
      ...me,
      profile: { ...me.profile, discover_hide_owned: false },
    } as unknown as Me
    await page.show(app(), '/discover/shelf/krimi')
    expect(plain(q('.dsec-h p:not(.eyebrow)')!.textContent)).toContain('19 Bücher · 2 davon im Regal.')
  })

  it('offers the sections the shelf has books for, the languages it holds, the formats, the ages and the sorts', async () => {
    serve()
    await page.show(app(), '/discover/shelf/krimi')
    expect(texts('[data-filter="section"]')).toEqual(['Alles', 'Neu', 'Gerade beliebt', 'Preisgekrönt', 'Klassiker'])
    expect(texts('[data-filter="language"]')).toEqual(['Alle Sprachen', 'Deutsch'])
    expect(texts('[data-filter="format"]')).toEqual(['Alle Formate', 'E-Book', 'Hörbuch'])
    expect(texts('[data-filter="years"]')).toEqual(['Alle Jahre', '2025 und 2026', '2010 bis 2024', 'vor 2010'])
    expect([...q('[data-testid="sort"]')!.querySelectorAll('option')].map((o) => o.textContent)).toEqual(['Beliebt zuerst', 'Neueste zuerst', 'Kürzeste zuerst'])
    expect(texts('.dgenres button')).toEqual(['Alle Gebiete', 'Ermittler', 'Cosy Crime'])
    expect(
      all('[aria-pressed="true"]')
        .filter((b) => b.closest('[data-testid="filters"]'))
        .map((b) => plain(b.textContent).trim()),
    ).toEqual(['Alles', 'Alle Sprachen', 'Alle Formate', 'Alle Jahre', 'Alle Gebiete'])
  })

  it('takes a section, keeps it in the address and asks the server for it', async () => {
    serve({
      'GET /api/v1/discover/shelves/krimi?market=de&section=award&hide_owned=true&limit=60': shelfPage([books[1]!], { section: 'award', total: 1 }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    await page.click(filter('section', 'award'))
    expect(server.called('GET', '/api/v1/discover/shelves/krimi?market=de&section=award&hide_owned=true&limit=60')).toBeTruthy()
    expect(texts('.bgrid .bcard b')).toEqual(['Der lange Heimweg'])
    expect(texts('.bgrid .bmeta')).toEqual(['Edgar Award'])
    expect(filter('section', 'award')!.classList.contains('on')).toBe(true)
  })

  it('sends every filter it is given in the address, and reads them back after a reload', async () => {
    const URL = 'GET /api/v1/discover/shelves/krimi?section=new&language=de&format=ebook&years=new&topic=ermittler&sort=new&market=de&hide_owned=true&limit=60'
    serve({ [URL]: shelfPage([books[0]!], { section: 'new', total: 1 }) })
    // Sent in the order the page builds them.
    server.answers['GET /api/v1/discover/shelves/krimi?market=de&section=new&language=de&format=ebook&years=new&topic=ermittler&sort=new&hide_owned=true&limit=60'] = shelfPage(
      [books[0]!],
      { section: 'new', total: 1 },
    )
    await page.show(app(), '/discover/shelf/krimi?section=new&language=de&format=ebook&years=new&topic=ermittler&sort=new')
    const asked = server.calls.find((c) => c.url.startsWith('/api/v1/discover/shelves/krimi'))!.url
    for (const part of ['section=new', 'language=de', 'format=ebook', 'years=new', 'topic=ermittler', 'sort=new', 'market=de', 'hide_owned=true', 'limit=60'])
      expect(asked).toContain(part)
    expect(filter('section', 'new')!.classList.contains('on')).toBe(true)
    expect(filter('years', 'new')!.classList.contains('on')).toBe(true)
    expect(q('[data-topic="ermittler"]')!.classList.contains('on')).toBe(true)
    expect((q('[data-testid="sort"]') as HTMLSelectElement).value).toBe('new')
  })

  it('leaves out a filter that is "all" and drops one the address spells wrong', async () => {
    serve()
    await page.show(app(), '/discover/shelf/krimi?section=bogus&years=old2&format=video&topic=A!&sort=random&language=ger')
    const asked = server.calls.find((c) => c.url.startsWith('/api/v1/discover/shelves/krimi'))!.url
    expect(asked).toBe('/api/v1/discover/shelves/krimi?market=de&hide_owned=true&limit=60')
  })

  it('prints the day on the newest first and the pages on the shortest first', async () => {
    serve({
      'GET /api/v1/discover/shelves/krimi?market=de&sort=new&hide_owned=true&limit=60': shelfPage(books, { sort: 'new' }),
      'GET /api/v1/discover/shelves/krimi?market=de&sort=short&hide_owned=true&limit=60': shelfPage(books, { sort: 'short' }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    await page.choose(q('[data-testid="sort"]'), 'new')
    expect(texts('.bgrid .bmeta')).toEqual(['erschienen am 20. Sept.', '1953', '2025'])
    await page.choose(q('[data-testid="sort"]'), 'short')
    expect(texts('.bgrid .bmeta')).toEqual(['288 Seiten'])
  })

  it('shows a page at a time and the rest on "Mehr zeigen", a book once', async () => {
    const first = Array.from({ length: 60 }, (_, n) => shelfCard(`a${n}`, { title: `Buch ${n}` }))
    const rest = [shelfCard('a59', { title: 'Buch 59' }), shelfCard('z1', { title: 'Letztes Buch' })]
    serve({
      [FIRST]: shelfPage(first, { total: 61 }),
      'GET /api/v1/discover/shelves/krimi?market=de&hide_owned=true&limit=60&offset=60': shelfPage(rest, { total: 61, offset: 60 }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    expect(all('.bgrid .bcard')).toHaveLength(60)
    await page.click(q('[data-testid="more"]'))
    expect(all('.bgrid .bcard')).toHaveLength(61)
    expect(texts('.bgrid .bcard b').at(-1)).toBe('Letztes Buch')
    expect(q('[data-testid="more"]')).toBeNull()
  })

  it('keeps the switch in the profile and reads the shelf again', async () => {
    serve({
      'GET /api/v1/discover/shelves/krimi?market=de&hide_owned=false&limit=60': shelfPage(books.slice(0, 2), { hide_owned: false }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    await page.click(q('[data-testid="hide-owned"]'))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({
      discover_hide_owned: false,
    })
    expect(all('.bgrid .bcard')).toHaveLength(2)
  })

  it('offers the market at its head, keeps it in the profile and reads the shelf of that market', async () => {
    serve({
      'GET /api/v1/discover/shelves/krimi?market=us&hide_owned=true&limit=60': shelfPage(books.slice(0, 1), { market: 'us' }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    const pick = q('#market')!
    expect(page.button('Deutschland', pick)!.getAttribute('aria-pressed')).toBe('true')
    await page.click(page.button('USA', pick))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ market: 'us' })
    expect(server.called('GET', '/api/v1/discover/shelves/krimi?market=us&hide_owned=true&limit=60')).toBeTruthy()
    expect(all('.bgrid .bcard')).toHaveLength(1)
    expect(page.button('USA', q('#market')!)!.getAttribute('aria-pressed')).toBe('true')
  })

  it('opens a book at its own address', async () => {
    serve()
    await page.show(app(), '/discover/shelf/krimi')
    await page.click(all('.bgrid .bcard')[2])
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/k3')
  })
})

describe('when there is nothing to show', () => {
  it('says that no shelf of that name exists', async () => {
    serve()
    server.fails['GET /api/v1/discover/shelves/nosuch?market=de&hide_owned=true&limit=60'] = { status: 404, code: 'not_found' }
    await page.show(app(), '/discover/shelf/nosuch')
    expect(q('[data-testid="unknown-shelf"]')!.textContent).toBe('Dieses Regal gibt es nicht.')
    expect(q('.back-link')!.getAttribute('href')).toBe('/discover')
  })

  it('says that the filters leave nothing, and that what fits is on the shelf already', async () => {
    serve({ [FIRST]: shelfPage([], { total: 0, owned: 4, topics: [] }) })
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('[data-testid="nothing"]')!.textContent).toBe('Mit diesen Filtern steht hier nichts. Was passt, hast du schon.')
    expect(q('.dgenres')).toBeNull()
  })

  it('says that the shelf is being filled and asks again after a few seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serve({
      [FIRST]: shelfPage([], {
        state: 'fetching',
        pending: true,
        total: 0,
        sections: [],
        languages: [],
        topics: [],
        sources: [],
      }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('[data-testid="why-fetching"]')!.textContent).toContain('Die Regale werden gerade gefüllt')
    server.answers[FIRST] = shelfPage(books)
    await act(async () => vi.advanceTimersByTime(4100))
    await act(async () => undefined)
    expect(all('.bgrid .bcard')).toHaveLength(3)
  })

  it('says that the sources are off, to the operator with the way to the settings', async () => {
    serve({
      [FIRST]: shelfPage([], {
        state: 'main_off',
        asking: false,
        total: 0,
        sources: [],
      }),
    })
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('[data-testid="why-main-off"] a')!.getAttribute('href')).toBe('/settings?tab=sources')
  })

  it('shows a refusal of the server as a sentence', async () => {
    serve()
    server.fails[FIRST] = { status: 500, code: 'internal_error' }
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('[role="alert"]')).toBeTruthy()
  })
})

describe('in English', () => {
  it('has its words and no dash as a pause', async () => {
    await changeLanguage('en', false)
    serve()
    await page.show(app(), '/discover/shelf/krimi')
    expect(q('h2.shp-t')!.textContent).toBe('Crime & Thrillers')
    expect(texts('[data-filter="section"]')).toEqual(['All', 'New', 'Popular now', 'Prize winners', 'Classics'])
    expect(texts('[data-filter="years"]')).toEqual(['All years', '2025 and 2026', '2010 to 2024', 'before 2010'])
    expect(texts('.dgenres button')).toEqual(['All topics', 'Detectives', 'Cosy crime'])
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})
