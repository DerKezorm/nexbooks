/**
 * Discover as the design shows it: the six tabs, the market, the filters, the grid of covers with the numbers on
 * the first places, the day on new and coming books, the source named to fit the filter, the switch that hides what the person
 * has, and what the page says when there is nothing to show. The wish list, the gaps in the series and more of the authors.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { WishProvider } from '../state/wish'
import { card, forYou, gap, list, volume } from '../test/discover'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { DiscoverPage } from './DiscoverPage'

const me = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', profile: { theme: 'light', view: 'shelf' } } as unknown as Me
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
        <Route path="discover" element={<DiscoverPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </WishProvider>
  )
}

const books = [
  card('t1', { title: 'Der Leuchtturmwaerter', rank: 1, sources: ['apple', 'audible'], kinds: ['audio', 'ebook'], forms: [{ kind: 'ebook', language: 'de', variant: '' }, { kind: 'audio', language: 'de', variant: 'unabridged' }] }),
  card('t2', { title: 'Die Salzwiese', rank: 2, authors: ['Ilse Marwitz'] }),
  card('t3', { title: 'Orbit Null', rank: 3, authors: ['Theo Valcourt'], genre: 'scifi' }),
  card('t4', { title: 'The Salt Orchard', rank: 4, authors: ['Quentin Hartwig'], language: 'en', forms: [{ kind: 'ebook', language: 'en', variant: '' }, { kind: 'audio', language: 'en', variant: 'abridged' }], kinds: ['audio', 'ebook'] }),
]
const BEST = 'GET /api/v1/discover/lists/best?hide_owned=true'

function serve(over: Record<string, unknown> = {}) {
  server.answers = { 'GET /api/v1/wishlist': { count: 0, items: [] }, [BEST]: list(books), 'PUT /api/v1/me/preferences': { theme: 'light', view: 'shelf' }, ...over }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  auth.setMe.mockClear()
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const q = (selector: string) => document.querySelector<HTMLElement>(selector)
const all = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)]
const texts = (selector: string) => all(selector).map((item) => plain(item.textContent).trim())

describe('the page', () => {
  it('has the tabs, the markets and the filters of the design, and the first list', async () => {
    serve()
    await page.show(app(), '/discover?list=best')
    expect(q('h1')!.textContent).toBe('Entdecken')
    expect(texts('#dlists button')).toEqual(['Stöbern', 'Bestseller', 'Neu erschienen', 'Erscheint bald', 'Gerade beliebt', 'Für dich'])
    expect(all('#dlists button').map((b) => b.classList.contains('on'))).toEqual([false, true, false, false, false, false])
    expect(texts('#market button')).toEqual(['Deutschland', 'USA', 'UK', 'Frankreich'])
    expect(all('#market button').map((b) => b.classList.contains('on'))).toEqual([true, false, false, false])
    // The other markets the server offers stand in a list beside the buttons.
    expect([...q('.market-more')!.querySelectorAll('option')].map((o) => o.textContent)).toEqual(['Weitere Märkte', 'Kanada'])
    expect(texts('#dfmt button')).toEqual(['Alle Formate', 'E-Book', 'Hörbuch'])
    expect(texts('#dlang button')).toEqual(['Alle Sprachen', 'Deutsch', 'Englisch'])
    expect(q('.toggle')!.textContent).toBe('Was ich habe ausblenden')
    expect((q('#hideOwned') as HTMLInputElement).checked).toBe(true)
    expect(texts('#dgenres button')).toEqual(['Alle Genres', 'Romane', 'Krimi & Thriller', 'Fantasy', 'Science-Fiction', 'Klassiker', 'Sachbuch', 'Kinder & Jugend', 'Lyrik'])
    expect(q('.dsec-h h2')!.textContent).toBe('Bestseller')
    expect(q('.dsec-h p')!.textContent).toBe('Die meistgekauften Bücher der Woche. 4 Bücher.')
  })

  it('draws covers with the number of their place on each, and no spines', async () => {
    serve()
    await page.show(app(), '/discover?list=best')
    expect(all('.dgrid .dcard')).toHaveLength(4)
    expect(texts('.dgrid .rk')).toEqual(['1', '2', '3', '4'])
    expect(all('.dgrid .dcard .cover')).toHaveLength(4)
    expect(all('.spine')).toHaveLength(0)
    expect(texts('.dgrid .dcard b')).toEqual(['Der Leuchtturmwaerter', 'Die Salzwiese', 'Orbit Null', 'The Salt Orchard'])
    expect(texts('.dgrid .dcard small')).toEqual(['Mara Feldkamp', 'Ilse Marwitz', 'Theo Valcourt', 'Quentin Hartwig'])
  })

  it('shows a chip for each form the book is known in: the language of an e-book, the headphones of a recording', async () => {
    serve()
    await page.show(app(), '/discover?list=best')
    const chips = all('.dgrid .dcard')[0]!.querySelectorAll('.chip')
    expect([...chips].map((chip) => [chip.getAttribute('data-kind'), chip.textContent])).toEqual([['ebook', 'DE'], ['audio', '']])
    const second = all('.dgrid .dcard')[3]!.querySelectorAll('.chip')
    expect([...second].map((chip) => chip.textContent)).toEqual(['EN', 'gekürzt'])
  })

  it('names the source to fit the list, the market and the filter the server answered with', async () => {
    serve({ 'GET /api/v1/discover/lists/best?format=audio&hide_owned=true': list(books.slice(0, 2), { format: 'audio', sources: ['audible'] }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="source"]')!.textContent).toBe('Quelle: Apple Books Charts und Audible · Deutschland')
    await page.click(page.button('Hörbuch'))
    expect(q('[data-testid="source"]')!.textContent).toBe('Quelle: Audible · Deutschland')
    expect(q('#dfmt .on')!.textContent).toBe('Hörbuch')
  })

  it('counts the books and says what the switch hides', async () => {
    serve({ [BEST]: list(books.slice(0, 3), { owned: 2 }) })
    await page.show(app(), '/discover?list=best')
    expect(q('.dsec-h p')!.textContent).toBe('Die meistgekauften Bücher der Woche. 3 Bücher, die du nicht hast · 2 ausgeblendet, weil sie schon im Regal stehen.')
    serve({ 'GET /api/v1/discover/lists/best?hide_owned=false': list(books.slice(0, 3), { owned: 2, hide_owned: false }), 'PUT /api/v1/me/preferences': { discover_hide_owned: false } })
    await page.click(q('#hideOwned'))
    expect(q('.dsec-h p')!.textContent).toBe('Die meistgekauften Bücher der Woche. 3 Bücher · 2 davon im Regal.')
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ discover_hide_owned: false })
  })

  it('puts a book that is on the shelf under "Im Regal" instead of "Merken", and opens it as the book of the shelf', async () => {
    serve({ [BEST]: list([card('t1', { title: 'Der Leuchtturmwaerter', rank: 1, owned_work_id: 7 }), card('t2', { rank: 2 })], { hide_owned: true }) })
    await page.show(app(), '/discover?list=best')
    const first = all('.dgrid .dcard')[0]!
    expect(first.querySelector('.badge')!.textContent).toBe('Im Regal')
    expect(first.querySelector('button.badge')).toBeNull()
    expect(first.classList.contains('is-owned')).toBe(true)
    expect(all('.dgrid .dcard')[1]!.querySelector('.badge')!.textContent).toBe('Merken')
    await page.click(first.querySelector('.dopen') as HTMLElement)
    expect(q('[data-testid="where"]')!.textContent).toBe('/work/7')
  })

  it('opens a book that is not on the shelf at an address of its own', async () => {
    serve()
    await page.show(app(), '/discover?list=best')
    await page.click(all('.dgrid .dcard')[1]!.querySelector('.dopen') as HTMLElement)
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/t2')
  })
})

describe('the lists', () => {
  it('ask the server for the list that was pressed, and keep it in the address', async () => {
    serve({ 'GET /api/v1/discover/lists/new?hide_owned=true': list([card('n1', { title: 'Kaltes Licht', released: '2026-10-09' })], { list: 'new', sources: ['audible'] }) })
    await page.show(app(), '/discover?list=best')
    await page.click(page.button('Neu erschienen'))
    expect(server.called('GET', '/api/v1/discover/lists/new?hide_owned=true')).toBeTruthy()
    expect(q('.dsec-h h2')!.textContent).toBe('Neu erschienen')
    expect(q('.dsec-h p')!.textContent).toContain('Erschienen in den letzten acht Wochen, neueste zuerst.')
    expect(all('#dlists button').map((b) => b.classList.contains('on'))).toEqual([false, false, true, false, false, false])
  })

  it('print the day on new and on coming books, and no number on them', async () => {
    serve({
      'GET /api/v1/discover/lists/new?hide_owned=true': list([card('n1', { released: '2026-10-09' })], { list: 'new' }),
      'GET /api/v1/discover/lists/soon?hide_owned=true': list([card('s1', { released: '2026-12-04', upcoming: true })], { list: 'soon' }),
    })
    await page.show(app(), '/discover?list=best')
    await page.click(page.button('Neu erschienen'))
    expect(texts('.dgrid .when')).toEqual(['erschienen am 9. Okt.'])
    expect(all('.dgrid .rk')).toHaveLength(0)
    await page.click(page.button('Erscheint bald'))
    expect(texts('.dgrid .when')).toEqual(['erscheint am 4. Dez.'])
  })

  it('number the best sellers and the trending books, not the others', async () => {
    serve({ 'GET /api/v1/discover/lists/trending?hide_owned=true': list([card('r1', { rank: 1 }), card('r2', { rank: 2 })], { list: 'trending', sources: ['hardcover'] }) })
    await page.show(app(), '/discover?list=best')
    await page.click(page.button('Gerade beliebt'))
    expect(texts('.dgrid .rk')).toEqual(['1', '2'])
    expect(q('[data-testid="source"]')!.textContent).toBe('Quelle: Hardcover')
  })

  it('send the filters to the server and keep the market, the language and the switch in the profile', async () => {
    serve({
      'GET /api/v1/discover/lists/best?format=ebook&hide_owned=true': list(books),
      'GET /api/v1/discover/lists/best?format=ebook&language=en&hide_owned=true': list(books),
      'GET /api/v1/discover/lists/best?format=ebook&language=en&genre=krimi&hide_owned=true': list(books),
      'GET /api/v1/discover/lists/best?market=us&format=ebook&language=en&genre=krimi&hide_owned=true': list(books, { market: 'us' }),
    })
    await page.show(app(), '/discover?list=best')
    await page.click(page.button('E-Book'))
    await page.click(q('#dlang [data-lang="en"]'))
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ language: 'en' })
    await page.click(q('#dgenres [data-dg="krimi"]'))
    await page.click(q('#market [data-m="us"]'))
    expect(server.calls.filter((call) => call.method === 'PUT').map((call) => call.body)).toEqual([{ language: 'en' }, { market: 'us' }])
    expect(server.calls.at(-1)!.url).toContain('market=us')
    expect(q('[data-testid="source"]')!.textContent).toContain('USA')
  })

  it('take another market from the list, keep it in the profile, and show it among the buttons', async () => {
    serve({ 'GET /api/v1/discover/lists/best?market=ca&hide_owned=true': list(books, { market: 'ca' }) })
    await page.show(app(), '/discover?list=best')
    await page.choose(page.select('Weitere Märkte'), 'ca')
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ market: 'ca' })
    expect(texts('#market button')).toEqual(['Deutschland', 'USA', 'UK', 'Frankreich', 'Kanada'])
    expect(q('#market .on')!.textContent).toBe('Kanada')
    expect(q('[data-testid="source"]')!.textContent).toContain('Kanada')
  })

  it('offer the languages the list holds and the one chosen before, and call them by name', async () => {
    auth.me = { ...me, profile: { ...me.profile, language: 'fr' } } as Me
    serve({ 'GET /api/v1/discover/lists/best?language=fr&hide_owned=true': list([], { language: 'fr', languages: [{ code: 'de', count: 2 }] }) })
    await page.show(app(), '/discover?list=best')
    expect(texts('#dlang button')).toEqual(['Alle Sprachen', 'Deutsch', 'Französisch'])
    expect(all('#dlang button').map((b) => b.classList.contains('on'))).toEqual([false, false, true])
    expect(q('[data-testid="nothing"]')!.textContent).toBe('Mit diesen Filtern ist nichts dabei. ')
  })

  it('come in English too, with no dash as a pause', async () => {
    await changeLanguage('en', false)
    serve({ [BEST]: list(books.slice(0, 2), { owned: 1 }) })
    await page.show(app(), '/discover?list=best')
    expect(texts('#dlists button')).toEqual(['Browse', 'Best sellers', 'New releases', 'Coming soon', 'Trending now', 'For you'])
    expect(q('.dsec-h p')!.textContent).toBe('The best-selling books of the week. 2 books you do not have · 1 hidden because it is on your shelf already.')
    expect(q('[data-testid="source"]')!.textContent).toBe('Source: Apple Books Charts and Audible · Germany')
    expect(document.body.textContent).not.toMatch(/[–—]/)
  })
})

describe('when there is nothing to show', () => {
  const none = (state: string, over: Record<string, unknown> = {}) => list([], { state: state as never, asking: false, fetched_at: null, ...over })

  it('says that the sources are off and, to the operator, where to switch them on', async () => {
    serve({ [BEST]: none('main_off') })
    await page.show(app(), '/discover?list=best')
    const why = q('[data-testid="why-main-off"]')!
    expect(plain(why.textContent)).toBe('nexbooks fragt gerade keine Quellen im Internet. Unter Einstellungen, Quellen kannst du das einschalten.')
    expect(why.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=sources')
  })

  it('tells a member the same without a link', async () => {
    auth.me = { ...me, role: 'member' } as Me
    serve({ [BEST]: none('main_off') })
    await page.show(app(), '/discover?list=best')
    const why = q('[data-testid="why-main-off"]')!
    expect(why.textContent).toBe('nexbooks fragt gerade keine Quellen im Internet. Das kann der Betreiber in den Einstellungen einschalten.')
    expect(why.querySelector('a')).toBeNull()
  })

  it('says when no source is ready for this list, and when Hardcover has no account', async () => {
    serve({ [BEST]: none('no_source') })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="why-no-source"]')!.textContent).toContain('Für diese Liste ist keine Quelle bereit.')
    page.close()
    serve({ [BEST]: none('no_source', { availability: [{ source: 'hardcover', state: 'not_connected' }] }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="why-no-source"]')!.textContent).toContain('Diese Liste kommt von Hardcover und braucht ein Konto.')
  })

  it('says when the list comes from Audible and Audible is off, for the operator with the way to the settings', async () => {
    serve({ [BEST]: none('no_source', { availability: [{ source: 'audible', state: 'off' }] }) })
    await page.show(app(), '/discover?list=best')
    const why = q('[data-testid="why-no-source"]')!
    expect(plain(why.textContent)).toBe('Diese Liste kommt von Audible, und das ist bei dir ausgeschaltet. Unter Einstellungen, Quellen kannst du es einschalten.')
    expect(why.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=sources')
    page.close()
    auth.me = { ...me, role: 'member' } as Me
    serve({ [BEST]: none('no_source', { availability: [{ source: 'audible', state: 'off' }] }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="why-no-source"]')!.textContent).toBe('Diese Liste kommt von Audible, und das ist ausgeschaltet. Der Betreiber kann es in den Einstellungen einschalten.')
  })

  it('says that the list is being fetched and asks again after a few seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve({ [BEST]: none('fetching', { asking: true }) })
      await page.show(app(), '/discover?list=best')
      expect(q('[data-testid="why-fetching"]')!.textContent).toBe('Die Liste wird geholt. Das dauert einen Moment.')
      serve({ [BEST]: list(books) })
      await vi.advanceTimersByTimeAsync(4500)
      await vi.waitFor(() => expect(all('.dgrid .dcard')).toHaveLength(4))
    } finally {
      vi.useRealTimers()
    }
  })

  it('says the source answered with nothing, or is not answering', async () => {
    serve({ [BEST]: none('empty', { asking: true }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="why-empty"]')!.textContent).toContain('hat für diesen Markt gerade nichts geliefert')
    page.close()
    serve({ [BEST]: none('failed', { asking: true }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="why-failed"]')!.textContent).toContain('antwortet gerade nicht')
  })

  it('says that what stands there was kept earlier when the sources are off now', async () => {
    serve({ [BEST]: list(books, { asking: false }) })
    await page.show(app(), '/discover?list=best')
    expect(q('[data-testid="off-note"]')!.textContent).toBe('Die Quellen sind zurzeit ausgeschaltet. Das hier ist der Stand vom 12. Oktober 2026.')
    expect(all('.dgrid .dcard')).toHaveLength(4)
  })

  it('says that everything that fits is on the shelf already', async () => {
    serve({ [BEST]: list([], { owned: 3 }) })
    await page.show(app(), '/discover?list=best')
    expect(plain(q('[data-testid="nothing"]')!.textContent)).toBe('Mit diesen Filtern ist nichts dabei. Alles, was passt, steht schon in deinem Regal.')
  })

  it('shows a refusal of the server as a sentence', async () => {
    serve()
    server.fails = { [BEST]: { status: 429, code: 'discover_too_often' } }
    await page.show(app(), '/discover?list=best')
    expect(q('[role="alert"]')!.textContent).toBe('Zu viele Anfragen auf einmal. Warte eine Minute.')
  })
})

describe('the wish list', () => {
  const wished = card('t2', { title: 'Die Salzwiese', wished: true })

  it('puts a book on the list at once, counts it on the tab, and says so to the server', async () => {
    serve({ 'PUT /api/v1/wishlist/t2': { count: 1, items: [{ ...books[1]!, wished: true }] } })
    await page.show(app(), '/discover?list=best')
    const badge = all('.dgrid .dcard')[1]!.querySelector<HTMLElement>('button.badge')!
    expect(badge.textContent).toBe('Merken')
    await page.click(badge)
    expect(badge.textContent).toBe('Gemerkt')
    expect(badge.classList.contains('on')).toBe(true)
    expect(server.changes()).toEqual(['PUT /api/v1/wishlist/t2'])
    expect(q('.wishsec h2')!.textContent).toBe('Deine Merkliste 1')
    expect(texts('.wishsec .dcard b')).toEqual(['Die Salzwiese'])
  })

  it('sends one request for a double click', async () => {
    serve({ 'PUT /api/v1/wishlist/t2': { count: 1, items: [{ ...books[1]!, wished: true }] } })
    await page.show(app(), '/discover?list=best')
    const badge = all('.dgrid .dcard')[1]!.querySelector<HTMLElement>('button.badge')!
    await act(async () => {
      badge.click()
      badge.click()
    })
    expect(server.changes()).toEqual(['PUT /api/v1/wishlist/t2'])
  })

  it('takes a book off with no question, says so for a few seconds with "Rückgängig", and puts it back', async () => {
    serve({
      'GET /api/v1/wishlist': { count: 1, items: [wished] },
      [BEST]: list([card('t2', { title: 'Die Salzwiese', rank: 1, wished: true })]),
      'DELETE /api/v1/wishlist/t2': { count: 0, items: [] },
      'PUT /api/v1/wishlist/t2': { count: 1, items: [wished] },
    })
    await page.show(app(), '/discover?list=best')
    expect(q('.wishsec h2')!.textContent).toBe('Deine Merkliste 1')
    await page.click(all('.wishsec .dcard')[0]!.querySelector('button.badge') as HTMLElement)
    expect(server.changes()).toEqual(['DELETE /api/v1/wishlist/t2'])
    expect(q('.wishsec')).toBeNull()
    expect(plain(q('[data-testid="wish-undo"]')!.textContent)).toBe('„Die Salzwiese“ ist nicht mehr auf der Merkliste.Rückgängig')
    expect(q('.dgrid .badge')!.textContent).toBe('Merken')
    await page.click(page.button('Rückgängig'))
    expect(server.changes()).toEqual(['DELETE /api/v1/wishlist/t2', 'PUT /api/v1/wishlist/t2'])
    expect(q('[data-testid="wish-undo"]')).toBeNull()
    expect(q('.wishsec h2')!.textContent).toBe('Deine Merkliste 1')
  })

  it('lets the undo go after six seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve({ 'GET /api/v1/wishlist': { count: 1, items: [wished] }, 'DELETE /api/v1/wishlist/t2': { count: 0, items: [] } })
      await page.show(app(), '/discover?list=best')
      await page.click(all('.wishsec .dcard')[0]!.querySelector('button.badge') as HTMLElement)
      expect(q('[data-testid="wish-undo"]')).toBeTruthy()
      await act(async () => void (await vi.advanceTimersByTimeAsync(6500)))
      expect(q('[data-testid="wish-undo"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('goes back to what it was when the list is full, and says why', async () => {
    serve()
    server.fails = { 'PUT /api/v1/wishlist/t2': { status: 409, code: 'wishlist_full' } }
    await page.show(app(), '/discover?list=best')
    const badge = all('.dgrid .dcard')[1]!.querySelector<HTMLElement>('button.badge')!
    await page.click(badge)
    expect(badge.textContent).toBe('Merken')
    expect(q('[data-testid="wish-problem"]')!.textContent).toBe('Die Merkliste ist voll. Nimm erst ein Buch herunter.')
    expect(q('.wishcount')).toBeNull()
  })
})

describe('for you', () => {
  const own = work(9, { title: 'Die Krone der Weberinnen', series: { id: 3, name: 'Die Chroniken von Arvel', pos: 1 } })
  const arvel = gap({
    volumes: [
      volume(1, 'own', { work: own }),
      volume(2, 'missing', { book: card('g2', { title: 'Glas und Asche', series: 'Die Chroniken von Arvel', series_pos: 2, released: '2026-09-15', sources: ['hardcover'] }) }),
      volume(3, 'soon', { book: card('g3', { title: 'Der Thron aus Bernstein', series: 'Die Chroniken von Arvel', series_pos: 3, released: '2027-02-01', upcoming: true, sources: ['hardcover'] }) }),
    ],
  })
  const FOR = 'GET /api/v1/discover/for-you'

  it('shows the gaps as a card for each series: volumes as covers, the ones that are missing dashed, the one to come with its day', async () => {
    serve({ [FOR]: forYou({ gaps: [arvel] }) })
    await page.show(app(), '/discover?list=best')
    await page.click(page.button('Für dich'))
    expect(q('#dfilters')).toBeNull()
    expect(texts('.dsec-h h2').slice(0, 2)).toEqual(['Lücken in deinen Reihen', 'Mehr von deinen Autorinnen und Autoren'])
    const box = q('[data-testid="gap"]')!
    expect(box.querySelector('h3')!.textContent).toBe('Die Chroniken von Arvel')
    expect(plain(box.querySelector('.gmeta')!.textContent)).toBe('Iris Quist · 1 von 3 erschienenen Bänden im Regal · 2 fehlen, einer kommt bald')
    expect(box.querySelector('.gmeta b')!.textContent).toBe('1 von 3')
    const cards = [...box.querySelectorAll<HTMLElement>('.dcard')]
    expect(cards.map((c) => c.className.replace('dcard', '').trim())).toEqual(['', 'missing', 'missing soon'])
    expect(cards.map((c) => plain(c.querySelector('.vol')!.textContent))).toEqual(['Band 1', 'Band 2 · fehlt dir', 'Band 3 · kommt bald'])
    expect(cards[0]!.querySelector('.badge')!.textContent).toBe('Im Regal')
    expect(cards[1]!.querySelector('.badge')!.textContent).toBe('Merken')
    expect(cards[2]!.querySelector('.soon-date')!.textContent).toBe('erscheint am 1. Februar 2027')
    expect(q('[data-testid="no-authors"]')!.textContent).toBe('Von deinen Autorinnen und Autoren gibt es gerade nichts, was dir fehlt.')
  })

  it('shows more of the authors as rows of covers, with how many of theirs the person has', async () => {
    serve({ [FOR]: forYou({ authors: [{ person_id: 4, name: 'Mara Feldkamp', on_shelf: 2, pending: false, books: [card('a1', { title: 'Briefe aus Triest' }), card('a2', { title: 'Nach dem Sturm' })] }] }) })
    await page.show(app(), '/discover?list=foryou')
    const row = q('[data-testid="author"]')!
    expect(plain(row.querySelector('h3')!.textContent)).toBe('Mara Feldkamp 2 im Regal')
    expect(texts('[data-testid="author"] .dcard b')).toEqual(['Briefe aus Triest', 'Nach dem Sturm'])
    expect(q('[data-testid="no-gaps"]')!.textContent).toBe('In deinen Reihen fehlt gerade nichts.')
  })

  it('says it is looking up what is not known yet, and asks again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve({ [FOR]: forYou({ pending: true, authors: [{ person_id: 4, name: 'Mara Feldkamp', on_shelf: 2, pending: true, books: [] }] }) })
      await page.show(app(), '/discover?list=foryou')
      expect(q('[data-testid="foryou-pending"]')!.textContent).toBe('nexbooks sieht nach, was es dazu gibt. Das dauert einen Moment.')
      expect(q('[data-testid="no-gaps"]')!.textContent).toBe('Wird nachgesehen …')
      serve({ [FOR]: forYou({ gaps: [arvel] }) })
      await vi.advanceTimersByTimeAsync(4500)
      await vi.waitFor(() => expect(q('[data-testid="gap"]')).toBeTruthy())
      expect(q('[data-testid="foryou-pending"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('says what is missing when nothing can be asked, with the way to the settings for the operator', async () => {
    serve({ [FOR]: forYou({ state: 'main_off', sources: [] }) })
    await page.show(app(), '/discover?list=foryou')
    const why = q('[data-testid="foryou-main_off"]')!
    expect(plain(why.textContent)).toContain('nexbooks fragt gerade keine Quellen im Internet.')
    expect(why.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=sources')
  })

  it('opens a volume that is not on the shelf at an address of its own and one that is as the book of the shelf', async () => {
    serve({ [FOR]: forYou({ gaps: [arvel] }) })
    await page.show(app(), '/discover?list=foryou')
    await page.click(all('[data-testid="gap"] .dcard .dopen')[1] as HTMLElement)
    expect(q('[data-testid="where"]')!.textContent).toBe('/discover/book/g2')
  })
})
