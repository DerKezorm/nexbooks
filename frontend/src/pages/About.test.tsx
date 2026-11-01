/**
 * The About page against a stand-in of the server: version, licence and links, whether a newer version is out (and the
 * daily question, which only the operator switches or asks now), the written text of a version and what nexbooks is
 * built with, in German and in English. The texts of "what is new" are invented here: the real ones are written with the
 * first release.
 */
import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { entriesFor } from '../lib/whatsNew'
import { Page, Server } from '../test/harness'
import { AboutPage } from './AboutPage'

const me = {
  id: 1, name: 'jule', display_name: 'Jule', role: 'operator', sign_in: 'password', email: '', language: 'de', version: '1.0.0', whats_new_seen: '1.0.0',
  session_stage: 'full', profile: { theme: 'system' },
} as unknown as Me
const auth = { me: me as Me | null }
vi.mock('../state/auth', () => ({ useAuth: () => auth }))

vi.mock('../i18n/whatsnew/de.json', () => ({
  default: {
    '1.0.0': {
      lead: 'Das ist nexbooks 1.0.', smallTitle: 'Außerdem', small: ['Kleinigkeit'],
      sections: [{ title: 'Das Regal', where: 'Bibliothek', body: 'Alle Bücher stehen auf einem Regal.' }],
    },
  },
}))
vi.mock('../i18n/whatsnew/en.json', () => ({
  default: {
    '1.0.0': {
      lead: 'This is nexbooks 1.0.', smallTitle: 'Also', small: ['A small thing'],
      sections: [{ title: 'The shelf', where: 'Library', body: 'All books stand on one shelf.' }],
    },
  },
}))

const server = new Server()
const page = new Page()

const about = {
  version: '1.0.0', license: 'AGPL-3.0', repo_url: 'https://github.com/example/nexbooks', releases_url: 'https://github.com/example/nexbooks/releases',
  project_url: 'https://nexbooks.example.com',
}
const current = { update_check: true, checked: true, latest: 'v1.0.0', newer: false, checked_at: '2026-10-10T08:15:00Z', release_url: null }

beforeEach(async () => {
  // jsdom has no modal dialogs: the window opens as a plain element.
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '')
  }
  await changeLanguage('de', false)
  auth.me = me
  server.answers = { 'GET /api/v1/about': about, 'GET /api/v1/about/updates': current }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the sources of data', () => {
  it('are named with what they give, and Audible says that it is off until the operator turns it on', async () => {
    await page.show(<AboutPage />)
    const list = document.querySelector('[data-testid="about-data-sources"]')!
    expect([...list.querySelectorAll('li')].map((item) => item.getAttribute('data-source'))).toEqual(['openlibrary', 'wikidata', 'dnb', 'bnf', 'libris', 'kb', 'nb', 'apple', 'hardcover', 'audible'])
    expect(list.querySelector('[data-source="openlibrary"]')!.textContent).toBe('Open Library: ein Projekt des Internet Archive, offene Daten zu Büchern.')
    expect(list.querySelector('[data-source="openlibrary"] a')!.getAttribute('href')).toBe('https://openlibrary.org')
    expect(list.querySelector('[data-source="hardcover"]')!.textContent).toContain('auch für die gewerbliche Nutzung')
    expect(list.querySelector('[data-source="audible"]')!.textContent).toContain('nur wenn der Betreiber es einschaltet')
    // The national libraries serve the catalogue of an author only.
    expect(list.querySelector('[data-source="libris"]')!.textContent).toBe('LIBRIS (Schweden): die Titel ihrer Sprache im Werk einer Autorin oder eines Autors')
  })
})

