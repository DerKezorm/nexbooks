/**
 * Opening and closing the page of a book with a quick hand: a double click opens the book once, two quick presses on two
 * spines open one book, and closing never lands on a book again or leaves a layer that nobody can see. The page is drawn
 * as the app draws it (the page it came from stays under the book, the book is the second set of routes).
 */
import { act, useEffect } from 'react'
import { Route, Routes, useLocation, useNavigationType, type Location } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { cameStraightFrom, historyIndex } from '../lib/openBook'
import { ebook, library, work, workOut } from '../test/books'
import { Page, Server } from '../test/harness'
import { LibraryPage } from './LibraryPage'
import { WorkPage } from './WorkPage'

const baseMe = { id: 1, name: 'jule', display_name: 'Jule', role: 'operator', profile: { view: 'shelf' } } as unknown as Me
const auth = { me: baseMe as Me | null, setMe: vi.fn() }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const books = [work(1, { title: 'Eins', genre: 'roman' }), work(2, { title: 'Zwei', genre: 'roman' }), work(3, { title: 'Drei', genre: 'krimi' })]
const stats = { works: 3, editions: 3, ebook_editions: 3, audio_editions: 0, works_with_ebook: 3, works_with_audio: 0, genres: { roman: 2, krimi: 1 }, languages: {} }

/** What the router did: each place the real location was at, with how it got there. */
let trail: string[] = []

function Shell() {
  const location = useLocation()
  const type = useNavigationType()
  useEffect(() => {
    trail.push(`${type} ${location.pathname}`)
  }, [location, type])
  const background = (location.state as { background?: Location } | null)?.background
  return (
    <>
      <Routes location={background ?? location}>
        <Route path="/" element={<LibraryPage />} />
        <Route path="*" element={<div />} />
      </Routes>
      {background && (
        <Routes>
          <Route path="work/:id" element={<WorkPage />} />
        </Routes>
      )}
      <div data-testid="real">{location.pathname}</div>
    </>
  )
}

function serve() {
  server.answers = {
    'GET /api/v1/libraries': [library()],
    'GET /api/v1/works?page=1&per_page=200': { items: books, total: books.length, page: 1, per_page: 200 },
    'GET /api/v1/works/stats': stats,
    'GET /api/v1/works/1': workOut(1, [ebook(1)], { title: 'Eins' }),
    'GET /api/v1/works/2': workOut(2, [ebook(2)], { title: 'Zwei' }),
    'GET /api/v1/works/3': workOut(3, [ebook(3)], { title: 'Drei' }),
  }
  server.fails = {}
  server.install()
}

const real = () => document.querySelector('[data-testid="real"]')!.textContent
const spine = (id: number) => document.querySelector(`.spine[data-work="${id}"]`) as HTMLElement
const layer = () => document.querySelector('[data-testid="work"]')
const escape = () => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 30))))

beforeEach(async () => {
  await changeLanguage('de', false)
  trail = []
  auth.me = baseMe
  // Nothing turns or fades here: the page closes at once.
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
  serve()
})
afterEach(() => {
  page.close()
  window.history.replaceState(null, '')
  vi.unstubAllGlobals()
})

describe('a double click on a spine', () => {
  it('opens the book once: one entry in the history, not two', async () => {
    await page.show(<Shell />)
    await act(async () => {
      spine(3).click()
      spine(3).click()
    })
    await settle()
    expect(trail.filter((step) => step === 'PUSH /work/3')).toHaveLength(1)
    expect(real()).toBe('/work/3')
  })

  it('leaves the library as it was after Escape: no layer, the page free to scroll', async () => {
    await page.show(<Shell />)
    await act(async () => {
      spine(3).click()
      spine(3).click()
    })
    await settle()
    expect(layer()).toBeTruthy()
    await escape()
    await settle()
    expect(real()).toBe('/')
    expect(layer()).toBeNull()
    expect(document.body.classList.contains('lock')).toBe(false)
    expect(document.querySelector('.spine')).toBeTruthy()
  })

  it('leaves the library as it was after the button "Bibliothek"', async () => {
    await page.show(<Shell />)
    await act(async () => {
      spine(3).click()
      spine(3).click()
    })
    await settle()
    await page.click(document.querySelector('.d-back') as HTMLElement)
    await settle()
    expect(real()).toBe('/')
    expect(layer()).toBeNull()
    expect(document.body.classList.contains('lock')).toBe(false)
  })
})

describe('two quick presses on two spines', () => {
  it('open the first book only', async () => {
    await page.show(<Shell />)
    await act(async () => {
      spine(2).click()
      spine(3).click()
    })
    await settle()
    expect(real()).toBe('/work/2')
    expect(trail.filter((step) => step.startsWith('PUSH /work/'))).toEqual(['PUSH /work/2'])
  })

  it('open a second book once the first is closed again', async () => {
    await page.show(<Shell />)
    await act(async () => spine(2).click())
    await settle()
    await escape()
    await settle()
    expect(real()).toBe('/')
    await act(async () => spine(3).click())
    await settle()
    expect(real()).toBe('/work/3')
  })
})

