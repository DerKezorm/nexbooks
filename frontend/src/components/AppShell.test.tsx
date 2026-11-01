/**
 * The frame of the design and its three areas: the head with the mark, the three areas in their order (the active one
 * marked), the round button for light and dark and the account menu; below it the views with their title, their line
 * and their empty sentence; between the areas and the buttons the search field.
 */
import { Route, Routes } from 'react-router-dom'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { DiscoverPage } from '../pages/DiscoverPage'
import { LibraryPage } from '../pages/LibraryPage'
import { StackPage } from '../pages/StackPage'
import { WishProvider } from '../state/wish'
import { wall } from '../test/browse'
import { Page, Server } from '../test/harness'
import { AppShell } from './AppShell'

const me = {
  id: 1, name: 'jule', display_name: 'Jule', role: 'operator', avatar: null, version: '0.1.0', whats_new_seen: '0.1.0',
  profile: { theme: 'system', view: 'shelf' },
} as unknown as Me
const auth = { status: 'signedIn', me: me as Me | null, signOut: async () => undefined, refresh: async () => undefined }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function app() {
  return (
    <WishProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<LibraryPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="stack" element={<StackPage />} />
        </Route>
      </Routes>
    </WishProvider>
  )
}

beforeEach(async () => {
  const kept = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
  })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
  document.documentElement.removeAttribute('data-theme')
  await changeLanguage('de', false)
  auth.me = me
  server.answers = {
    'PUT /api/v1/me/preferences': { theme: 'dark', view: 'shelf' },
    'GET /api/v1/libraries': [],
    'GET /api/v1/stack': { years: [], today: '2026-10-14' },
    'GET /api/v1/wishlist': { count: 0, items: [] },
    // No source is on: the page says what is missing.
    'GET /api/v1/discover/lists/best?hide_owned=true': {
      list: 'best', market: 'de', format: 'all', language: '', genre: '', hide_owned: true, state: 'main_off', availability: [], asking: false, sources: ['apple', 'audible'],
      fetched_at: null, languages: [], markets: ['de', 'us', 'uk', 'fr'], total: 0, owned: 0, items: [],
    },
    // Browse is the first tab of Discover: its wall says the same.
    'GET /api/v1/discover/browse?hide_owned=true': wall([], { state: 'main_off', availability: [], asking: false, sources: [], fetched_at: null }),
    'GET /api/v1/discover/browse/personal?hide_owned=true': { rows: [] },
  }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

function tabs(): { text: string; href: string | null; on: boolean }[] {
  return [...document.querySelectorAll('.tabs a')].map((item) => ({ text: item.textContent?.trim() ?? '', href: item.getAttribute('href'), on: item.classList.contains('on') }))
}

describe('the head', () => {
  it('holds the mark, the three areas in their order, the button of light and dark and the account', async () => {
    await page.show(app(), '/')
    expect(document.querySelector('header.top')).toBeTruthy()
    expect(document.querySelector('.brand .mark')).toBeTruthy()
    expect(document.querySelector('.wordmark')!.textContent).toBe('NEXBOOKS')
    expect(tabs()).toEqual([
      { text: 'Bibliothek', href: '/', on: true },
      { text: 'Entdecken', href: '/discover', on: false },
      { text: 'Lesestapel', href: '/stack', on: false },
    ])
    expect(document.querySelector('.top button.icon-btn:not(.round)')).toBeTruthy()
    expect(document.querySelector('.top button[aria-haspopup]')).toBeTruthy()
    // The search field stands between the areas and the buttons, with the key that goes to it.
    const field = document.querySelector('.top .search input') as HTMLInputElement
    expect(field.getAttribute('placeholder')).toBe('Titel, Autorin, Reihe …')
    expect(field.getAttribute('aria-label')).toBe('Suchen')
    expect(document.querySelector('.top .search kbd')!.textContent).toBe('/')
    expect(document.querySelector('.top')!.children[2]).toBe(document.querySelector('.top .search'))
  })

  it('shows the cog of the settings to the operator only, before the button of light and dark', async () => {
    await page.show(app(), '/')
    const gear = document.querySelector<HTMLAnchorElement>('.top #gear')!
    expect(gear.getAttribute('href')).toBe('/settings?tab=procure')
    expect(gear.getAttribute('aria-label')).toBe('Einstellungen')
    expect(gear.classList.contains('icon-btn') && !gear.classList.contains('on')).toBe(true)
    expect(gear.nextElementSibling).toBe(document.querySelector('.top button.icon-btn:not(.round)'))
    page.close()
    auth.me = { ...me, role: 'member' } as Me
    await page.show(app(), '/')
    expect(document.querySelector('.top #gear')).toBeNull()
    expect(document.querySelector('.top button.icon-btn:not(.round)')).toBeTruthy()
  })

  it('marks the area that is open, and only that one', async () => {
    await page.show(app(), '/discover')
    expect(tabs().map((tab) => tab.on)).toEqual([false, true, false])
    page.close()
    await page.show(app(), '/stack')
    expect(tabs().map((tab) => tab.on)).toEqual([false, false, true])
  })

  it('speaks English too', async () => {
    await changeLanguage('en', false)
    await page.show(app(), '/')
    expect(tabs().map((tab) => tab.text)).toEqual(['Library', 'Discover', 'Reading stack'])
  })
})

