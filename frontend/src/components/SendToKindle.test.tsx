/**
 * "Send to Kindle" on the e-book, against a stand-in of the server: shown only for an EPUB without copy protection and
 * only while the operator allows e-readers; by mail when the server can mail and the Kindle has an address; a note where
 * to set the address when it has none; and by hand (the file downloaded and Amazon's page opened in a new tab, in the
 * click itself) when the server cannot mail.
 */
import { Route, Routes } from 'react-router-dom'

import type { FileOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { forgetEreaders } from '../state/ereaders'
import { Page, Server } from '../test/harness'
import { AMAZON_SEND } from '../lib/kindle'
import { SendToKindle } from './SendToKindle'

const server = new Server()
const page = new Page()

const epub: FileOut = { id: 11, format: 'epub', path: 'x.epub', size: 1_200_000, part: 1, duration: 0, sha256: 'a'.repeat(64), protected: false }
const azw3: FileOut = { ...epub, id: 12, format: 'azw3' }
const mailing = { address: 'jule_k9@example.com', mail_ready: true, sender: 'books@example.com', max_megabytes: 50 }
const web = { address: '', mail_ready: false, sender: '', max_megabytes: 50 }

function serve(kindle = mailing, allowed = true) {
  forgetEreaders()
  server.answers = { 'GET /api/v1/ereaders': { allowed, links: [], kindle }, 'POST /api/v1/files/11/kindle': { queued: true } }
  server.fails = {}
  server.install()
}

async function show(files: FileOut[]) {
  await page.show(
    <Routes>
      <Route path="/" element={<SendToKindle files={files} />} />
      <Route path="*" element={<div data-testid="elsewhere" />} />
    </Routes>,
  )
}

let opened: unknown[][] = []
let downloaded: string[] = []

beforeEach(async () => {
  await changeLanguage('de', false)
  opened = []
  downloaded = []
  vi.stubGlobal('open', (...args: unknown[]) => {
    opened.push(args)
    return null
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloaded.push(this.getAttribute('href') ?? '')
  })
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  forgetEreaders()
})

describe('where the button is', () => {
  it('is on an EPUB', async () => {
    serve()
    await show([azw3, epub])
    expect(page.button('An Kindle senden')).toBeTruthy()
  })

  it.each([
    ['a book with no EPUB', [azw3]],
    ['an EPUB with copy protection', [{ ...epub, protected: true }]],
    ['a book with no file', []],
  ])('is not on %s', async (_name, files) => {
    serve()
    await show(files as FileOut[])
    expect(page.button('An Kindle senden')).toBeUndefined()
  })

  it('is not there while the operator has switched e-readers off', async () => {
    serve(mailing, false)
    await show([epub])
    expect(page.button('An Kindle senden')).toBeUndefined()
  })

  it('is not there while the server says nothing the page can use', async () => {
    forgetEreaders()
    server.answers = {}
    server.install()
    await show([epub])
    expect(page.button('An Kindle senden')).toBeUndefined()
  })

  it('is in English too', async () => {
    await changeLanguage('en', false)
    serve()
    await show([epub])
    expect(page.button('Send to Kindle')).toBeTruthy()
  })
})

describe('by mail', () => {
  it('asks the server to mail the EPUB and says it is on its way', async () => {
    serve()
    await show([azw3, epub])
    await page.click(page.button('An Kindle senden'))
    expect(server.changes()).toEqual(['POST /api/v1/files/11/kindle'])
    expect(document.querySelector('[data-testid="kindle-said"]')!.textContent).toContain('Unterwegs zu deinem Kindle')
    expect(opened).toEqual([])
    expect(downloaded).toEqual([])
  })

  it('says what the server refused', async () => {
    serve()
    server.fails['POST /api/v1/files/11/kindle'] = { status: 413, code: 'file_too_large', values: { max_mb: 50 } }
    await show([epub])
    await page.click(page.button('An Kindle senden'))
    expect(document.querySelector('[data-testid="kindle-said"]')!.textContent).toContain('größer als 50 MB')
  })

  it('says when the brake holds', async () => {
    serve()
    server.fails['POST /api/v1/files/11/kindle'] = { status: 429, code: 'kindle_too_often' }
    await show([epub])
    await page.click(page.button('An Kindle senden'))
    expect(document.querySelector('[data-testid="kindle-said"]')!.textContent).toContain('Zu viele Bücher auf einmal')
  })

  it('sends nothing and points to the setup when the Kindle has no address', async () => {
    serve({ ...mailing, address: '' })
    await show([epub])
    await page.click(page.button('An Kindle senden'))
    expect(server.changes()).toEqual([])
    expect(opened).toEqual([])
    expect(document.querySelector('[data-testid="kindle-said"]')!.textContent).toContain('Trag zuerst die Adresse deines Kindle ein.')
    expect(document.querySelector('[data-testid="kindle-setup"]')!.getAttribute('href')).toBe('/account?tab=devices')
  })
})

describe('by hand', () => {
  it('opens the page of Amazon in a new tab and downloads the file, and sends nothing to the server', async () => {
    serve(web)
    await show([epub])
    await page.click(page.button('An Kindle senden'))
    expect(opened).toEqual([[AMAZON_SEND, '_blank', 'noopener,noreferrer']])
    expect(downloaded).toEqual(['/api/v1/files/11/download'])
    expect(server.changes()).toEqual([])
    expect(document.querySelector('[data-testid="kindle-said"]')!.textContent).toContain('Ziehe die Datei dort hinein.')
  })

  it('takes the EPUB, not another format', async () => {
    serve(web)
    await show([azw3, epub])
    await page.click(page.button('An Kindle senden'))
    expect(downloaded).toEqual(['/api/v1/files/11/download'])
  })
})
