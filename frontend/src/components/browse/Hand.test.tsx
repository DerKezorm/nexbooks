/**
 * The cover taken in the hand: the mouse rests on a cover for 0.3 seconds and the back of the book appears
 * beside it; a click opens the page; on a screen without a mouse the first tap shows the back as a sheet.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { WishProvider } from '../../state/wish'
import { shelfCard, strip, wall } from '../../test/browse'
import { bookPage } from '../../test/discover'
import { Page, Server, plain } from '../../test/harness'
import { DiscoverPage } from '../../pages/DiscoverPage'

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
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))
const procure = {
  allowed: false,
  open: vi.fn(),
  kindsOf: vi.fn(() => [] as unknown[]),
  counts: null,
  wishes: [],
  running: [],
  reload: async () => undefined,
  byWork: new Map(),
}
vi.mock('../../state/procure', () => ({ useProcure: () => procure }))

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

const WALL = 'GET /api/v1/discover/browse?hide_owned=true'
const books = [
  shelfCard('k1', {
    title: 'Nebelwacht',
    authors: ['Ilse Brandt'],
    color: '#1f304b',
    genre: 'krimi',
    released: '2026-09-20',
    pages: 312,
    minutes: 1263,
    award: 'Edgar Award',
    blurb: 'Ein Kommissar am Deich und eine Tote, die niemand kennen will.',
    forms: [
      { kind: 'ebook', language: 'de', variant: '' },
      { kind: 'audio', language: 'de', variant: 'unabridged' },
    ],
    kinds: ['audio', 'ebook'],
  }),
  shelfCard('k2', {
    title: 'Nebelläufer',
    authors: ['Theo Valcourt'],
    blurb: 'Ein zweites Buch.',
  }),
  shelfCard('k3', {
    title: 'Die Salztöchter',
    authors: ['Juna Okafor'],
    blurb: '',
    owned_work_id: 4,
  }),
]

function serve(over: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/discover/browse/personal?hide_owned=true': { rows: [] },
    [WALL]: wall([strip('krimi', books)]),
    'PUT /api/v1/wishlist/k1': { count: 1, items: [] },
    ...over,
  }
  server.fails = {}
  server.install()
}

function mouse(on: boolean) {
  vi.stubGlobal('matchMedia', () => ({
    matches: !on,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  procure.allowed = false
  procure.open.mockClear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mouse(true)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const q = (selector: string) => document.querySelector<HTMLElement>(selector)
const all = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)]
const card = (index: number) => all('.bcard')[index]!

async function rest(target: Element, ms: number) {
  await act(async () => {
    target.dispatchEvent(
      new MouseEvent('mouseover', {
        bubbles: true,
        relatedTarget: document.body,
      }),
    )
  })
  await act(async () => vi.advanceTimersByTime(ms))
}
async function leave(target: Element, ms = 0) {
  await act(async () => {
    target.dispatchEvent(
      new MouseEvent('mouseout', {
        bubbles: true,
        relatedTarget: document.body,
      }),
    )
  })
  if (ms) await act(async () => vi.advanceTimersByTime(ms))
}

describe('with a mouse', () => {
  it('takes the cover in the hand after 0.3 seconds, and not before', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 250)
    expect(q('[data-testid="hand"]')).toBeNull()
    await act(async () => vi.advanceTimersByTime(100))
    expect(q('[data-testid="hand"]')).toBeTruthy()
    expect(card(0).classList.contains('held')).toBe(true)
  })

  it('shows the back of the book in its colour: the words, the length, the forms, the prize', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    const hand = q('[data-testid="hand"]')!
    expect(hand.style.getPropertyValue('--bc')).toBe('#1f304b')
    expect(plain(hand.querySelector('.h-eye')!.textContent)).toBe('Krimi & Thriller')
    expect(plain(hand.querySelector('.h-t')!.textContent)).toBe('Nebelwacht')
    expect(plain(hand.querySelector('.h-by')!.textContent).trim()).toBe('von Ilse Brandt')
    expect(plain(hand.querySelector('.h-award')!.textContent)).toBe('Edgar Award')
    expect(plain(q('[data-testid="hand-blurb"]')!.textContent)).toBe('Ein Kommissar am Deich und eine Tote, die niemand kennen will.')
    expect([...hand.querySelectorAll('.h-facts span')].map((s) => plain(s.textContent))).toEqual(['312Seiten', '21 Std 3 MinHörbuch', '20. September 2026erschienen'])
    expect([...hand.querySelectorAll('.chip')].map((c) => c.getAttribute('data-kind'))).toEqual(['ebook', 'audio'])
    expect([...hand.querySelectorAll('.h-act button')].map((b) => plain(b.textContent).trim())).toEqual(['Merken', 'Ansehen'])
    expect(hand.getAttribute('role')).toBe('group')
  })

  it('takes the next cover faster, and lets go of the first', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await leave(card(0))
    await rest(card(1), 80)
    expect(plain(q('[data-testid="hand"] .h-t')!.textContent)).toBe('Nebelläufer')
    expect(all('[data-testid="hand"]')).toHaveLength(1)
  })

  it('lets go 0.2 seconds after the mouse left, and keeps hold while the mouse is on the back', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await leave(card(0), 100)
    expect(q('[data-testid="hand"]')).toBeTruthy()
    // The mouse goes from the cover onto the back: one `mouseout` leaves the cover and enters the back.
    await act(async () => {
      card(0).dispatchEvent(
        new MouseEvent('mouseout', {
          bubbles: true,
          relatedTarget: q('[data-testid="hand"]'),
        }),
      )
    })
    await act(async () => vi.advanceTimersByTime(400))
    expect(q('[data-testid="hand"]')).toBeTruthy()
    await leave(q('[data-testid="hand"]')!, 300)
    expect(q('[data-testid="hand"]')).toBeNull()
  })

  it('lets go when the mouse leaves before the cover was taken', async () => {
    serve()
    await page.show(app(), '/discover')
    await act(async () => {
      card(0).dispatchEvent(
        new MouseEvent('mouseover', {
          bubbles: true,
          relatedTarget: document.body,
        }),
      )
    })
    await act(async () => vi.advanceTimersByTime(200))
    await leave(card(0), 400)
    expect(q('[data-testid="hand"]')).toBeNull()
  })

  it('lets go on Escape and when the page scrolls', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(q('[data-testid="hand"]')).toBeNull()
    await rest(card(0), 350)
    await act(async () => {
      document.dispatchEvent(new Event('scroll', { bubbles: true }))
    })
    expect(q('[data-testid="hand"]')).toBeNull()
  })

  it('opens the page of the book on a click and lets go of the hand', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await page.click(card(0))
    expect(q('[data-testid="hand"]')).toBeNull()
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/k1')
  })

  it('turns the cover over with the space bar, and Enter opens the page', async () => {
    serve()
    await page.show(app(), '/discover')
    card(0).focus()
    await act(async () => {
      card(0).dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }))
      card(0).dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true, cancelable: true }))
    })
    const hand = q('[data-testid="hand"]')!
    expect(hand.getAttribute('role')).toBe('dialog')
    expect(hand.contains(document.activeElement)).toBe(true)
    expect(q('[data-testid="where"]')).toBeNull()
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(q('[data-testid="hand"]')).toBeNull()
    await page.click(card(0))
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/k1')
  })

  it('puts the book on the wish list from the back, and says so', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await page.click(page.button('Merken', q('[data-testid="hand"]')!))
    expect(server.called('PUT', '/api/v1/wishlist/k1')).toBeTruthy()
    expect(plain(q('[data-testid="hand"] [data-wish]')!.textContent).trim()).toBe('Gemerkt')
    expect(q('[data-testid="hand"] [data-wish]')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('offers "Beschaffen" only to a person who may, and says when it is asked for', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    expect(q('[data-testid="hand"] [data-procure]')).toBeNull()
    await leave(card(0), 300)
    procure.allowed = true
    await rest(card(0), 350)
    await page.click(q('[data-testid="hand"] [data-procure]'))
    expect(procure.open).toHaveBeenCalledTimes(1)
    expect((procure.open.mock.calls[0] as unknown[][])[0]).toEqual([expect.objectContaining({ token: 'k1', title: 'Nebelwacht' })])
    expect(q('[data-testid="hand"]')).toBeNull()
    procure.kindsOf.mockReturnValue([{ kind: 'ebook', wish: {}, download: null }])
    await rest(card(0), 350)
    expect(q('[data-testid="hand-asked"]')!.textContent).toContain('Gesucht')
    expect(q('[data-testid="hand"] [data-procure]')).toBeNull()
    procure.kindsOf.mockReturnValue([])
  })

  it('shows a book of the shelf with "Im Regal ansehen" and nothing to fetch', async () => {
    serve()
    procure.allowed = true
    await page.show(app(), '/discover')
    await rest(card(2), 350)
    expect([...all('[data-testid="hand"] .h-act button')].map((b) => plain(b.textContent).trim())).toEqual(['Im Regal ansehen'])
    await page.click(page.button('Im Regal ansehen', q('[data-testid="hand"]')!))
    expect(q('[data-testid="where"]')!.textContent).toBe('/work/4')
  })

  it('goes to the page of the author through the search, and to nowhere when the search finds none', async () => {
    serve({
      'GET /api/v1/search/people?q=Ilse+Brandt&limit=4': {
        items: [
          {
            name: 'Ilse Brandt',
            person_id: 9,
            token: null,
            alias: '',
            works: 3,
            on_shelf: 0,
            soon: 0,
            photo_url: null,
            genre: 'krimi',
          },
        ],
      },
    })
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    await page.click(q('.h-by button'))
    expect(q('[data-testid="where"]')!.textContent).toBe('/person/9')
  })
})

describe('the words on the back', () => {
  it('are asked for from the page of the book when the card has none, and asked again while the sources look', async () => {
    serve({
      'GET /api/v1/discover/books/k7': {
        ...bookPage('k7'),
        description: '',
        pending: true,
        forms_known: [],
      },
      [WALL]: wall([strip('krimi', [shelfCard('k7', { title: 'Die Salztöchter', blurb: '' })])]),
    })
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    expect(q('[data-testid="hand-blurb"]')!.textContent).toBe('Der Klappentext wird geholt …')
    server.answers['GET /api/v1/discover/books/k7'] = {
      ...bookPage('k7'),
      description: 'Salz, Wind und ein Fund im Watt.',
      pending: false,
      forms_known: [
        {
          kind: 'ebook',
          language: 'de',
          variant: '',
          minutes: null,
          narrators: [],
          pages: 288,
          source: 'openlibrary',
        },
      ],
    }
    await act(async () => vi.advanceTimersByTime(3600))
    await act(async () => undefined)
    expect(q('[data-testid="hand-blurb"]')!.textContent).toBe('Salz, Wind und ein Fund im Watt.')
    expect(plain(q('.h-facts')!.textContent)).toContain('288Seiten')
  })

  it('say that no source has any when the page of the book has none and nothing is pending', async () => {
    serve({
      'GET /api/v1/discover/books/k4': {
        ...bookPage('k4'),
        description: '',
        pending: false,
        forms_known: [],
      },
      [WALL]: wall([strip('krimi', [shelfCard('k4', { title: 'Ohne Worte', blurb: '' })])]),
    })
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    expect(q('[data-testid="hand-blurb"]')!.textContent).toBe('Zu diesem Buch hat keine Quelle einen Klappentext.')
  })

  it('are not asked for when the card has them', async () => {
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    expect(server.calls.some((c) => c.url.startsWith('/api/v1/discover/books/'))).toBe(false)
  })
})

describe('without a mouse', () => {
  beforeEach(() => mouse(false))

  it('shows the back as a sheet at the first tap, with a cover and a close button, and does not open the page', async () => {
    serve()
    await page.show(app(), '/discover')
    await page.click(card(0))
    const sheet = q('[data-testid="hand"]')!
    expect(sheet.classList.contains('sheet')).toBe(true)
    expect(sheet.getAttribute('role')).toBe('dialog')
    expect(sheet.getAttribute('aria-modal')).toBe('true')
    expect(sheet.querySelector('.h-cv .cover')).toBeTruthy()
    expect(q('[data-testid="where"]')).toBeNull()
    expect(document.body.classList.contains('lock')).toBe(true)
    expect(document.activeElement).toBe(q('[data-testid="hand-close"]'))
  })

  it('closes on the button, on the scrim and on Escape, and gives the page back its scrolling', async () => {
    serve()
    await page.show(app(), '/discover')
    await page.click(card(0))
    await page.click(q('[data-testid="hand-close"]'))
    expect(q('[data-testid="hand"]')).toBeNull()
    expect(document.body.classList.contains('lock')).toBe(false)
    await page.click(card(0))
    await page.click(q('[data-testid="hand-back"]'))
    expect(q('[data-testid="hand"]')).toBeNull()
    await page.click(card(0))
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(q('[data-testid="hand"]')).toBeNull()
  })

  it('opens the page of the book from "Ansehen"', async () => {
    serve()
    await page.show(app(), '/discover')
    await page.click(card(0))
    await page.click(page.button('Ansehen', q('[data-testid="hand"]')!))
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/k1')
  })

  it('keeps the Tab key inside the sheet', async () => {
    serve()
    await page.show(app(), '/discover')
    await page.click(card(0))
    const sheet = q('[data-testid="hand"]')!
    const buttons = [...sheet.querySelectorAll<HTMLElement>('button')]
    buttons[buttons.length - 1]!.focus()
    const event = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    })
    await act(async () => {
      window.dispatchEvent(event)
    })
    expect(event.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(buttons[0])
  })
})

describe('in English', () => {
  it('has its words on the back and no dash', async () => {
    await changeLanguage('en', false)
    serve()
    await page.show(app(), '/discover')
    await rest(card(0), 350)
    const hand = q('[data-testid="hand"]')!
    expect([...hand.querySelectorAll('.h-facts span')].map((s) => plain(s.textContent))).toEqual(['312pages', '21 h 3 minaudiobook', 'September 20, 2026released'])
    expect([...hand.querySelectorAll('.h-act button')].map((b) => plain(b.textContent).trim())).toEqual(['Save', 'View'])
    expect(hand.textContent).not.toMatch(/[–—]/)
  })
})
