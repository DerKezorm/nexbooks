/**
 * The authors in the search: a group of their own before the person's books, the authors of the
 * person's libraries first and those the catalogues know after them, each with a photo or the initials, the number of works,
 * how many stand on the shelf and the other name the words found; Enter or a press opens the page of the author.
 */
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'

import type { AuthorHit } from '../api/client'
import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { SearchBox } from './SearchBox'

const server = new Server()
const page = new Page()

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname}</div>
}

function app() {
  return (
    <>
      <SearchBox />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </>
  )
}

const own: AuthorHit = { name: 'Edmund Halloran', person_id: 7, token: null, alias: '', works: 49, on_shelf: 4, soon: 2, photo_url: null, genre: 'krimi' }
const theirs: AuthorHit = { name: 'Ida Nordmann', person_id: null, token: 'tok-ida-0001', alias: 'Ida Nord', works: null, on_shelf: 0, soon: 0, photo_url: null, genre: '' }
const catalog = { available: true, failed: false, items: [] }

function serve(mine: AuthorHit[], fromSources: AuthorHit[]) {
  server.answers = {
    'GET /api/v1/search?q=nord&limit=8': { query: 'nord', items: [work(1, { title: 'Das Haus am Nordkanal' })] },
    'GET /api/v1/search/people?q=nord&limit=4': { items: mine },
    'POST /api/v1/search/catalog': { ...catalog, people: fromSources },
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.useFakeTimers({ shouldAdvanceTime: true })
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

describe('the authors in the search', () => {
  it('stand before the person\'s own books, theirs first, with the numbers and the other name', async () => {
    serve([own], [{ ...own, token: 'tok-own' }, theirs])
    await page.show(app())
    await typeAndWait('nord')
    const heads = [...document.querySelectorAll('.res-head')].map((head) => plain(head.textContent))
    expect(heads.slice(0, 2)).toEqual(['Autorinnen und Autoren', 'In deiner Bibliothek'])
    const people = [...document.querySelectorAll('[data-testid="author-hit"]')]
    expect(people.map((hit) => plain(hit.querySelector('b')!.textContent))).toEqual(['Edmund Halloran', 'Ida Nordmann'])
    expect(plain(people[0]!.querySelector('small')!.textContent)).toBe('Krimi & Thriller · 49 Werke · 4 davon im Regal')
    expect(plain(people[1]!.querySelector('small')!.textContent)).toBe('auch als Ida Nord')
    expect(plain(people[0]!.querySelector('.av')!.textContent)).toBe('EH')
  })

  it('open the page of the author with Enter and with a press', async () => {
    serve([own], [theirs])
    await page.show(app())
    await typeAndWait('nord')
    const input = document.querySelector('#q') as HTMLInputElement
    await act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(plain(document.querySelector('[data-testid="where"]')!.textContent)).toBe('/person/7')
    await typeAndWait('')
    await typeAndWait('nord')
    await page.click(document.querySelectorAll('[data-testid="author-hit"]')[1])
    expect(plain(document.querySelector('[data-testid="where"]')!.textContent)).toBe('/author/tok-ida-0001')
  })

  it('make the page of an author the search only named when it is opened, and not while the person types', async () => {
    const named: AuthorHit = { ...theirs, token: null }
    serve([], [named])
    server.answers['POST /api/v1/catalog/authors'] = { token: 'tok-new-0001', person_id: null }
    await page.show(app())
    await typeAndWait('nord')
    expect(server.called('POST', '/api/v1/catalog/authors')).toBeUndefined()
    await page.click(document.querySelector('[data-testid="author-hit"]'))
    await idle()
    expect(server.called('POST', '/api/v1/catalog/authors')!.body).toEqual({ name: 'Ida Nordmann' })
    expect(plain(document.querySelector('[data-testid="where"]')!.textContent)).toBe('/author/tok-new-0001')
  })

  it('go first through the arrows, then the books', async () => {
    serve([own], [])
    await page.show(app())
    await typeAndWait('nord')
    const input = document.querySelector('#q') as HTMLInputElement
    await act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    expect(document.querySelector('[role="row"][aria-selected="true"]')!.getAttribute('data-id')).toBe('1')
  })

  it('are not asked for a single letter', async () => {
    serve([], [])
    await page.show(app())
    await typeAndWait('n')
    expect(server.calls.some((call) => call.url.startsWith('/api/v1/search/people'))).toBe(false)
  })
})