describe('the facts', () => {
  it('show the version, the licence, the source, the releases and where to report a problem', async () => {
    await page.show(<AboutPage />)
    const facts = document.querySelector('[data-testid="about-facts"]')!.textContent!
    expect(document.querySelector('[data-testid="about-version"]')?.textContent).toBe('1.0.0')
    expect(facts).toContain('AGPL-3.0')
    expect(facts).toContain('github.com/example/nexbooks')
    expect(facts).toContain('nexbooks.example.com')
    const links = [...document.querySelectorAll('dl a')].map((a) => a.getAttribute('href'))
    expect(links).toContain('https://www.gnu.org/licenses/agpl-3.0.html')
    expect(links).toContain('https://github.com/example/nexbooks/issues/new')
    for (const a of document.querySelectorAll('dl a')) expect(a.getAttribute('rel')).toContain('noopener')
  })

  it('say that this is the newest version', async () => {
    await page.show(<AboutPage />)
    expect(document.querySelector('[data-testid="about-update-state"]')?.textContent).toBe('Das ist die neueste Version.')
  })

  it('say calmly that nothing can be checked while the repository does not exist (GitHub answers "not found")', async () => {
    server.answers['GET /api/v1/about/updates'] = { ...current, latest: null, newer: false, release_url: null }
    await page.show(<AboutPage />)
    const state = document.querySelector('[data-testid="about-update-state"]')!
    expect(state.textContent).toBe('Zurzeit nicht prüfbar: Es gibt noch keine Veröffentlichung, oder GitHub ist nicht erreichbar.')
    expect(state.querySelector('a')).toBeNull()
    expect(page.text).not.toMatch(/Fehler:|fehlgeschlagen|nicht gefunden/i)
  })

  it('say that a newer version is out, with the way to it', async () => {
    server.answers['GET /api/v1/about/updates'] = { ...current, latest: 'v0.1.1', newer: true, release_url: 'https://github.com/example/nexbooks/releases/tag/v0.1.1' }
    await page.show(<AboutPage />)
    expect(document.querySelector('[data-testid="about-update-state"]')?.textContent).toContain('0.1.1')
    expect(page.text).toContain('Version 0.1.1 ist erschienen')
    expect(document.querySelector('[data-testid="about-update-state"] a')?.getAttribute('href')).toBe('https://github.com/example/nexbooks/releases/tag/v0.1.1')
  })
})

describe('the daily question', () => {
  it('is switched and asked now by the operator, and says what goes out', async () => {
    server.answers['PUT /api/v1/about/updates'] = { ...current, update_check: false }
    server.answers['POST /api/v1/about/updates/check'] = current
    await page.show(<AboutPage />)
    expect(page.text).toContain('Hinaus geht nur die Frage')
    await page.click(page.button('Jetzt nachsehen'))
    expect(server.changes()).toEqual(['POST /api/v1/about/updates/check'])
    await page.click(document.querySelector<HTMLElement>('[role="switch"]'))
    expect(server.called('PUT', '/api/v1/about/updates')?.body).toEqual({ update_check: false })
  })

  it('is only shown, not changed, to a member', async () => {
    auth.me = { ...me, role: 'member' } as Me
    await page.show(<AboutPage />)
    expect(document.querySelector('[role="switch"]')).toBeNull()
    expect(page.button('Jetzt nachsehen')).toBeUndefined()
    expect(document.querySelector('[data-testid="about-update-state"]')?.textContent).toBe('Das ist die neueste Version.')
  })
})

describe('what is new', () => {
  it('opens the text of the running version and reads it again at any time', async () => {
    // The texts come by a dynamic import: loaded before the page, the button is there at its first idle moment, also
    // on a busy machine.
    await entriesFor('de')
    await page.show(<AboutPage />)
    await page.click(page.button('Neu in nexbooks 1.0.0'))
    const window = document.querySelector('[data-testid="whats-new-window"]')!
    expect(window.textContent).toContain('Das ist nexbooks 1.0')
    expect(window.textContent).toContain('Alle Bücher stehen auf einem Regal.')
    expect(window.textContent).not.toMatch(/[–—]/)
  })

  it('has the same text in English', async () => {
    await changeLanguage('en', false)
    await entriesFor('en')
    await page.show(<AboutPage />)
    await page.click(page.button('New in nexbooks 1.0.0'))
    const window = document.querySelector('[data-testid="whats-new-window"]')!
    expect(window.textContent).toContain('This is nexbooks 1.0')
    expect(window.textContent).toContain('All books stand on one shelf.')
  })

  it('shows no row for a version without a text', async () => {
    auth.me = { ...me, version: '0.9.9' } as Me
    await entriesFor('de')
    await page.show(<AboutPage />)
    expect(page.button('Neu in nexbooks 0.9.9')).toBeUndefined()
  })
})

describe('what it is built with', () => {
  it('names the parts that stay and the fonts with their licences, and none of what was left out', async () => {
    await page.show(<AboutPage />)
    const built = [...document.querySelectorAll('li')].map((item) => (item.textContent ?? '').replace(/\s+/g, ' ').replace('(', ' ('))
    for (const part of ['FastAPI (MIT)', 'SQLAlchemy (MIT)', 'Pillow (MIT-CMU)', 'Fraunces (OFL-1.1)', 'Inter (OFL-1.1)', 'Oswald (OFL-1.1)']) {
      expect(built, part).toContain(part)
    }
    expect(page.text).not.toMatch(/OCRmyPDF|Tesseract|pikepdf|img2pdf|PDF\.js|OpenCV|jscanify|Figtree/)
  })
})
