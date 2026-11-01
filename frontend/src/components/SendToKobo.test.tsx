/**
 * "To the Kobo" on the e-book, against a stand-in of the server: shown only for an EPUB without copy protection and only
 * while the operator allows e-readers; puts the book on the list and says the Kobo fetches it at its next sync; says where
 * to set a Kobo up while none is connected; takes the book off only after asking (and sends nothing before the answer);
 * and says what the server refused.
 */
import { Route, Routes } from 'react-router-dom'

import type { FileOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { forgetEreaders } from '../state/ereaders'
import { Page, Server } from '../test/harness'
import { SendToKobo } from './SendToKobo'

const server = new Server()
const page = new Page()

const epub: FileOut = { id: 11, format: 'epub', path: 'x.epub', size: 1_200_000, part: 1, duration: 0, sha256: 'a'.repeat(64), protected: false }
const azw3: FileOut = { ...epub, id: 12, format: 'azw3' }
const kindle = { address: '', mail_ready: false, sender: '', max_megabytes: 50 }
const kobo = { id: 3, kind: 'kobo', name: 'Kobo', prefix: 'nbe_Ab3d', created_at: '2026-10-01T10:00:00Z', last_used_at: null, blocked: false }

function serve(state: Record<string, unknown> = {}) {
  forgetEreaders()
  server.answers = {
    'GET /api/v1/ereaders': { allowed: true, links: [kobo], kindle, kobo: [], ...state },
    'PUT /api/v1/editions/5/kobo': { listed: true },
    'DELETE /api/v1/editions/5/kobo': { listed: false },
  }
  server.fails = {}
  server.install()
}

async function show(files: FileOut[]) {
  await page.show(
    <Routes>
      <Route path="/" element={<SendToKobo editionId={5} title="Der Leuchtturm" files={files} />} />
      <Route path="*" element={<div data-testid="elsewhere" />} />
    </Routes>,
  )
}

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  forgetEreaders()
})

describe('where the button is', () => {
  it('is on an EPUB', async () => {
    serve()
    await show([azw3, epub])
    expect(page.button('Auf den Kobo')).toBeTruthy()
  })

  it.each([
    ['a book with no EPUB', [azw3]],
    ['an EPUB with copy protection', [{ ...epub, protected: true }]],
    ['a book with no file', []],
  ])('is not on %s', async (_name, files) => {
    serve()
    await show(files as FileOut[])
    expect(page.button('Auf den Kobo')).toBeUndefined()
  })

  it('is not there while the operator has switched e-readers off', async () => {
    serve({ allowed: false })
    await show([epub])
    expect(page.button('Auf den Kobo')).toBeUndefined()
  })

  it('is not there while the server says nothing the page can use', async () => {
    forgetEreaders()
    server.answers = {}
    server.install()
    await show([epub])
    expect(page.button('Auf den Kobo')).toBeUndefined()
  })

  it('is in English too', async () => {
    await changeLanguage('en', false)
    serve()
    await show([epub])
    expect(page.button('To the Kobo')).toBeTruthy()
  })

  it('is already the button to take the book off when the book is on the list', async () => {
    serve({ kobo: [5] })
    await show([epub])
    expect(page.button('Auf den Kobo')).toBeUndefined()
    expect(page.button('Vom Kobo nehmen')).toBeTruthy()
  })
})

describe('putting a book on the list', () => {
  it('asks the server and says the Kobo fetches the book at its next sync', async () => {
    serve()
    await show([epub])
    await page.click(page.button('Auf den Kobo'))
    expect(server.changes()).toEqual(['PUT /api/v1/editions/5/kobo'])
    expect(document.querySelector('[data-testid="kobo-said"]')!.textContent).toContain('Der Kobo holt das Buch beim nächsten Abgleich.')
    expect(document.querySelector('[data-testid="kobo-setup"]')).toBeNull()
    expect(page.text).not.toMatch(/[–—]/)
    // The button now takes it off.
    expect(page.button('Vom Kobo nehmen')).toBeTruthy()
  })

  it('points to the setup when no Kobo is connected, and still puts the book on the list', async () => {
    serve({ links: [{ ...kobo, kind: 'opds' }, { ...kobo, id: 4, blocked: true }] })
    await show([epub])
    await page.click(page.button('Auf den Kobo'))
    expect(server.changes()).toEqual(['PUT /api/v1/editions/5/kobo'])
    expect(document.querySelector('[data-testid="kobo-said"]')!.textContent).toContain('Du hast noch keinen Kobo verbunden.')
    expect(document.querySelector('[data-testid="kobo-setup"]')!.getAttribute('href')).toBe('/account?tab=devices')
  })

  it('says what the server refused', async () => {
    serve()
    server.fails['PUT /api/v1/editions/5/kobo'] = { status: 409, code: 'kobo_list_full' }
    await show([epub])
    await page.click(page.button('Auf den Kobo'))
    expect(document.querySelector('[data-testid="kobo-said"]')!.textContent).toContain('Die Liste für den Kobo ist voll.')
    expect(page.button('Auf den Kobo')).toBeTruthy()
  })

  it('says when the brake holds', async () => {
    serve()
    server.fails['PUT /api/v1/editions/5/kobo'] = { status: 429, code: 'kobo_too_often' }
    await show([epub])
    await page.click(page.button('Auf den Kobo'))
    expect(document.querySelector('[data-testid="kobo-said"]')!.textContent).toContain('Zu viele Anfragen.')
  })
})

describe('taking a book off', () => {
  it('asks first, says it can be put back, and sends nothing before the answer', async () => {
    serve({ kobo: [5] })
    await show([epub])
    await page.click(page.button('Vom Kobo nehmen'))
    expect(server.changes()).toEqual([])
    expect(document.body.textContent).toContain('wird beim nächsten Abgleich vom Kobo gelöscht')
    expect(document.body.textContent).toContain('jederzeit wieder auf den Kobo legen')
    await page.inDialog('Vom Kobo nehmen')
    expect(server.changes()).toEqual(['DELETE /api/v1/editions/5/kobo'])
    expect(document.querySelector('[data-testid="kobo-said"]')!.textContent).toContain('Der Kobo entfernt das Buch beim nächsten Abgleich.')
    expect(page.button('Auf den Kobo')).toBeTruthy()
  })

  it('sends nothing when the question is answered with no', async () => {
    serve({ kobo: [5] })
    await show([epub])
    await page.click(page.button('Vom Kobo nehmen'))
    await page.click(page.button('Abbrechen'))
    expect(server.changes()).toEqual([])
    expect(page.button('Vom Kobo nehmen')).toBeTruthy()
  })
})
