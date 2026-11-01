/**
 * "Du beobachtest" at the top of Discover and the words "Über Beobachten von ..." in "Gesucht" and on the wish list. All invented.
 */
import { Route, Routes } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { Wanted } from '../pages/acquire/Wanted'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { watchOf } from '../test/catalog'
import { card } from '../test/discover'
import { Page, Server, plain } from '../test/harness'
import { counts, wish } from '../test/procure'
import { idle } from '../test/wait'
import { ForeignCard } from './ForeignCard'
import { WatchStrip } from './WatchStrip'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

async function strip(items: unknown[]) {
  server.answers = { 'GET /api/v1/watching': { items, limit: 100 } }
  server.install()
  await page.show(
    <Routes>
      <Route path="*" element={<WatchStrip />} />
    </Routes>,
  )
  await idle()
}

describe('"Du beobachtest"', () => {
  it('shows nothing for a person who watches nobody', async () => {
    await strip([])
    expect(document.querySelector('[data-testid="watch-strip"]')).toBeNull()
  })

  it('shows each author with the day of the next book, and the language when it is another one', async () => {
    await strip([
      watchOf(),
      watchOf({ token: 'tok2', name: 'Ida Falkner', person_id: 7, next: null, announced: [] }),
      watchOf({ token: 'tok3', name: 'Marit Lindqvist', language: 'sv', target: 'sv', next: { key: 'k', title: 'Skuggor', language: 'de', date: '2027-01-10', state: 'wished', kinds: ['ebook'] } }),
      watchOf({ token: 'tok4', name: 'Femke Aalders', state: 'waiting', next: null, announced: [] }),
      watchOf({ token: 'tok5', name: 'Ilse Brandt', next: { key: 'y', title: 'Das ferne Licht', language: 'de', date: '2027', state: 'year', kinds: [] } }),
    ])
    const chips = [...document.querySelectorAll('[data-testid="watch-chip"]')]
    expect(plain(document.querySelector('.ws-l')!.textContent)).toBe('Du beobachtest')
    expect(chips.map((chip) => plain(chip.textContent))).toEqual([
      'EHEdmund Halloran' + 'nächstes Buch am 3. März 2027',
      'IFIda Falkner' + 'nichts angekündigt',
      'MLMarit Lindqvist' + 'nächstes Buch am 10. Jan. 2027 (Deutsch)',
      'FAFemke Aalders' + 'wird vorbereitet',
      // A year without a day: no day is made up.
      'IBIlse Brandt' + 'nächstes Buch 2027',
    ])
    // The page of the person where the author is one of the libraries, else the page of the catalogue.
    expect(chips.map((chip) => chip.getAttribute('href'))).toEqual(['/author/cat-token', '/person/7', '/author/tok3', '/author/tok4', '/author/tok5'])
  })

  it('shows nothing when the list cannot be read', async () => {
    server.answers = {}
    server.install()
    await page.show(<WatchStrip />)
    await idle()
    expect(document.querySelector('[data-testid="watch-strip"]')).toBeNull()
  })
})

describe('where a wish came from', () => {
  beforeEach(() => window.localStorage.setItem('nexbooks.acquire.wanted', 'cards'))
  afterEach(() => window.localStorage.clear())

  it('says in "Gesucht" that a wish came through watching an author, and who wished the others', async () => {
    server.answers = { 'GET /api/v1/procure/running': { downloads: [] } }
    server.install()
    await page.show(
      <ProcureProvider>
        <Wanted wishes={[wish(1, { via: 'Edmund Halloran' }), wish(2, { via: '' })]} counts={counts()} onChanged={async () => undefined} />
      </ProcureProvider>,
    )
    const lines = [...document.querySelectorAll('.rq-who')].map((item) => plain(item.textContent))
    expect(lines[0]).toMatch(/^Über „Beobachten“ von Edmund Halloran · /)
    expect(lines[1]).toMatch(/^Gewünscht von Mira · /)
  })

  it('says on the wish list that the watching of an author put a book there', async () => {
    server.answers = { 'GET /api/v1/wishlist': { count: 2, items: [card('tokw1'), card('tokw2')], via: { tokw1: 'Edmund Halloran' } } }
    server.install()
    await page.show(
      <WishProvider>
        <ProcureProvider>
          <ForeignCard card={card('tokw1')} mode="row" onOpen={() => undefined} />
          <ForeignCard card={card('tokw2')} mode="row" onOpen={() => undefined} />
        </ProcureProvider>
      </WishProvider>,
    )
    await idle()
    const marks = [...document.querySelectorAll('[data-testid="via-watch"]')].map((item) => plain(item.textContent))
    expect(marks).toEqual(['Über „Beobachten“ von Edmund Halloran'])
  })

  it('says why a book the watching put on the wish list is not wished', async () => {
    server.answers = {
      'GET /api/v1/wishlist': {
        count: 2,
        items: [card('tokw1'), card('tokw2')],
        via: { tokw1: 'Edmund Halloran', tokw2: 'Edmund Halloran' },
        via_why: { tokw1: 'language:en', tokw2: 'reissue' },
      },
    }
    server.install()
    await page.show(
      <WishProvider>
        <ProcureProvider>
          <ForeignCard card={card('tokw1')} mode="row" onOpen={() => undefined} />
          <ForeignCard card={card('tokw2')} mode="row" onOpen={() => undefined} />
        </ProcureProvider>
      </WishProvider>,
    )
    await idle()
    const marks = [...document.querySelectorAll('[data-testid="via-watch"]')].map((item) => plain(item.textContent))
    expect(marks).toEqual(['Über „Beobachten“ von Edmund Halloran · angekündigt auf Englisch', 'Über „Beobachten“ von Edmund Halloran · vielleicht eine Neuauflage'])
  })
})
