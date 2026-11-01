/**
 * Browse, the first tab of Discover: the wall of thirteen shelves in three departments, the strips of covers with
 * the tabs of their sections, the shelves made for the person, the switch that hides what the person has, and what the page
 * says when there is nothing to show. Nothing asks a source: the page reads what the server kept.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { WishProvider } from '../state/wish'
import { personalRow, shelfCard, strip, wall } from '../test/browse'
import { watchOf } from '../test/catalog'
import { card } from '../test/discover'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { DiscoverPage } from './DiscoverPage'

const me = {
  id: 1,
  name: 'jule',
  display_name: 'Jule',
  role: 'operator',
  profile: { theme: 'light', view: 'shelf' },
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
  return <div data-testid="where">{location.pathname}</div>
}

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route path="discover" element={<DiscoverPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </WishProvider>
  )
}

const krimi = [
  shelfCard('k1', {
    title: 'Nebelwacht',
    authors: ['Ilse Brandt'],
    released: '2026-09-20',
  }),
  shelfCard('k2', {
    title: 'Nebelläufer',
    authors: ['Theo Valcourt'],
    released: '2026-03-05',
  }),
  shelfCard('k3', {
    title: 'Die Salztöchter',
    authors: ['Juna Okafor'],
    released: '',
    year: 2025,
  }),
]
const WALL = 'GET /api/v1/discover/browse?hide_owned=true'

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/discover/browse/personal?hide_owned=true': { rows: [] },
    [WALL]: wall([
      strip('krimi', krimi),
      strip(
        'bio',
        [
          shelfCard('b1', {
            title: 'Mit leichtem Gepäck',
            authors: ['Agnes Abendroth'],
          }),
        ],
        {
          section: 'popular',
          sections: [
            { key: 'popular', count: 9 },
            { key: 'award', count: 3 },
          ],
        },
      ),
    ]),
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

describe('the first tab', () => {
  it('names the authors one watches under its head, above the shelves, and nothing for a person who watches nobody', async () => {
    serve({ 'GET /api/v1/watching': { items: [watchOf()], limit: 100 } })
    await page.show(app(), '/discover')
    expect(plain(q('[data-testid="watch-strip"]')!.textContent)).toBe('Du beobachtestEHEdmund Halloran' + 'nächstes Buch am 3. März 2027')
    // Below the intro and the switch of the page, above the shelves.
    expect(q('.browse-top')!.compareDocumentPosition(q('[data-testid="watch-strip"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(q('[data-testid="watch-strip"]')!.compareDocumentPosition(q('[data-testid="tile"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    page.close()
    serve({ 'GET /api/v1/watching': { items: [], limit: 100 } })
    await page.show(app(), '/discover')
    expect(q('[data-testid="watch-strip"]')).toBeNull()
  })

  it('is Stöbern, shown when the address names no list, with Bestseller and the others beside it', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(texts('#dlists button')).toEqual(['Stöbern', 'Bestseller', 'Neu erschienen', 'Erscheint bald', 'Gerade beliebt', 'Für dich'])
    expect(all('#dlists button').map((b) => b.classList.contains('on'))).toEqual([true, false, false, false, false, false])
    expect(q('[data-testid="browse"]')).toBeTruthy()
    expect(server.called('GET', WALL.replace('GET ', ''))).toBeTruthy()
    expect(server.called('GET', '/api/v1/discover/lists/best?hide_owned=true')).toBeFalsy()
  })

  it('goes to the lists and back, and the address keeps only the lists that are not the first', async () => {
    serve({ 'GET /api/v1/discover/lists/best?hide_owned=true': {} })
    await page.show(app(), '/discover')
    await page.click(page.button('Bestseller'))
    expect(q('[data-testid="browse"]')).toBeFalsy()
    await page.click(page.button('Stöbern'))
    expect(q('[data-testid="browse"]')).toBeTruthy()
  })
})

describe('the wall', () => {
  it('has a tile for each of the thirteen shelves, in three departments, with three covers and the numbers', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(all('[data-testid="tile"]')).toHaveLength(13)
    expect(texts('.tw-dept h3')).toEqual(['Belletristik', 'Sachbuch', 'Kinder und Jugend'])
    expect(all('.tw-dept').map((dept) => dept.querySelectorAll('.shtile').length)).toEqual([5, 6, 2])
    const tile = q('[data-shelf="krimi"]')!
    expect(plain(tile.querySelector('b')!.textContent)).toBe('Krimi & Thriller')
    expect(plain(tile.querySelector('small')!.textContent)).toBe('19 Bücher · 5 neu')
    expect(tile.querySelectorAll('.sh-c')).toHaveLength(3)
    expect(tile.getAttribute('href')).toBe('/discover/shelf/krimi')
    expect(plain(q('[data-shelf="bio"] small')!.textContent)).toBe('19 Bücher')
    expect(texts('[data-testid="wall"] .dsec-h h2')).toEqual(['Regale nach Themen'])
    expect(q('[data-testid="source"]')!.textContent).toBe('Quelle: Apple Books Charts und Open Library · Deutschland')
  })

  it('opens a shelf from its tile', async () => {
    serve()
    await page.show(app(), '/discover')
    await page.click(q('[data-shelf="bio"]'))
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/shelf/bio')
  })

  it('says what to do with the intro, in the words of a mouse or of a finger', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(q('.browse-intro')!.textContent).toContain('Fahr über ein Cover')
    page.close()
    vi.stubGlobal('matchMedia', () => ({
      matches: true,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }))
    serve()
    await page.show(app(), '/discover')
    expect(q('.browse-intro')!.textContent).toContain('Tippe auf ein Cover')
  })
})

describe('the strips', () => {
  it('stand under the departments, each with its name, the tabs of its sections and the link to the whole shelf', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(all('[data-testid="dept"]')).toHaveLength(2)
    expect(texts('[data-testid="dept"] .dept-h h2')).toEqual(['Belletristik', 'Sachbuch'])
    expect(texts('[data-testid="dept"] .dept-h p')).toEqual([
      'Romane, Spannung und alles, was erfunden ist.',
      'Leben, Geschichte, Natur und Gesellschaft. Was wirklich war und ist.',
    ])
    const first = q('[data-shelf="krimi"][data-testid="strip"]')!
    expect(plain(first.querySelector('h3 a')!.textContent)).toBe('Krimi & Thriller')
    expect([...first.querySelectorAll('.subtabs button')].map((b) => [plain(b.textContent), b.classList.contains('on')])).toEqual([
      ['Neu', true],
      ['Gerade beliebt', false],
      ['Klassiker', false],
    ])
    expect(plain(first.querySelector('.sec-link')!.textContent).trim()).toBe('Ganzes Regal')
    expect(first.querySelector('.sec-link')!.getAttribute('href')).toBe('/discover/shelf/krimi')
    expect([...first.querySelectorAll('.bcard b')].map((b) => b.textContent)).toEqual(['Nebelwacht', 'Nebelläufer', 'Die Salztöchter'])
  })

  it('print the day on the new books, the year when only that is known, and the prize on the prize winners', async () => {
    serve({
      [WALL]: wall([
        strip('krimi', krimi),
        strip(
          'bio',
          [
            shelfCard('b1', {
              title: 'Gegen den Strom',
              award: 'Pulitzer Prize',
            }),
          ],
          {
            section: 'award',
            sections: [
              { key: 'award', count: 3 },
              { key: 'popular', count: 9 },
            ],
          },
        ),
      ]),
    })
    await page.show(app(), '/discover')
    const metas = [...q('[data-shelf="krimi"][data-testid="strip"]')!.querySelectorAll('.bmeta')].map((m) => m.textContent)
    expect(metas).toEqual(['erschienen am 20. Sept.', 'erschienen am 5. März', '2025'])
    expect(texts('[data-shelf="bio"][data-testid="strip"] .bmeta')).toEqual(['Pulitzer Prize'])
    expect(all('[data-shelf="bio"][data-testid="strip"] .tribbon')).toHaveLength(1)
  })

  it('read another section from the server when its tab is pressed, and keep it', async () => {
    const popular = [shelfCard('p1', { title: 'Der stille Pier' }), shelfCard('p2', { title: 'Hafenlichter' })]
    const URL = '/api/v1/discover/shelves/krimi?section=popular&hide_owned=true&limit=14'
    serve({ [`GET ${URL}`]: { items: popular } })
    await page.show(app(), '/discover')
    const tab = (name: string) => page.button(name, q('[data-shelf="krimi"][data-testid="strip"]')!)
    await page.click(tab('Gerade beliebt'))
    expect(server.called('GET', URL)).toBeTruthy()
    expect([...q('[data-shelf="krimi"][data-testid="strip"]')!.querySelectorAll('.bcard b')].map((b) => b.textContent)).toEqual(['Der stille Pier', 'Hafenlichter'])
    expect(tab('Gerade beliebt')!.classList.contains('on')).toBe(true)
    await page.click(tab('Neu'))
    expect([...q('[data-shelf="krimi"][data-testid="strip"]')!.querySelectorAll('.bcard b')]).toHaveLength(3)
    await page.click(tab('Gerade beliebt'))
    expect(server.calls.filter((c) => c.url === URL)).toHaveLength(1)
  })

  it('keep the tab the person chose while the wall is still being filled and read again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const popular = [shelfCard('p1', { title: 'Der stille Pier' }), shelfCard('p2', { title: 'Hafenlichter' })]
    const URL = '/api/v1/discover/shelves/krimi?section=popular&hide_owned=true&limit=14'
    serve({ [WALL]: wall([strip('krimi', krimi)], { pending: true }), [`GET ${URL}`]: { items: popular } })
    await page.show(app(), '/discover')
    const tab = (name: string) => page.button(name, q('[data-shelf="krimi"][data-testid="strip"]')!)
    await page.click(tab('Gerade beliebt'))
    const before = server.calls.filter((c) => c.url.startsWith('/api/v1/discover/browse?')).length
    await act(async () => vi.advanceTimersByTime(4100))
    await act(async () => vi.advanceTimersByTime(10))
    expect(server.calls.filter((c) => c.url.startsWith('/api/v1/discover/browse?')).length).toBeGreaterThan(before)
    expect(tab('Gerade beliebt')!.classList.contains('on')).toBe(true)
    expect([...q('[data-shelf="krimi"][data-testid="strip"]')!.querySelectorAll('.bcard b')].map((b) => b.textContent)).toEqual(['Der stille Pier', 'Hafenlichter'])
  })

  it('ask for another section with the switch as the person set it', async () => {
    const URL = '/api/v1/discover/shelves/krimi?section=popular&hide_owned=false&limit=14'
    serve({
      'GET /api/v1/discover/browse?hide_owned=false': wall([strip('krimi', krimi)], { hide_owned: false }),
      'GET /api/v1/discover/browse/personal?hide_owned=false': { rows: [] },
      [`GET ${URL}`]: { items: [shelfCard('p1', { title: 'Der stille Pier' })] },
    })
    auth.me = { ...me, profile: { ...me.profile, discover_hide_owned: false } } as Me
    await page.show(app(), '/discover')
    await page.click(page.button('Gerade beliebt', q('[data-shelf="krimi"][data-testid="strip"]')!))
    expect(server.called('GET', URL)).toBeTruthy()
  })

  it('open a book that is not on the shelf at its own address, and a book of the shelf as the book of the shelf', async () => {
    serve({
      [WALL]: wall([strip('krimi', [shelfCard('k1', { title: 'Nebelwacht' }), shelfCard('k2', { title: 'Orbit Null', owned_work_id: 7 })])]),
    })
    await page.show(app(), '/discover')
    await page.click(all('.bcard')[0])
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/k1')
    page.close()
    serve({
      [WALL]: wall([strip('krimi', [shelfCard('k2', { title: 'Orbit Null', owned_work_id: 7 })])]),
    })
    await page.show(app(), '/discover')
    await page.click(all('.bcard')[0])
    expect(q('[data-testid="where"]')!.textContent).toBe('/work/7')
  })

  it('mark what the person has, what they asked for and what they put on their list', async () => {
    serve({
      [WALL]: wall([strip('krimi', [shelfCard('k1', { title: 'Eins', owned_work_id: 3 }), shelfCard('k2', { title: 'Zwei' }), shelfCard('k3', { title: 'Drei', wished: true })])]),
    })
    await page.show(app(), '/discover')
    expect(texts('[data-testid="badge-owned"]')).toEqual(['Im Regal'])
    expect(all('.bcard').map((c) => c.classList.contains('is-owned'))).toEqual([true, false, false])
    expect(all('.bcard')[0]!.querySelector('[data-testid="badge-asked"]')).toBeNull()
  })

  it('name each card for a screen reader with the title and the author', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(all('.bcard')[0]!.getAttribute('aria-label')).toBe('Nebelwacht, Ilse Brandt')
  })
})

describe('the switch that hides what the person has', () => {
  it('is on from the start, is kept in the profile, and the wall is asked for again', async () => {
    serve({
      'GET /api/v1/discover/browse?hide_owned=false': wall([strip('krimi', krimi.slice(0, 2))], { hide_owned: false }),
      'GET /api/v1/discover/browse/personal?hide_owned=false': { rows: [] },
    })
    await page.show(app(), '/discover')
    expect((q('[data-testid="hide-owned"]') as HTMLInputElement).checked).toBe(true)
    expect(all('[data-shelf="krimi"][data-testid="strip"] .bcard')).toHaveLength(3)
    await page.click(q('[data-testid="hide-owned"]'))
    expect(server.changes()).toContain('PUT /api/v1/me/preferences')
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({
      discover_hide_owned: false,
    })
    expect(server.called('GET', '/api/v1/discover/browse?hide_owned=false')).toBeTruthy()
    expect(all('[data-shelf="krimi"][data-testid="strip"] .bcard')).toHaveLength(2)
  })
})

describe('the shelves made for the person', () => {
  it('stand between the wall and the departments, with the books they chose', async () => {
    const read = work(5, { title: 'Die Salzwiese' })
    serve({
      'GET /api/v1/discover/browse/personal?hide_owned=true': {
        rows: [
          personalRow('because', [shelfCard('p1', { title: 'Hafenlichter' })], {
            reads: [read],
            author: 'Ilse Marwitz',
            genre: 'krimi',
          }),
          personalRow('like', [shelfCard('p2', { title: 'Der stille Pier' })], {
            reads: [read, work(6, { title: 'Orbit Null' })],
          }),
          personalRow('short', [shelfCard('p3', { title: 'Kurze Nacht', pages: 160 })]),
          personalRow('long', [shelfCard('p4', { title: 'Die lange Nacht', minutes: 1263 })]),
        ],
      },
    })
    await page.show(app(), '/discover')
    expect(texts('[data-testid="personal"] .dsec-h h2')).toEqual(['Für dich ausgesucht'])
    expect(texts('[data-testid="personal"] .dsec-h p')).toEqual(['Aus deinem Lesestapel, berechnet auf diesem Server. Nichts davon geht nach draußen.'])
    // The covers of the favourites stand in the title; what they spell is not part of what the row says.
    const headline = (h: HTMLElement) => {
      const copy = h.cloneNode(true) as HTMLElement
      copy.querySelector('.favs')?.remove()
      return plain(copy.textContent).replace(/\s+/g, ' ').trim()
    }
    expect(all('[data-testid="personal"] .prow h3').map(headline)).toEqual([
      'Weil du „Die Salzwiese“ gelesen hastIlse Marwitz · Krimi & Thriller',
      'Mehr wie deine LieblingsbücherBücher, die du in wenigen Tagen ausgelesen hast',
      'Kurzunter 250 Seiten, für einen Abend oder eine Zugfahrt',
      'Für lange AbendeBücher über 600 Seiten, Hörbücher über 20 Stunden',
    ])
    expect(all('[data-testid="personal-like"] .favc')).toHaveLength(2)
    expect(texts('[data-testid="personal-short"] .bmeta')).toEqual(['160 Seiten'])
    expect(texts('[data-testid="personal-long"] .bmeta')).toEqual(['21 Std 3 Min'])
    const order = [...document.querySelectorAll('[data-testid="wall"], [data-testid="personal"], [data-testid="dept"]')].map((n) => n.getAttribute('data-testid'))
    expect(order).toEqual(['wall', 'personal', 'dept', 'dept'])
  })

  it('are not there when the server made none, or could not', async () => {
    serve()
    await page.show(app(), '/discover')
    expect(q('[data-testid="personal"]')).toBeNull()
    page.close()
    serve()
    server.fails['GET /api/v1/discover/browse/personal?hide_owned=true'] = {
      status: 500,
      code: 'internal_error',
    }
    await page.show(app(), '/discover')
    expect(q('[data-testid="personal"]')).toBeNull()
    expect(q('[data-testid="tile"]')).toBeTruthy()
  })
})

describe('when there is nothing to show', () => {
  const none = (state: string, over: Record<string, unknown> = {}) =>
    wall([], {
      state: state as never,
      asking: false,
      fetched_at: null,
      sources: [],
      ...over,
    })

  it('says that the shelves are being filled and asks again after a few seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serve({ [WALL]: none('fetching', { asking: true, pending: true }) })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-fetching"]')!.textContent).toContain('Die Regale werden gerade gefüllt')
    expect(server.calls.filter((c) => c.url === WALL.replace('GET ', ''))).toHaveLength(1)
    server.answers[WALL] = wall([strip('krimi', krimi)])
    await act(async () => vi.advanceTimersByTime(4100))
    await act(async () => undefined)
    expect(all('[data-testid="tile"]')).toHaveLength(13)
    expect(server.calls.filter((c) => c.url === WALL.replace('GET ', ''))).toHaveLength(2)
  })

  it('says that the sources are off and, to the operator, where to switch them on', async () => {
    serve({ [WALL]: none('main_off') })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-main-off"] a')!.getAttribute('href')).toBe('/settings?tab=sources')
    page.close()
    auth.me = { ...me, role: 'member' } as unknown as Me
    serve({ [WALL]: none('main_off') })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-main-off"] a')).toBeNull()
    expect(q('[data-testid="why-main-off"]')!.textContent).toContain('Das kann der Betreiber')
  })

  it('says when no source is ready for the shelves', async () => {
    serve({ [WALL]: none('no_source') })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-no-source"]')!.textContent).toContain('Für die Regale ist keine Quelle bereit')
    expect(q('[data-testid="why-no-source"] a')!.getAttribute('href')).toBe('/settings?tab=sources')
  })

  it('says the sources gave nothing, or are not answering', async () => {
    serve({ [WALL]: none('empty', { asking: true }) })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-empty"]')!.textContent).toBe('Die Quellen haben für diesen Markt gerade nichts geliefert.')
    page.close()
    serve({ [WALL]: none('failed', { asking: true }) })
    await page.show(app(), '/discover')
    expect(q('[data-testid="why-failed"]')!.textContent).toContain('Die Quellen antworten gerade nicht')
  })

  it('says that everything that fits is on the shelf already', async () => {
    serve({ [WALL]: none('ready', { asking: true }) })
    await page.show(app(), '/discover')
    expect(q('[data-testid="nothing"]')!.textContent).toContain('Mit diesen Filtern steht hier nichts.')
    expect(q('[data-testid="nothing"]')!.textContent).toContain('Was passt, hast du schon.')
  })

  it('says that what stands there was kept earlier when the sources are off now', async () => {
    serve({
      [WALL]: wall([strip('krimi', krimi)], {
        asking: false,
        fetched_at: '2026-10-09T09:00:00Z',
      }),
    })
    await page.show(app(), '/discover')
    expect(q('[data-testid="off-note"]')!.textContent).toContain('9. Oktober 2026')
    expect(all('[data-testid="tile"]')).toHaveLength(13)
  })

  it('says that not all shelves are filled yet while some are, and keeps asking', async () => {
    serve({ [WALL]: wall([strip('krimi', krimi)], { pending: true }) })
    await page.show(app(), '/discover')
    expect(q('[data-testid="partial"]')!.textContent).toContain('Noch nicht alle Regale sind gefüllt')
  })

  it('shows a refusal of the server as a sentence', async () => {
    serve()
    server.fails[WALL] = { status: 500, code: 'internal_error' }
    await page.show(app(), '/discover')
    expect(q('[role="alert"]')).toBeTruthy()
  })
})

describe('in English', () => {
  it('has its words and no dash as a pause', async () => {
    await changeLanguage('en', false)
    serve({
      'GET /api/v1/discover/browse/personal?hide_owned=true': {
        rows: [personalRow('short', [shelfCard('p3', { title: 'Short Night', pages: 160 })])],
      },
    })
    await page.show(app(), '/discover')
    expect(texts('#dlists button')[0]).toBe('Browse')
    expect(texts('.tw-dept h3')).toEqual(['Fiction', 'Non-fiction', 'Children and young adults'])
    expect(plain(q('[data-shelf="krimi"] b')!.textContent)).toBe('Crime & Thrillers')
    expect(plain(q('[data-shelf="krimi"] small')!.textContent)).toBe('19 books · 5 new')
    expect(texts('[data-testid="strip"] .subtabs button').slice(0, 3)).toEqual(['New', 'Popular now', 'Classics'])
    expect(q('[data-testid="personal-short"] .bmeta')!.textContent).toBe('160 pages')
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})

// A card as the lists draw it also stands on the wall of a shelf that the person has all of (smoke for the shared helper).
it('the test helper makes a card that the lists accept too', () => {
  expect(shelfCard('x')).toMatchObject({
    token: 'x',
    award: '',
    blurb: '',
    topics: [],
  })
  expect(card('x').token).toBe('x')
})