describe('the button of light and dark', () => {
  it('switches the page, keeps the choice in this browser and in the account, and says which theme is on', async () => {
    await page.show(app(), '/')
    const button = document.querySelector<HTMLElement>('.top button.icon-btn:not(.round)')!
    expect(button.getAttribute('aria-label')).toBe('Hell oder dunkel')
    expect(button.querySelector('svg')!.getAttribute('class')).toContain('lucide-sun')
    await page.click(button)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('nexbooks.theme')).toBe('dark')
    expect(server.called('PUT', '/api/v1/me/preferences')!.body).toEqual({ theme: 'dark' })
    expect(button.querySelector('svg')!.getAttribute('class')).toContain('lucide-moon')
    await page.click(button)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(server.calls.filter((call) => call.url === '/api/v1/me/preferences').at(-1)!.body).toEqual({ theme: 'light' })
  })
})

describe('the three areas', () => {
  it('say what is not there yet in its words, and the operator is shown where to make a library', async () => {
    await page.show(app(), '/')
    expect(document.querySelector('h1')!.textContent).toBe('Bibliothek')
    expect(document.querySelector('.lead p')!.textContent).toBe('Deine E-Books und Hörbücher, ein Regal je Genre.')
    const empty = document.querySelector('[data-testid="library-empty"]')!
    expect(empty.textContent).toBe('Noch keine Bibliothek. Lege unter Einstellungen einen Ordner mit Büchern an.')
    expect(empty.querySelector('a')!.getAttribute('href')).toBe('/settings?tab=libraries')
    expect(empty.className).toBe('empty')
  })

  it('tell a member that the operator shares a library, with no link to the settings', async () => {
    auth.me = { ...me, role: 'member' } as Me
    await page.show(app(), '/')
    const empty = document.querySelector('[data-testid="library-empty"]')!
    expect(empty.textContent).toBe('Noch keine Bücher für dich. Der Betreiber gibt dir eine Bibliothek frei.')
    expect(empty.querySelector('a')).toBeNull()
  })

  it('open on Discover and on the reading stack with their own title, line and sentence', async () => {
    await page.show(app(), '/discover')
    expect(document.querySelector('h1')!.textContent).toBe('Entdecken')
    expect(document.querySelector('.lead p')!.textContent).toContain('Was du schon hast, blendet nexbooks aus.')
    expect(document.querySelector('[data-testid="why-main-off"]')!.textContent).toContain('nexbooks fragt gerade keine Quellen im Internet')
    page.close()
    await page.show(app(), '/stack')
    expect(document.querySelector('h1')!.textContent).toBe('Lesestapel')
    expect(document.querySelector('.lead p')!.textContent).toContain('Jedes Buch so dick wie im Regal.')
    expect(document.querySelector('[data-testid="stack-empty"]')!.textContent).toContain('sobald du Bücher gelesen oder gehört hast')
  })

  it('come in English with the same sentences', async () => {
    await changeLanguage('en', false)
    await page.show(app(), '/')
    expect(document.querySelector('[data-testid="library-empty"]')!.textContent).toBe('No library yet. Add a folder of books under Settings.')
    expect(document.querySelector('.lead p')!.textContent).toBe('Your e-books and audiobooks, one shelf per genre.')
  })

  it('never use a dash as a pause', async () => {
    for (const at of ['/', '/discover', '/stack']) {
      await page.show(app(), at)
      expect(document.querySelector('main')!.textContent).not.toMatch(/[–—]/)
      if (at !== '/stack') page.close()
    }
  })
})
