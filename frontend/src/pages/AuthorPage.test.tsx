/**
 * The page of an author nobody needs a book of: the head, "Kommt bald"
 * with the day in each language, where the catalogue comes from, the filters, the sections, the marks of the editions,
 * "Alle fehlenden merken" after a question for a person who may not fetch books and "Alle fehlenden beschaffen" through
 * the dialog for one who may, and "Verschieben nach …" with its way back. All invented.
 */
import { Route, Routes, useLocation } from 'react-router-dom'

import type { AuthorCatalog, Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { halloran, watchOf } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { counts, options } from '../test/procure'
import { idle } from '../test/wait'
import { pollDelay } from '../lib/catalog'
import { PersonPage } from './PersonPage'

const me = { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()
const AUTHOR = 'GET /api/v1/catalog/authors/cat-token'

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname}</div>
}

function serve(catalog: AuthorCatalog, allowed = false, over: Record<string, unknown> = {}) {
  server.answers = {
    [AUTHOR]: catalog,
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/procure/counts': counts({ allowed }),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }] },
    ...over,
  }
  server.fails = {}
  server.install()
}

async function show(at = '/author/cat-token') {
  await page.show(
    <WishProvider>
      <ProcureProvider>
        <Routes>
          <Route path="author/:token" element={<PersonPage />} />
          <Route path="person/:id" element={<Where />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </ProcureProvider>
    </WishProvider>,
    at,
  )
  await idle()
}

const sectionTitles = () => [...document.querySelectorAll('[data-testid="author-section"] h2')].map((h) => plain(h.textContent))
const workTitles = (section: string) => [...document.querySelectorAll(`[data-section="${section}"] [data-testid="work"] .at-t, [data-section="${section}"] [data-testid="work"] td.c-t > .at-t`)].map((b) => plain(b.textContent))

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the head', () => {
  it('names the author, the other name, the languages and the numbers, with the initials where there is no photo', async () => {
    serve(halloran())
    await show()
    const hero = document.querySelector('[data-testid="author-hero"]')!
    expect(plain(hero.querySelector('h1')!.textContent)).toBe('Edmund Halloran')
    expect(plain(hero.querySelector('[data-testid="author-initials"]')!.textContent)).toBe('EH')
    expect(plain(hero.querySelector('[data-testid="author-alias"]')!.textContent)).toBe('Schreibt auch als Richard Vane.')
    expect(plain(hero.querySelector('.au-langs')!.textContent)).toBe('SprachenEnglisch · OriginalDeutsch')
    expect(plain(hero.querySelector('[data-testid="author-facts"]')!.textContent)).toBe('9Werke0im Regal1angekündigt1974 bis 2026erschienen')
    // "beobachten" stands below the head, off for a person who does not watch the author.
    expect(plain(document.querySelector('[data-testid="watch-card"] h3')!.textContent)).toBe('Edmund Halloran beobachten')
    expect(document.querySelector<HTMLInputElement>('[data-testid="watch-switch"]')!.checked).toBe(false)
  })

  it('leads a token of an author of the person\'s libraries to the person\'s page', async () => {
    serve(halloran({ person_id: 7 }))
    await show()
    expect(plain(document.querySelector('[data-testid="where"]')!.textContent)).toBe('/person/7')
  })

  it('says when the catalogue is being built, and when there is no such author', async () => {
    serve(halloran({ state: 'building', built_at: null, works: [] }))
    await show()
    expect(plain(document.querySelector('[data-testid="catalog-state"]')!.textContent)).toContain('nexbooks fragt gerade die Quellen nach dem Werk von Edmund Halloran')
    page.close()
    serve(halloran())
    server.fails = { [AUTHOR]: { status: 404, code: 'not_found' } }
    server.install()
    await show()
    expect(plain(document.querySelector('[role="alert"]')!.textContent)).toBe('Diese Person kennt nexbooks nicht.')
  })
})

