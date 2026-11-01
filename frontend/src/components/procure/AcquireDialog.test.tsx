/**
 * "Beschaffen": what the dialog offers a book, in its words; one request, however often the
 * button is pressed; the note when there is no indexer (the way to the settings only for the operator); and the dialog for
 * many books at once.
 */
import { act } from 'react'

import type { Me } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { options } from '../../test/procure'
import { Page, Server } from '../../test/harness'
import { idle } from '../../test/wait'
import { AcquireDialog, type AcquireBook } from './AcquireDialog'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const BOOK: AcquireBook = { workId: 7, title: 'Die Laternenstraße', authors: ['Rune Falkner'], series: 'Nebelchor', series_pos: 2, color: '#4f2b47', cover_url: null }
const OPTIONS_URL = 'GET /api/v1/procure/book-options?work_id=7'

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

async function show(extra: Partial<Parameters<typeof AcquireDialog>[0]> = {}, answers: Record<string, unknown> = {}) {
  server.answers = { [OPTIONS_URL]: options(), 'POST /api/v1/procure/wishes': { results: [{ index: 0, kind: 'ebook', status: 'created', code: '', wish: { state: 'searching' } }] }, ...answers }
  server.fails = {}
  server.install()
  const done = vi.fn()
  const close = vi.fn()
  await page.show(<AcquireDialog books={[BOOK]} onClose={close} onDone={done} {...extra} />)
  return { done, close }
}

