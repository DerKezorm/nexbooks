/**
 * The search field of the head: the key `/`, the library asked after a breath, the catalogues of the sources asked
 * after a longer one and only when the operator has any on, two groups with their names, the keys of the list, and the way
 * to the shelf (a hit leads to the library with the number of the book).
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import { changeLanguage } from '../i18n'
import { ebook, recording, work } from '../test/books'
import { catalogHit } from '../test/catalog'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { SearchBox } from './SearchBox'

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{`${location.pathname}|${JSON.stringify((location.state as { goto?: number } | null)?.goto ?? null)}`}</div>
}

function app() {
  return (
    <>
      <SearchBox />
      <input id="other" />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </>
  )
}

const hits = [work(1, { title: 'Das Haus am Nordkanal', genre: 'roman', editions: [ebook(1), recording(2)] }), work(2, { title: 'Nordlicht', genre: 'krimi', authors: [{ id: 2, name: 'Eva Müller' }] })]
const catalog = { available: true, failed: false, pending: false, ticket: 'ticket-0001', items: [catalogHit('tok-nordsee-0001')] }

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.useFakeTimers({ shouldAdvanceTime: true })
  server.answers = { 'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: hits }, 'POST /api/v1/search/catalog': catalog }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function typeAndWait(text: string, ms = 800) {
  await page.type(document.querySelector('#q') as HTMLInputElement, text)
  await act(async () => void (await vi.advanceTimersByTimeAsync(ms)))
  await idle()
}

describe('the field', () => {
  it('stands in the head with its words and the key that goes to it', async () => {
    await page.show(app())
    const input = document.querySelector('#q') as HTMLInputElement
    expect(input.getAttribute('placeholder')).toBe('Titel, Autorin, Reihe …')
    expect(input.getAttribute('aria-label')).toBe('Suchen')
    expect(document.querySelector('.search kbd')!.textContent).toBe('/')
    expect(document.querySelector('.results')).toBeNull()
  })

  it('takes the key slash from anywhere that is not a field, and leaves it to a field that is', async () => {
    await page.show(app())
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true })))
    expect(document.activeElement?.id).toBe('q')
    ;(document.querySelector('#other') as HTMLInputElement).focus()
    const event = new KeyboardEvent('keydown', { key: '/', bubbles: true, cancelable: true })
    await act(async () => void document.querySelector('#other')!.dispatchEvent(event))
    expect(event.defaultPrevented).toBe(false)
    expect(document.activeElement?.id).toBe('other')
  })
})

describe('the library', () => {
  it('is asked after a breath, not for every letter, and shows its hits under their group', async () => {
    await page.show(app())
    await page.type(document.querySelector('#q') as HTMLInputElement, 'n')
    await page.type(document.querySelector('#q') as HTMLInputElement, 'no')
    await page.type(document.querySelector('#q') as HTMLInputElement, 'nord')
    await act(async () => void (await vi.advanceTimersByTimeAsync(200)))
    await idle()
    expect(server.calls.filter((c) => c.url.startsWith('/api/v1/search?')).map((c) => c.url)).toEqual(['/api/v1/search?q=nord&limit=8'])
    expect(document.querySelector('.res-head')!.textContent).toBe('In deiner Bibliothek')
    const rows = [...document.querySelectorAll('.res:not(.is-catalog)')]
    expect(rows.map((r) => plain(r.querySelector('b')!.textContent))).toEqual(['Das Haus am Nordkanal', 'Nordlicht'])
    expect(rows.map((r) => plain(r.querySelector('small')!.textContent))).toEqual(['Ada Feldkamp · Romane', 'Eva Müller · Krimi & Thriller'])
    expect(rows[0]!.classList.contains('act')).toBe(true)
  })

  it('says that nothing was found, and shows no list for an empty field', async () => {
    server.answers['GET /api/v1/search?q=xyz&limit=8'] = { query: 'xyz', items: [] }
    server.answers['POST /api/v1/search/catalog'] = { available: false, failed: false, items: [] }
    await page.show(app())
    await typeAndWait('xyz')
    expect(document.querySelector('.results .none')!.textContent).toBe('Nichts gefunden.')
    await typeAndWait('')
    expect(document.querySelector('.results')).toBeNull()
  })

  it('moves with the arrows, goes to the shelf with Enter, and closes with Escape', async () => {
    await page.show(app())
    await typeAndWait('nord')
    const input = document.querySelector('#q') as HTMLInputElement
    const press = async (key: string) => act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })))
    await press('ArrowDown')
    expect([...document.querySelectorAll('.res')].map((r) => r.classList.contains('act')).slice(0, 2)).toEqual([false, true])
    await press('ArrowDown')
    // The arrows go on to the hit of the catalogue (the third), and stop there.
    expect([...document.querySelectorAll('.res')].map((r) => r.classList.contains('act'))).toEqual([false, false, true])
    await press('ArrowDown')
    expect([...document.querySelectorAll('.res')].map((r) => r.classList.contains('act'))).toEqual([false, false, true])
    await press('ArrowUp')
    await press('ArrowUp')
    expect(document.querySelector('.res')!.classList.contains('act')).toBe(true)
    await press('ArrowDown')
    await press('Enter')
    // To the library, with the number of the book the person chose.
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/|2')
    expect(document.querySelector('.results')).toBeNull()
    await typeAndWait('nord ')
    expect(document.querySelector('.results')).toBeTruthy()
    await press('Escape')
    expect(document.querySelector('.results')).toBeNull()
  })

  it('goes to the shelf with a click on a hit, too', async () => {
    await page.show(app())
    await typeAndWait('nord')
    await page.click(document.querySelectorAll('.res:not(.is-catalog)')[0] as HTMLElement)
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/|1')
  })
})

describe('the catalogue', () => {
  it('is asked after a longer breath, shown under its own group with its sources and "not on the shelf", and opens the page of the book', async () => {
    await page.show(app())
    await typeAndWait('nord', 300)
    expect(server.calls.filter((c) => c.method === 'POST').length).toBe(0)
    await act(async () => void (await vi.advanceTimersByTimeAsync(600)))
    await idle()
    expect(server.called('POST', '/api/v1/search/catalog')!.body).toEqual({ q: 'nord' })
    const heads = [...document.querySelectorAll('.res-head')].map((h) => h.textContent)
    expect(heads).toEqual(['In deiner Bibliothek', 'Im Katalog · Open Library, Hardcover'])
    const hit = document.querySelector('[data-testid="catalog-hit"]') as HTMLElement
    expect(plain(hit.querySelector('b')!.textContent)).toBe('Nordsee Notizen')
    expect(plain(hit.querySelector('small')!.textContent)).toBe('Mara Lindqvist · 2021')
    expect([...hit.querySelectorAll('.chip')].map((c) => c.textContent)).toEqual(['nicht im Regal'])
    expect(hit.getAttribute('role')).toBe('row')
    await page.click(hit)
    // The page of a book that is not on a shelf has an address of its own.
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/discover/book/tok-nordsee-0001|null')
  })

  it('leads a hit that is on the shelf to the book on the shelf, like a hit of the library', async () => {
    server.answers['POST /api/v1/search/catalog'] = { ...catalog, items: [{ ...catalog.items[0]!, owned_work_id: 7 }] }
    await page.show(app())
    await typeAndWait('nord', 1000)
    const hit = document.querySelector('[data-testid="catalog-hit"]') as HTMLElement
    expect([...hit.querySelectorAll('.chip')].map((c) => c.textContent)).toEqual(['Im Regal'])
    await page.click(hit)
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/|7')
  })

  it('goes through the hits of the library and then those of the catalogue with the arrows, and Enter opens the one it stands on', async () => {
    await page.show(app())
    await typeAndWait('nord', 1000)
    const field = document.querySelector('#q') as HTMLInputElement
    await act(async () => {
      for (let i = 0; i < 2; i++) field.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    expect(document.querySelector('[data-testid="catalog-hit"]')!.classList.contains('act')).toBe(true)
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    await idle()
    expect(document.querySelector('[data-testid="where"]')!.textContent).toBe('/discover/book/tok-nordsee-0001|null')
  })

  it('is not asked for a word of one or two letters, and not again once it said that no source is on', async () => {
    server.answers['POST /api/v1/search/catalog'] = { available: false, failed: false, items: [] }
    server.answers['GET /api/v1/search?q=no&limit=8'] = { query: 'no', items: [] }
    await page.show(app())
    await typeAndWait('no', 1000)
    expect(server.calls.filter((c) => c.method === 'POST').length).toBe(0)
    await typeAndWait('nord', 1000)
    expect(server.calls.filter((c) => c.method === 'POST').length).toBe(1)
    await typeAndWait('nordsee', 1000)
    expect(server.calls.filter((c) => c.method === 'POST').length).toBe(1)
    expect([...document.querySelectorAll('.res-head')].map((h) => h.textContent)).not.toContain('Im Katalog')
  })

  it('says when the catalogue does not answer, and keeps what the library found', async () => {
    server.answers['POST /api/v1/search/catalog'] = { available: true, failed: true, items: [] }
    await page.show(app())
    await typeAndWait('nord', 1500)
    expect(document.querySelectorAll('.res:not(.is-catalog)').length).toBe(2)
    expect([...document.querySelectorAll('.results .none')].map((n) => n.textContent)).toEqual(['Der Katalog antwortet gerade nicht.'])
  })

  it('does not ask a hit away that came late: an answer to an older question is dropped', async () => {
    let release: (value: Response) => void = () => undefined
    const original = globalThis.fetch
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === '/api/v1/search?q=nord&limit=8') return new Promise<Response>((resolve) => (release = resolve))
        return original(url, init)
      }),
    )
    await page.show(app())
    const input = document.querySelector('#q') as HTMLInputElement
    // Typed without waiting for the fake API: the first answer is held back on purpose.
    const write = async (value: string) =>
      act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
    await write('nord')
    await act(async () => void (await vi.advanceTimersByTimeAsync(200)))
    server.answers['GET /api/v1/search?q=nordl&limit=8'] = { query: 'nordl', items: [hits[1]!] }
    await write('nordl')
    await act(async () => void (await vi.advanceTimersByTimeAsync(200)))
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 20))))
    release(new Response(JSON.stringify({ query: 'nord', items: hits }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await idle()
    expect([...document.querySelectorAll('.res:not(.is-catalog) b')].map((b) => b.textContent)).toEqual(['Nordlicht'])
  })
})

describe('in English', () => {
  it('speaks English and never pauses with a dash', async () => {
    await changeLanguage('en', false)
    await page.show(app())
    expect(document.querySelector('#q')!.getAttribute('placeholder')).toBe('Title, author, series …')
    await typeAndWait('nord', 1500)
    expect([...document.querySelectorAll('.res-head')].map((h) => h.textContent)).toEqual(['In your library', 'In the catalogue · Open Library, Hardcover'])
    expect(document.querySelector('.results')!.textContent).not.toMatch(/[–—]/)
  })
})
