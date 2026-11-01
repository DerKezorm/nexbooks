/**
 * "{name} beobachten" on the page of an author: the switch asks the server
 * and shows what its answer made; the choices (kind, language, "also non-fiction ...") change the watch; switching it off asks
 * first, with "the wishes stay" as the default, and sends nothing before the answer; a person who may not fetch books sees no
 * choice of kind and is told their books go onto the wish list. All invented.
 */
import { act, useState } from 'react'

import type { AuthorCatalog, AuthorWatch } from '../api/client'
import { changeLanguage } from '../i18n'
import { ProcureProvider } from '../state/procure'
import { WishProvider } from '../state/wish'
import { halloran, watchOf } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { counts } from '../test/procure'
import { idle } from '../test/wait'
import { WatchCard } from './WatchCard'

const me = { id: 1, name: 'jule', role: 'member', profile: {} }
const auth = { status: 'signedIn', me, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()
const PUT = 'PUT /api/v1/watching/authors/cat-token'
const STOP_KEEP = 'DELETE /api/v1/watching/authors/cat-token?wishes=keep'
const STOP_REMOVE = 'DELETE /api/v1/watching/authors/cat-token?wishes=remove'

function Holder({ start, died = null }: { start: AuthorCatalog; died?: number | null }) {
  const [catalog, setCatalog] = useState(start)
  return <WatchCard catalog={catalog} onChange={setCatalog} died={died} />
}

async function show(watch: AuthorWatch | null, allowed = true, over: Record<string, unknown> = {}, died: number | null = null) {
  server.answers = {
    'GET /api/v1/wishlist': { count: 0, items: [] },
    'GET /api/v1/procure/counts': counts({ allowed }),
    'GET /api/v1/procure/wishes': { wishes: [] },
    'GET /api/v1/procure/running': { downloads: [] },
    ...over,
  }
  server.fails = {}
  server.install()
  await page.show(
    <WishProvider>
      <ProcureProvider>
        <Holder start={halloran({ watch })} died={died} />
      </ProcureProvider>
    </WishProvider>,
  )
  await idle()
}

const card = () => document.querySelector('[data-testid="watch-card"]')!
const switchOf = () => document.querySelector<HTMLInputElement>('[data-testid="watch-switch"]')!

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the switch', () => {
  it('stands off, says what watching does for a person who may fetch books, and sends nothing by itself', async () => {
    await show(null)
    expect(plain(card().querySelector('h3')!.textContent)).toBe('Edmund Halloran beobachten')
    expect(plain(card().querySelector('.w-head p')!.textContent)).toContain('Neue Bücher von Edmund Halloran werden gewünscht, sobald sie angekündigt sind')
    expect(switchOf().checked).toBe(false)
    expect(plain(card().querySelector('.toggle b')!.textContent)).toBe('Beobachten')
    expect(card().querySelector('.w-opts')).toBeNull()
    expect(server.changes()).toEqual([])
  })

  it('switches on with the default choices and tells what the server made', async () => {
    await show(null, true, { [PUT]: watchOf({ made: { wished: 2, listed: 0, stopped: '' } }) })
    await page.click(switchOf())
    expect(server.called('PUT', '/api/v1/watching/authors/cat-token')!.body).toEqual({ kind: 'both', more: false })
    expect(switchOf().checked).toBe(true)
    expect(plain(card().querySelector('.toggle b')!.textContent)).toBe('Beobachtet')
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toBe('Du beobachtest Edmund Halloran. 2 Wünsche für angekündigte Bücher angelegt. Ansehen')
    expect(card().querySelector('[data-testid="watch-said"] a')!.getAttribute('href')).toBe('/acquire')
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe('Angekündigt und gewünscht: „Der Hüter der verlorenen Zimmer“.')
  })

  it('says one book in the singular, and nothing about wishes when nothing was announced', async () => {
    await show(null, true, { [PUT]: watchOf({ made: { wished: 1, listed: 0, stopped: '' } }) })
    await page.click(switchOf())
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toContain('1 angekündigtes Buch ist jetzt gewünscht.')
    page.close()
    await show(null, true, { [PUT]: watchOf({ made: { wished: 0, listed: 0, stopped: '' }, announced: [], next: null, wished: 0 }) })
    await page.click(switchOf())
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toBe('Du beobachtest Edmund Halloran.')
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe('Gerade ist nichts angekündigt.')
  })

  it('tells why a look stopped before the end', async () => {
    await show(null, true, { [PUT]: watchOf({ made: { wished: 0, listed: 0, stopped: 'no_indexers' } }) })
    await page.click(switchOf())
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toContain('Es ist kein Indexer eingeschaltet.')
  })

  it('asks the server once for a double press', async () => {
    await show(null, true, { [PUT]: watchOf() })
    await act(async () => {
      switchOf().click()
      switchOf().click()
    })
    await idle()
    expect(server.calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
  })

  it('says a refusal of the server and stays off', async () => {
    await show(null)
    server.fails = { [PUT]: { status: 409, code: 'too_many_watches' } }
    server.install()
    await page.click(switchOf())
    expect(plain(card().querySelector('[role="alert"]')!.textContent)).toContain('Du beobachtest so viele Autorinnen und Autoren')
    expect(switchOf().checked).toBe(false)
  })

  it('names the death of the author as the design does', async () => {
    await show(null, true, {}, 1999)
    expect(plain(card().querySelector('.w-head p')!.textContent)).toContain('Edmund Halloran ist 1999 gestorben; Beobachten lohnt sich trotzdem für Übersetzungen und Bücher aus dem Nachlass.')
  })
})