describe('the dialog Beschaffen', () => {
  it('offers e-book, audiobook and both, the languages allowed, the library, and says what it will take', async () => {
    await show()
    const dialog = page.dialog!
    expect(dialog.textContent).toContain('Die Laternenstraße')
    expect(dialog.textContent).toContain('Nebelchor, Band 2')
    expect([...dialog.querySelectorAll('[role="radio"]')].map((item) => item.textContent)).toEqual(['E-Book', 'Hörbuch', 'Beides'])
    // Both is chosen when both are free.
    expect(dialog.querySelector('[role="radio"][aria-checked="true"]')!.textContent).toBe('Beides')
    const language = dialog.querySelector('#acq-lang') as HTMLSelectElement
    // Only the languages the operator allows, all of them chosen; the account's language plays no part.
    expect([...language.options].map((item) => item.textContent)).toEqual(['Alle erlaubten (Deutsch, sonst Englisch)', 'Deutsch', 'Englisch (Original)'])
    expect(language.value).toBe('')
    expect(dialog.querySelector('[data-testid="acquire-language-hint"]')!.textContent).toBe('Erlaubt sind die Sprachen unter Einstellungen, Beschaffung, Formate und Ablage, Sprachen.')
    const place = dialog.querySelector('#acq-lib') as HTMLSelectElement
    expect([...place.options].map((item) => item.textContent)).toEqual(['Nach Art (E-Books in Bücher, Hörbücher in Bücher)', 'Bücher', 'Familie'])
    const sum = dialog.querySelector('[data-testid="acquire-sum"]')!.textContent!
    expect(sum).toContain('E-Book: nimmt EPUB, dann AZW3, dann MOBI und weitere, Ziel EPUB.')
    expect(sum).toContain('Hörbuch: nimmt FLAC, dann M4B/AAC hoch, dann MP3 320 und weitere, Ziel FLAC. Nur ungekürzt.')
  })

  it('asks for what was chosen, once, however often the button is pressed', async () => {
    const { done } = await show()
    await page.click(page.dialog!.querySelector('[data-what="audio"]'))
    expect(page.dialog!.querySelector('[data-testid="acquire-sum"]')!.textContent).not.toContain('E-Book:')
    const go = page.dialog!.querySelector('[data-testid="acquire-go"]') as HTMLButtonElement
    await act(async () => {
      go.click()
      go.click()
    })
    await idle()
    expect(server.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toEqual({ items: [{ work_id: 7 }], kinds: ['audio'] })
    expect(done).toHaveBeenCalledWith({ text: 'Wunsch angelegt: Die Laternenstraße. nexbooks sucht jetzt.' })
  })

  it('asks for the library when several take the kind and the operator chose none, and sends nothing before', async () => {
    const base = options()
    await show({}, { [OPTIONS_URL]: options({ kinds: base.kinds.map((entry) => ({ ...entry, default_library: null, choose: entry.kind === 'ebook' })) }) })
    const dialog = page.dialog!
    const place = dialog.querySelector('#acq-lib') as HTMLSelectElement
    expect(place.options[0].textContent).toBe('Bibliothek wählen')
    expect(place.options[0].disabled).toBe(true)
    expect(dialog.querySelector('[data-testid="acquire-choose"]')!.textContent).toContain('Mehrere Bibliotheken nehmen diese Art von Buch.')
    const go = dialog.querySelector<HTMLButtonElement>('[data-testid="acquire-go"]')!
    expect(go.disabled).toBe(true)
    await page.click(go)
    expect(server.called('POST', '/api/v1/procure/wishes')).toBeUndefined()
    await page.choose(place, '2')
    expect(dialog.querySelector('[data-testid="acquire-choose"]')).toBeNull()
    await page.click(go)
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toMatchObject({ library_id: 2 })
  })

  it('sends the language and the library the person chose', async () => {
    await show()
    await page.choose(page.dialog!.querySelector('#acq-lang'), 'en')
    await page.choose(page.dialog!.querySelector('#acq-lib'), '2')
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toEqual({ items: [{ work_id: 7 }], kinds: ['ebook', 'audio'], language: 'en', library_id: 2 })
  })

  it('names the one language allowed and asks for it, whatever the language of the account', async () => {
    auth.me = { id: 1, name: 'jule', role: 'member', profile: {}, language: 'en' } as unknown as Me
    await show({}, { [OPTIONS_URL]: options({ languages: ['de'], original_language: 'en' }) })
    const language = page.dialog!.querySelector('#acq-lang') as HTMLSelectElement
    expect([...language.options].map((item) => item.textContent)).toEqual(['Deutsch'])
    expect(language.value).toBe('de')
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toEqual({ items: [{ work_id: 7 }], kinds: ['ebook', 'audio'], language: 'de' })
  })

  it('offers the original language only when the operator allows it', async () => {
    await show({}, { [OPTIONS_URL]: options({ languages: ['de', 'fr'], original_language: 'en' }) })
    const language = page.dialog!.querySelector('#acq-lang') as HTMLSelectElement
    expect([...language.options].map((item) => item.value)).toEqual(['', 'de', 'fr'])
    expect(language.value).toBe('')
  })

  it('proposes a writable library, and chooses it, when the one nexbooks would use is read-only', async () => {
    const base = options()
    const libraries = [{ id: 1, name: 'Bücher', writable: false, write_problem: 'read_only' }, { id: 2, name: 'Familie', writable: true, write_problem: '' }]
    await show({}, { [OPTIONS_URL]: options({ kinds: base.kinds.map((entry) => ({ ...entry, libraries })) }) })
    const place = page.dialog!.querySelector('#acq-lib') as HTMLSelectElement
    expect(place.value).toBe('2')
    expect([...place.options].map((item) => item.textContent)).toEqual(['Nach Art (E-Books in Familie, Hörbücher in Familie)', 'Bücher (nicht beschreibbar: schreibgeschützt eingebunden)', 'Familie'])
    // The library that is not writable is not left out, it is greyed out with its reason.
    expect([...place.options].map((item) => item.disabled)).toEqual([false, true, false])
    expect(page.dialog!.querySelector('[data-testid="acquire-unwritable"]')!.textContent).toBe('Ausgegraut, weil nexbooks dort nicht schreiben darf: Bücher (schreibgeschützt eingebunden).')
    expect(page.dialog!.querySelector('[data-testid="acquire-proposal"]')!.textContent).toBe('Die Ablage nach Art ist nicht beschreibbar. nexbooks schlägt „Familie“ vor.')
    expect(page.dialog!.querySelector('[data-testid="acquire-readonly"]')).toBeNull()
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toEqual({ items: [{ work_id: 7 }], kinds: ['ebook', 'audio'], library_id: 2 })
  })

  it('greys out a library that is read-only, names the reason, and warns if it is chosen all the same', async () => {
    const base = options()
    const libraries = [{ id: 1, name: 'Bücher', writable: true, write_problem: '' }, { id: 2, name: 'Familie', writable: false, write_problem: 'no_permission' }]
    await show({}, { [OPTIONS_URL]: options({ kinds: base.kinds.map((entry) => ({ ...entry, libraries })) }) })
    expect(page.dialog!.querySelector('[data-testid="acquire-readonly"]')).toBeNull()
    const place = page.dialog!.querySelector('#acq-lib') as HTMLSelectElement
    const familie = [...place.options].find((item) => item.value === '2')!
    expect([familie.disabled, familie.textContent]).toEqual([true, 'Familie (nicht beschreibbar: kein Schreibrecht)'])
    expect(page.dialog!.querySelector('[data-testid="acquire-unwritable"]')!.textContent).toBe('Ausgegraut, weil nexbooks dort nicht schreiben darf: Familie (kein Schreibrecht).')
    // A choice the page cannot offer (set from outside the list) still says why it takes nothing.
    await page.choose(place, '2')
    expect(page.dialog!.querySelector('[data-testid="acquire-readonly"]')!.textContent).toBe('„Familie“ ist nur lesbar. nexbooks kann dort nichts ablegen. Gib dem Container Schreibrecht. Wähle eine beschreibbare Ablage.')
  })

  it('says that the wish waits when no library is writable', async () => {
    const base = options()
    const libraries = [{ id: 1, name: 'Bücher', writable: false, write_problem: 'read_only' }, { id: 2, name: 'Familie', writable: false, write_problem: 'read_only' }]
    await show({}, { [OPTIONS_URL]: options({ kinds: base.kinds.map((entry) => ({ ...entry, libraries })) }) })
    expect(page.dialog!.querySelector('[data-testid="acquire-proposal"]')).toBeNull()
    expect(page.dialog!.querySelector('[data-testid="acquire-readonly"]')!.textContent).toContain('Keine deiner Ablagen ist beschreibbar. Der Wunsch wartet')
  })

  it('offers only the kinds that are free and says why the others are not', async () => {
    const base = options()
    await show({}, { [OPTIONS_URL]: options({ kinds: [{ ...base.kinds[0], state: 'have' }, { ...base.kinds[1], state: 'wished' }, { ...base.kinds[1], kind: 'audio' }] }) })
    // The e-book is on the shelf, the audiobook is free in the last entry (the first audio entry is wished).
    expect(page.dialog!.textContent).toContain('E-Book steht schon im Regal.')
  })

  it('says that all is under way when nothing is free', async () => {
    const base = options()
    await show({}, { [OPTIONS_URL]: options({ kinds: [{ ...base.kinds[0], state: 'have' }, { ...base.kinds[1], state: 'have' }] }) })
    expect(page.dialog!.querySelector('[data-testid="acquire-nothing"]')!.textContent).toBe('Beide Fassungen stehen schon im Regal.')
    expect(page.dialog!.querySelector('[data-testid="acquire-go"]')).toBeNull()
  })

  it('tells a member without an indexer to ask the operator, and the operator where to go', async () => {
    await show({}, { [OPTIONS_URL]: options({ indexers: 0 }) })
    expect(page.dialog!.querySelector('[data-testid="acquire-no-indexer"]')!.textContent).toContain('Bitte die Betreiberin oder den Betreiber')
    expect([...page.dialog!.querySelectorAll('a')]).toHaveLength(0)
    page.close()
    auth.me = { id: 1, name: 'jule', role: 'operator', profile: {} } as unknown as Me
    await show({}, { [OPTIONS_URL]: options({ indexers: 0 }) })
    expect([...page.dialog!.querySelectorAll('a')].find((link) => link.getAttribute('href') === '/settings?tab=procure&sub=indexers')!.textContent).toBe('Zu den Einstellungen')
  })

  it('names the day a book comes out and the switched-off automatic work', async () => {
    await show({}, { [OPTIONS_URL]: options({ release_date: '2027-03-04', automatic: false }) })
    expect(page.dialog!.querySelector('[data-testid="acquire-not-out"]')!.textContent).toContain('Erscheint am 4. März 2027.')
    expect(page.dialog!.querySelector('[data-testid="acquire-manual"]')!.textContent).toContain('Die Automatik ist aus.')
  })

  it('shows the refusal of the server and stays open', async () => {
    const { done } = await show()
    server.fails['POST /api/v1/procure/wishes'] = { status: 409, code: 'no_indexers' }
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    expect(page.dialog!.querySelector('[data-testid="acquire-failure"]')!.textContent).toBeTruthy()
    expect(done).not.toHaveBeenCalled()
    // The button works again after the refusal.
    expect((page.dialog!.querySelector('[data-testid="acquire-go"]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('asks for many books in one go, with the same kinds and language for all', async () => {
    const books: AcquireBook[] = [1, 2, 3, 4].map((n) => ({ token: `tok${n}`, title: `Buch ${n}`, authors: [], series: '', series_pos: null, color: '#7d2f2b', cover_url: null }))
    server.answers = { 'GET /api/v1/procure/book-options?book=tok1': options(), 'POST /api/v1/procure/wishes': { results: books.map((_, index) => ({ index, kind: 'ebook', status: 'created', code: '', wish: null })) } }
    server.fails = {}
    server.install()
    const done = vi.fn()
    await page.show(<AcquireDialog books={books} kinds={['ebook']} onClose={() => undefined} onDone={done} />)
    expect(page.dialog!.getAttribute('aria-label')).toBe('4 Bücher beschaffen?')
    expect(page.dialog!.querySelector('[data-testid="acquire-many"]')!.textContent).toContain('Buch 1 · Buch 2 · Buch 3 · +1')
    expect(page.dialog!.querySelector('[role="radio"][aria-checked="true"]')!.textContent).toBe('E-Book')
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    expect(server.called('POST', '/api/v1/procure/wishes')!.body).toEqual({ items: [{ book: 'tok1' }, { book: 'tok2' }, { book: 'tok3' }, { book: 'tok4' }], kinds: ['ebook'] })
    expect(done).toHaveBeenCalledWith({ text: '4 Wünsche angelegt.' })
  })

  it('sends a list longer than the server takes at once in parts, and says nothing of the first book’s original language', async () => {
    const books: AcquireBook[] = Array.from({ length: 155 }, (_, i) => ({ token: `t${i}`, title: `Buch ${i}`, authors: [], series: '', series_pos: null, color: '#7d2f2b', cover_url: null }))
    server.answers = { 'GET /api/v1/procure/book-options?book=t0': { ...options(), languages: ['de'], original_language: 'de' }, 'POST /api/v1/procure/wishes': { results: [{ index: 0, kind: 'ebook', status: 'created', code: '', wish: null }] } }
    server.fails = {}
    server.install()
    await page.show(<AcquireDialog books={books} kinds={['ebook']} onClose={() => undefined} onDone={vi.fn()} />)
    expect(page.dialog!.querySelector('select')?.textContent ?? '').not.toContain('Original')
    await page.click(page.dialog!.querySelector('[data-testid="acquire-go"]'))
    const posts = server.calls.filter((c) => c.method === 'POST' && c.url === '/api/v1/procure/wishes')
    expect(posts.map((c) => (c.body as { items: unknown[] }).items.length)).toEqual([100, 55])
    expect((posts[1].body as { items: { book: string }[] }).items[0]).toEqual({ book: 't100' })
  })
})