describe('watching', () => {
  it('shows the watch the server has, below the head', async () => {
    serve(halloran({ watch: watchOf() }), true)
    await show()
    const card = document.querySelector('[data-testid="watch-card"]')!
    expect(card.classList.contains('on')).toBe(true)
    expect(document.querySelector<HTMLInputElement>('[data-testid="watch-switch"]')!.checked).toBe(true)
    expect(plain(card.querySelector('[data-testid="watch-summary"]')!.textContent)).toBe('Angekündigt und gewünscht: „Der Hüter der verlorenen Zimmer“.')
    // Below the head, above "Kommt bald".
    const order = [...document.querySelectorAll('[data-testid="author-hero"], [data-testid="watch-card"], [data-testid="author-soon"]')].map((item) => item.getAttribute('data-testid'))
    expect(order).toEqual(['author-hero', 'watch-card', 'author-soon'])
  })

  it('asks again while a watch waits for its first look, and then shows what it made', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve(halloran({ watch: watchOf({ state: 'waiting', announced: [], next: null, wished: 0 }) }), true)
      await show()
      expect(plain(document.querySelector('[data-testid="watch-card"] .w-sum')!.textContent)).toBe('nexbooks holt gerade den Katalog von Edmund Halloran und schaut dann nach, was angekündigt ist.')
      serve(halloran({ watch: watchOf() }), true)
      await vi.advanceTimersByTimeAsync(4500)
      await vi.waitFor(() => expect(document.querySelector('[data-testid="watch-summary"]')).not.toBeNull())
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not ask again for a catalogue that is built and a watch that has had its look', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      serve(halloran({ watch: watchOf() }), true)
      await show()
      const reads = () => server.calls.filter((call) => call.url === '/api/v1/catalog/authors/cat-token').length
      const before = reads()
      await vi.advanceTimersByTimeAsync(9000)
      expect(reads()).toBe(before)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('an author nobody of the libraries is', () => {
  it('shows the photo, who made it and the short text the catalogue found, and says when the name is shared', async () => {
    serve(
      halloran({
        photo_url: '/api/v1/catalog/authors/cat-token/photo?v=0123456789ab',
        credit: { source: 'wikidata', author: 'Ina Fotograf', license: 'CC BY-SA 4.0', license_url: 'https://creativecommons.org/licenses/by-sa/4.0/', page_url: 'https://commons.wikimedia.org/wiki/File:Beispiel.jpg' },
        bio: { texts: [{ text: 'erfundener Schriftsteller', language: 'de', source: 'wikidata', url: 'https://www.wikidata.org/wiki/Q9000001' }], born: 1951, died: null },
        common_name: true,
      }),
    )
    await show()
    const hero = document.querySelector('[data-testid="author-hero"]')!
    expect(hero.querySelector('img')!.getAttribute('src')).toBe('/api/v1/catalog/authors/cat-token/photo?v=0123456789ab')
    expect(hero.querySelector('[data-testid="author-initials"]')).toBeNull()
    expect(plain(hero.querySelector('[data-testid="photo-credit"]')!.textContent)).toContain('CC BY-SA 4.0')
    expect(plain(hero.querySelector('[data-testid="author-bio"]')!.textContent)).toContain('erfundener Schriftsteller')
    expect(plain(document.querySelector('[data-testid="common-name"]')!.textContent)).toBe('Mehrere Menschen heißen Edmund Halloran. Was nachweislich von jemand anderem ist, fehlt hier, und was keine Quelle sicher zuordnet, steht unter „Nicht sicher zugeordnet“.')
  })

  it('says nothing of a shared name when the sources know one person of it', async () => {
    serve(halloran())
    await show()
    expect(document.querySelector('[data-testid="common-name"]')).toBeNull()
  })

  it('asks for a catalogue that is being built until it is there, less often after a while', () => {
    expect(pollDelay(0)).toBe(4000)
    expect(pollDelay(14)).toBe(4000)
    expect(pollDelay(15)).toBeGreaterThan(4000)
    expect(pollDelay(45)).toBe(30000)
    expect(pollDelay(1000)).toBe(30000)
  })
})

describe('"Kommt bald"', () => {
  it('gives the day in each language, the original first', async () => {
    serve(halloran())
    await show()
    const soon = document.querySelector('[data-testid="soon-work"]')!
    expect(plain(soon.querySelector('b')!.textContent)).toBe('Der Hüter der verlorenen Zimmer')
    expect([...soon.querySelectorAll('li')].map((li) => plain(li.textContent))).toEqual(['Englisch (Original): erscheint am 15. November 2026', 'Deutsch: erscheint am 3. März 2027'])
  })

  it('says what the watch does with a book to come in the place of the button', async () => {
    const year = { key: 'keeper', title: 'Der Hüter der verlorenen Zimmer', language: 'de', date: '2027', state: 'year' as const, kinds: [] }
    serve(halloran({ watch: watchOf({ announced: [year], next: year }) }), true)
    await show()
    const soon = () => document.querySelector('[data-testid="soon-work"]')!
    expect(plain(soon().querySelector('[data-testid="soon-watch"]')!.textContent)).toBe('wird gewünscht, sobald der Tag feststeht')
    expect(soon().querySelector('.mini-act')).toBeNull()
    page.close()
    serve(halloran({ watch: watchOf({ announced: [], next: null, not_yet: [{ key: 'keeper', title: 'The Keeper of Lost Rooms', language: 'de', announced_in: 'en', listed: true }] }) }), true)
    await show()
    expect(plain(soon().querySelector('[data-testid="soon-watch"]')!.textContent)).toBe('auf der Merkliste, bis es auf Deutsch angekündigt ist')
    page.close()
    const covered = { key: 'keeper', title: 'Der Hüter der verlorenen Zimmer', language: 'de', date: '2027-03-03', state: 'covered' as const, kinds: ['ebook' as const] }
    serve(halloran({ watch: watchOf({ announced: [covered], next: covered }) }), true)
    await show()
    expect(plain(soon().querySelector('[data-testid="soon-watch"]')!.textContent)).toBe('schon für eure Bibliothek gewünscht')
  })
})

describe('the works', () => {
  it('stand by kind in the person\'s language, with what is left out named and a way to show it', async () => {
    serve(halloran())
    await show()
    expect(sectionTitles()).toEqual(['Reihe · Wrenfield 2', 'Romane 3', 'Erzählbände 1', 'Als Richard Vane 1', 'Nicht sicher zugeordnet 1'])
    expect(workTitles('k:roman')).toEqual(['Die Salztöchter', 'Ruß', 'Der Hüter der verlorenen Zimmer'])
    const hidden = document.querySelector('[data-testid="hidden-language"]')!
    expect(plain(hidden.textContent)).toBe('1 Buch gibt es nicht auf Deutsch. Alle Sprachen zeigen')
    await page.click(page.button('Alle Sprachen zeigen', hidden))
    expect(workTitles('k:roman')).toEqual(['The Salt Daughters', 'Soot', 'Tidewater', 'The Keeper of Lost Rooms'])
    expect(document.querySelector('[data-testid="hidden-language"]')).toBeNull()
  })

  it('show the original title under the translation, and the marks of the editions', async () => {
    serve(halloran())
    await show()
    const salt = document.querySelector('[data-key="salt"]')!
    expect(plain(salt.querySelector('.at-sub')!.textContent)).toBe('Original: The Salt Daughters')
    expect([...salt.querySelectorAll('.fp')].map((fp) => fp.getAttribute('data-mark'))).toEqual(['ebook:avail', 'audio:avail'])
    const soot = document.querySelector('[data-key="soot"]')!
    expect([...soot.querySelectorAll('.fp')].map((fp) => fp.getAttribute('data-mark'))).toEqual(['ebook:avail', 'audio:none'])
  })

  it('stand by year, and as a list', async () => {
    serve(halloran())
    await show()
    await page.click(page.button('Nach Jahr'))
    expect(sectionTitles()).toEqual(['2020er 2', '1980er 4', '1970er 1', 'Nicht sicher zugeordnet 1'])
    await page.click(page.button('Liste'))
    expect(document.querySelectorAll('.atab').length).toBe(4)
    expect(plain(document.querySelector('[data-key="ash"] .c-t')!.textContent)).toBe('Die AschenstraßeWrenfield, Band 1Original: The Ash Road')
  })

  it('hide what the person has when asked', async () => {
    const catalog = halloran()
    catalog.works[1] = { ...catalog.works[1]!, work: { ...(await import('../test/books')).work(9, { title: 'Ruß' }) }, have_ebook: true }
    serve(catalog)
    await show()
    expect(workTitles('k:roman')).toContain('Ruß')
    await page.check(document.querySelector<HTMLInputElement>('[data-testid="hide-owned"]'))
    expect(workTitles('k:roman')).not.toContain('Ruß')
    expect(plain(document.querySelector('.au-legend')!.textContent)).toContain('1 ausgeblendet, das du schon hast.')
  })
})

describe('where the catalogue comes from', () => {
  it('names the sources, how many works are sure and how sure it is', async () => {
    serve(halloran())
    await show()
    const note = document.querySelector('[data-testid="catalog-note"]')!
    expect(plain(note.querySelector('.cn-s')!.textContent)).toBe('Woher dieser Katalog kommtOpen Library, Deutsche Nationalbibliothek · 8 von 9 Werken sicher erkannt, 1 unsicher')
    expect(plain(note.querySelector('.meter em')!.textContent)).toBe('hoch')
  })

  it('starts with the numbers, not with a dot, for an author no source knows', async () => {
    serve({ ...halloran(), sources: [], works: [], sure: 0, unsure: 0 })
    await show()
    const note = document.querySelector('[data-testid="catalog-note"]')!
    expect(plain(note.querySelector('.cn-s span')!.textContent)).toBe('0 von 0 Werken sicher erkannt')
    serve({ ...halloran(), sources: [], works: [], sure: 0, unsure: 2 })
    page.close()
    await show()
    expect(plain(document.querySelector('[data-testid="catalog-note"] .cn-s span')!.textContent)).toBe('0 von 0 Werken sicher erkannt, 2 unsicher')
  })
})

describe('a person who may not fetch books', () => {
  it('saves all missing books of a section after a question, and sends nothing when it is cancelled', async () => {
    serve(halloran())
    await show()
    const bulk = document.querySelector('[data-section="k:roman"] [data-testid="bulk"]')!
    expect(plain(bulk.textContent)).toBe('Alle fehlenden merken 3')
    await page.click(bulk)
    expect(plain(page.dialog!.textContent)).toContain('3 Bücher merken?')
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(bulk)
    await page.inDialog('Alle merken')
    expect(server.changes()).toEqual(['PUT /api/v1/wishlist/tok-salt', 'PUT /api/v1/wishlist/tok-soot', 'PUT /api/v1/wishlist/tok-keeper'])
    expect(plain(document.querySelector('[data-testid="author-notice"]')!.textContent)).toContain('3 Bücher gemerkt.')
  })

  it('cannot move a work', async () => {
    serve(halloran())
    await show()
    expect(document.querySelector('[data-testid="move-work"]')).toBeNull()
  })
})

describe('a person who may fetch books', () => {
  it('opens the dialog of every other place for one work and for all missing ones, with their number', async () => {
    serve(halloran(), true, { 'GET /api/v1/procure/book-options?book=tok-salt': options() })
    await show()
    const get = document.querySelector('[data-key="salt"] [data-testid="work-get"]')!
    expect(plain(get.textContent)).toBe('Beschaffen')
    expect(plain(document.querySelector('[data-key="keeper"] [data-testid="work-get"]')!.textContent)).toBe('Vormerken')
    await page.click(document.querySelector('[data-section="k:roman"] [data-testid="bulk"]'))
    expect(page.dialog).toBeTruthy()
    expect(plain(page.dialog!.textContent)).toContain('3')
    expect(server.changes()).toEqual([])
  })

  it('moves an unsure work to a kind, and takes it back', async () => {
    const moved = halloran({ can_move: true })
    moved.works = moved.works.map((w) => (w.key === 'comic' ? { ...w, kind: 'erz', why: '', moved: true } : w))
    serve(halloran({ can_move: true }), true, { 'PUT /api/v1/catalog/authors/cat-token/works/comic/kind': moved, 'DELETE /api/v1/catalog/authors/cat-token/works/comic/kind': halloran({ can_move: true }) })
    await show()
    await page.choose(page.select('„Wrenfield. Die Graphic Novel“ verschieben nach'), 'erz')
    expect(server.called('PUT', '/api/v1/catalog/authors/cat-token/works/comic/kind')!.body).toEqual({ kind: 'erz' })
    expect(sectionTitles()).toContain('Erzählbände 2')
    const notice = document.querySelector('[data-testid="author-notice"]')!
    expect(plain(notice.textContent)).toContain('„Wrenfield. Die Graphic Novel“ steht jetzt bei Erzählbände. nexbooks merkt sich das.')
    await page.click(page.button('Rückgängig', notice))
    expect(server.changes()).toContain('DELETE /api/v1/catalog/authors/cat-token/works/comic/kind')
    expect(sectionTitles()).toContain('Nicht sicher zugeordnet 1')
  })
})

describe('in English', () => {
  it('speaks English and never pauses with a dash', async () => {
    await changeLanguage('en', false)
    serve(halloran())
    await show()
    const text = plain(document.querySelector('.au-page')!.textContent)
    expect(text).toContain('The works')
    expect(text).toContain('Not placed for sure')
    expect(text).not.toMatch(/[–—]/)
  })
})