describe('closing', () => {
  it('goes one step back when the entry before is the one the book was opened from', () => {
    window.history.replaceState({ idx: 4 }, '')
    expect(historyIndex()).toBe(4)
    expect(cameStraightFrom({ background: { pathname: '/' } as Location, from: 3 })).toBe(true)
  })

  it('does not trust "back" when the entry before is something else (the book twice, a position that is not known)', () => {
    window.history.replaceState({ idx: 5 }, '')
    expect(cameStraightFrom({ background: { pathname: '/' } as Location, from: 3 })).toBe(false)
    window.history.replaceState(null, '')
    expect(historyIndex()).toBeNull()
    expect(cameStraightFrom({ background: { pathname: '/' } as Location, from: 3 })).toBe(false)
    expect(cameStraightFrom({ background: { pathname: '/' } as Location })).toBe(false)
    expect(cameStraightFrom(null)).toBe(false)
  })

  it('pops the history when the entry before is the origin', async () => {
    // A browser history keeps the position of each entry; the book is opened from entry 0 and stands on entry 1.
    window.history.replaceState({ idx: 0 }, '')
    await page.show(<Shell />)
    await act(async () => spine(3).click())
    await settle()
    window.history.replaceState({ idx: 1 }, '')
    await escape()
    await settle()
    expect(real()).toBe('/')
    expect(trail.at(-1)).toBe('POP /')
    expect(layer()).toBeNull()
  })

  it('replaces the book by the page it came from when the entry before is not the origin', async () => {
    window.history.replaceState({ idx: 0 }, '')
    await page.show(<Shell />)
    await act(async () => spine(3).click())
    await settle()
    window.history.replaceState({ idx: 2 }, '') // as after a book pushed twice
    await escape()
    await settle()
    expect(real()).toBe('/')
    expect(trail.at(-1)).toBe('REPLACE /')
    expect(layer()).toBeNull()
    expect(document.body.classList.contains('lock')).toBe(false)
  })
})

describe('the turn to the page of a book', () => {
  type Step = { from: string; duration: number; easing: string; keyframes: number }
  let steps: Step[]

  beforeEach(() => {
    steps = []
    // The animations of a real browser are not here: each step is noted and counts as done at once.
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
    Element.prototype.getAnimations = () => []
    Element.prototype.animate = function (this: Element, keyframes: Keyframe[] | PropertyIndexedKeyframes | null, options?: number | KeyframeAnimationOptions) {
      const timing = options as KeyframeAnimationOptions
      steps.push({ from: this.className, duration: Number(timing.duration), easing: String(timing.easing), keyframes: Array.isArray(keyframes) ? keyframes.length : 0 })
      return { finished: Promise.resolve() } as unknown as Animation
    }
  })
  it('turns a spine in two steps: 360 ms out, then 420 ms in, with the curves of the design', async () => {
    await page.show(<Shell />)
    await act(async () => spine(3).click())
    await settle()
    expect(steps).toEqual([
      { from: 'f-spine', duration: 360, easing: 'cubic-bezier(.5,0,.75,0)', keyframes: 2 },
      { from: 'f-cover', duration: 420, easing: 'cubic-bezier(.2,.7,.3,1)', keyframes: 2 },
    ])
  })

  it('grows the cover of a card of the grid in one step of 520 ms', async () => {
    auth.me = { ...baseMe, profile: { view: 'grid' } } as unknown as Me
    await page.show(<Shell />)
    await act(async () => (document.querySelector('.book[data-work="3"]') as HTMLElement).click())
    await settle()
    expect(steps).toEqual([{ from: 'f-cover', duration: 520, easing: 'cubic-bezier(.2,.8,.2,1)', keyframes: 2 }])
  })

  it('starts the growing at the cover of the card, not at the card', async () => {
    auth.me = { ...baseMe, profile: { view: 'grid' } } as unknown as Me
    const rect = (left: number, top: number, width: number, height: number) => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect
    const original = Element.prototype.getBoundingClientRect
    Element.prototype.getBoundingClientRect = function (this: Element) {
      if (this.classList.contains('book')) return rect(10, 20, 150, 360) // the whole card
      if (this.closest('.book') && this.classList.contains('cover')) return rect(10, 20, 150, 225) // its cover
      return rect(300, 100, 330, 495) // the cover on the page
    }
    try {
      await page.show(<Shell />)
      await act(async () => (document.querySelector('.book[data-work="3"]') as HTMLElement).click())
      await settle()
      const host = document.querySelector('.f-cover') as HTMLElement
      expect([host.style.left, host.style.top, host.style.width, host.style.height]).toEqual(['10px', '20px', '150px', '225px'])
    } finally {
      Element.prototype.getBoundingClientRect = original
    }
  })
})