describe('the choices', () => {
  it('show the kind, the language and the box for more, as the watch has them', async () => {
    await show(watchOf({ kind: 'audio', language: 'en', target: 'en', more: true }))
    expect(card().classList.contains('on')).toBe(true)
    expect([...card().querySelectorAll('[role="radio"]')].map((item) => `${plain(item.textContent)}:${item.getAttribute('aria-checked')}`)).toEqual(['E-Book:false', 'Hörbuch:true', 'Beides:false'])
    const select = card().querySelector<HTMLSelectElement>('[data-testid="watch-language"]')!
    expect(select.value).toBe('en')
    // The languages of "Beschaffen", the first one first; the language of the account plays no part.
    expect([...select.options].map((option) => `${option.value}:${plain(option.textContent)}`)).toEqual([':Deutsch (erste Wahl)', 'en:Englisch (Original)'])
    expect(card().querySelector<HTMLInputElement>('[data-testid="watch-more"]')!.checked).toBe(true)
  })

  it('offer only the first language when books are fetched in one, and say when the chosen one is no longer fetched', async () => {
    await show(watchOf({ languages: ['de'] }))
    expect([...card().querySelector<HTMLSelectElement>('[data-testid="watch-language"]')!.options].map((option) => plain(option.textContent))).toEqual(['Deutsch (erste Wahl)'])
    page.close()
    await show(watchOf({ language: 'en', languages: ['de'], language_off: true }), true, { [PUT]: watchOf({ kind: 'ebook', languages: ['de'] }) })
    expect(card().querySelector<HTMLSelectElement>('[data-testid="watch-language"]')!.value).toBe('')
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toContain('Englisch wird nicht mehr beschafft; nexbooks wünscht jetzt auf Deutsch.')
    // The language no longer fetched is not sent back with another change.
    await page.click(card().querySelector('[data-wkind="ebook"]'))
    expect(server.called('PUT', '/api/v1/watching/authors/cat-token')!.body).toEqual({ kind: 'ebook', language: '', more: false })
  })

  it('change the watch with the other choices kept', async () => {
    await show(watchOf({ kind: 'both', language: 'en', target: 'en', more: false }), true, { [PUT]: watchOf({ kind: 'ebook', language: 'en', target: 'en' }) })
    await page.click(card().querySelector('[data-wkind="ebook"]'))
    expect(server.called('PUT', '/api/v1/watching/authors/cat-token')!.body).toEqual({ kind: 'ebook', language: 'en', more: false })
    await page.click(card().querySelector('[data-testid="watch-more"]'))
    // (The card shows what the server answered: the kind is the one just chosen.)
    expect(server.calls.filter((call) => call.method === 'PUT').at(-1)!.body).toEqual({ kind: 'ebook', language: 'en', more: true })
  })

  it('change the language, and say only the news of a look that made something', async () => {
    await show(watchOf(), true, { [PUT]: watchOf({ language: 'en', made: { wished: 1, listed: 0, stopped: '' } }) })
    const select = card().querySelector<HTMLSelectElement>('[data-testid="watch-language"]')!
    await act(async () => {
      select.value = 'en'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await idle()
    expect(server.calls.filter((call) => call.method === 'PUT').at(-1)!.body).toEqual({ kind: 'both', language: 'en', more: false })
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toContain('1 weiteres Buch ist jetzt gewünscht.')
  })

  it('leave out the kind for a person who may not fetch books, and say the books go onto the wish list', async () => {
    await show(watchOf({ can_acquire: false, listed: 1, wished: 0 }), false)
    expect(card().querySelector('[role="radiogroup"]')).toBeNull()
    expect(plain(card().querySelector('.w-head p')!.textContent)).toContain('landen auf deiner Merkliste')
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe('Angekündigt und gemerkt: „Der Hüter der verlorenen Zimmer“.')
  })

  it('say what is announced only in another language, and what waits for the catalogue', async () => {
    await show(watchOf({ announced: [], next: null, not_yet: [{ key: 'keeper', title: 'The Keeper of Lost Rooms', language: 'de', announced_in: 'en', listed: true }] }))
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe(
      'Gerade ist nichts angekündigt. „The Keeper of Lost Rooms“ ist auf Deutsch noch nicht angekündigt, nur auf Englisch; es steht so lange auf deiner Merkliste, und nexbooks wünscht es, sobald es so weit ist.',
    )
    page.close()
    await show(watchOf({ state: 'waiting', announced: [], next: null }))
    expect(plain(card().querySelector('.w-sum')!.textContent)).toBe('nexbooks holt gerade den Katalog von Edmund Halloran und schaut dann nach, was angekündigt ist.')
  })

  it('name the books announced that nothing was done for yet', async () => {
    await show(watchOf({ announced: [{ key: 'k', title: 'Das späte Licht', language: 'de', date: '2027-06-01', state: 'open', kinds: [] }] }))
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe('Angekündigt, noch nicht angelegt: „Das späte Licht“.')
  })

  it('say why a book is on the wish list instead of wished, what waits for its day, and what was not wished', async () => {
    await show(
      watchOf({
        announced: [
          { key: 'a', title: 'Der Hüter der verlorenen Zimmer', language: 'de', date: '2027-03-03', state: 'wished', kinds: ['ebook'] },
          { key: 'b', title: 'Das Moorhaus', language: 'de', date: '2027-03-01', state: 'listed', kinds: [], why: 'reissue' },
          { key: 'e', title: 'Die Papierbrücke', language: 'de', date: '2027-04-13', state: 'listed', kinds: [], why: 'translation' },
          { key: 'c', title: 'Das ferne Licht', language: 'de', date: '2027', state: 'year', kinds: [] },
          { key: 'd', title: 'Der Nebelchor', language: 'de', date: '2027-05-05', state: 'held', kinds: [], why: 'not_a_book' },
        ],
      }),
    )
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe(
      'Angekündigt und gewünscht: „Der Hüter der verlorenen Zimmer“. Auf deiner Merkliste statt gewünscht: „Das Moorhaus“ (vielleicht eine Neuauflage), „Die Papierbrücke“ (vielleicht eine neue Übersetzung). ' +
        'Angekündigt für 2027, noch ohne Tag: „Das ferne Licht“; nexbooks wünscht es, sobald der Tag feststeht. Nicht gewünscht: „Der Nebelchor“.',
    )
    expect(card().querySelector('[data-testid="watch-few-sources"]')).toBeNull()
  })

  it('say what somebody else wished for the library already, and why the translation of a book on the shelf waits on the list', async () => {
    await show(
      watchOf({
        announced: [
          { key: 'a', title: 'Der Hüter der verlorenen Zimmer', language: 'de', date: '2027-03-03', state: 'covered', kinds: ['ebook'] },
          { key: 'b', title: 'Das blaue Archiv', language: 'de', date: '2027-01-02', state: 'listed', kinds: [], why: 'own:en' },
        ],
      }),
    )
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toBe(
      'Auf deiner Merkliste statt gewünscht: „Das blaue Archiv“ (hast du auf Englisch). Schon für eure Bibliothek gewünscht: „Der Hüter der verlorenen Zimmer“.',
    )
  })

  it('say when the automatic work is switched off, on the card and after switching on', async () => {
    await show(watchOf({ paused: true }))
    expect(plain(card().querySelector('[data-testid="watch-summary"]')!.textContent)).toContain('Die Automatik ist ausgeschaltet. nexbooks wünscht nichts, bis sie wieder an ist.')
    page.close()
    await show(null, true, { [PUT]: watchOf({ paused: true, announced: [], next: null, made: { wished: 0, listed: 0, stopped: 'off' } }) })
    await page.click(switchOf())
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toBe(
      'Du beobachtest Edmund Halloran. Die Automatik ist ausgeschaltet. Dein Beobachten ist gespeichert; nexbooks wünscht, sobald sie wieder an ist.',
    )
  })

  it('say when the sources that know books to come are off', async () => {
    await show(watchOf({ sources_few: true }))
    expect(plain(card().querySelector('[data-testid="watch-few-sources"]')!.textContent)).toContain('Angekündigte Bücher kennen vor allem Audible und Hardcover, und beide sind aus.')
  })
})

describe('switching off', () => {
  it('asks first and sends nothing until the button is pressed', async () => {
    await show(watchOf(), true, { [STOP_KEEP]: 204 })
    await page.click(switchOf())
    expect(page.dialog).toBeTruthy()
    expect(plain(page.dialog!.textContent)).toContain('Edmund Halloran nicht mehr beobachten?')
    expect(server.changes()).toEqual([])
    // Escape and "Abbrechen" send nothing either.
    await page.click(page.button('Abbrechen', page.dialog!))
    expect(page.dialog).toBeFalsy()
    expect(server.changes()).toEqual([])
    expect(card().classList.contains('on')).toBe(true)
  })

  it('keeps the wishes by default and says so', async () => {
    await show(watchOf(), true, { [STOP_KEEP]: 204 })
    await page.click(switchOf())
    expect(page.dialog!.querySelector<HTMLInputElement>('[data-testid="watch-keep"]')!.checked).toBe(true)
    await page.click(page.dialog!.querySelector('[data-testid="watch-stop"]'))
    expect(server.changes()).toEqual(['DELETE /api/v1/watching/authors/cat-token?wishes=keep'])
    expect(switchOf().checked).toBe(false)
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toBe('Du beobachtest Edmund Halloran nicht mehr. Schon angelegte Wünsche bleiben.')
  })

  it('takes the wishes away only when that is chosen, and says it cannot be taken back', async () => {
    await show(watchOf(), true, { [STOP_REMOVE]: 204 })
    await page.click(switchOf())
    expect(plain(page.dialog!.textContent)).toContain('Das lässt sich nicht zurückholen.')
    await page.click(page.dialog!.querySelector('[data-testid="watch-remove"]'))
    await page.click(page.dialog!.querySelector('[data-testid="watch-stop"]'))
    expect(server.changes()).toEqual(['DELETE /api/v1/watching/authors/cat-token?wishes=remove'])
    expect(plain(card().querySelector('[data-testid="watch-said"]')!.textContent)).toContain('Die Wünsche, die noch nicht begonnen hatten, sind weg.')
  })

  it('tells a refusal inside the dialog, which stays open', async () => {
    await show(watchOf())
    server.fails = { [STOP_KEEP]: { status: 500, code: 'internal_error' } }
    server.install()
    await page.click(switchOf())
    await page.click(page.dialog!.querySelector('[data-testid="watch-stop"]'))
    expect(page.dialog).toBeTruthy()
    expect(page.dialog!.querySelector('[role="alert"]')).toBeTruthy()
    expect(card().classList.contains('on')).toBe(true)
  })
})
