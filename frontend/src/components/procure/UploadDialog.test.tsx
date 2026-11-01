/**
 * "Hochladen": the files chosen are listed, what is no book or too large is named and nothing is sent; the
 * files go one by one, then the upload is filed and the dialog says what came of it; stopping while files are sent asks
 * first and drops what came.
 */
import { act } from 'react'

import { uploadsApi, type UploadOut } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server, plain } from '../../test/harness'
import { UploadDialog } from './UploadDialog'

const server = new Server()
const page = new Page()

function upload(over: Partial<UploadOut> = {}): UploadOut {
  return { id: 5, state: 'uploading', phase: 'uploading', library_id: 1, work_id: null, kind: 'ebook', problem: '', files: [], limits: { ebook_bytes: 1, file_bytes: 1, total_bytes: 1, files: 1 }, created_at: new Date().toISOString(), ...over }
}

function serve(answers: Record<string, unknown> = {}) {
  server.answers = {
    'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }, { id: 2, name: 'Hörbücher', kind: 'audio' }] },
    'POST /api/v1/uploads': upload(),
    'POST /api/v1/uploads/5/finish': upload({ state: 'completed', phase: 'waiting' }),
    'GET /api/v1/uploads/5': upload({ state: 'imported', phase: 'done', work_id: 42 }),
    'DELETE /api/v1/uploads/5': 204,
    ...answers,
  }
  server.fails = {}
  server.install()
}

async function choose(files: File[]) {
  const input = document.querySelector<HTMLInputElement>('[data-testid="upload-input"]')!
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
}

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.useRealTimers()
})
afterEach(() => {
  page.close()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('uploading books', () => {
  it('names what is no book and sends nothing then', async () => {
    serve()
    await page.show(<UploadDialog onClose={() => undefined} />)
    await choose([new File(['x'], 'setup.exe'), new File(['y'], 'Buch.epub')])
    const rows = [...document.querySelectorAll('[data-testid="upload-files"] li')].map((row) => plain(row.textContent))
    expect(rows[0]).toContain('kein Buchformat')
    expect((document.querySelector('[data-act="upload"]') as HTMLButtonElement).disabled).toBe(true)
    await page.click(page.button('setup.exe entfernen'))
    // Nothing else is wrong now; the person still says which library (there are two).
    expect((document.querySelector('[data-act="upload"]') as HTMLButtonElement).disabled).toBe(true)
    await page.click(document.querySelectorAll<HTMLInputElement>('input[name="upload-library"]')[0])
    expect((document.querySelector('[data-act="upload"]') as HTMLButtonElement).disabled).toBe(false)
    expect(server.changes()).toEqual([])
  })

  it('refuses e-books and recordings together', async () => {
    serve()
    await page.show(<UploadDialog onClose={() => undefined} />)
    await choose([new File(['x'], 'a.epub'), new File(['y'], 'b.mp3')])
    expect(page.text).toContain('E-Books und Hörbücher bitte getrennt hochladen.')
    expect((document.querySelector('[data-act="upload"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends the files one by one, files the upload and says it is on the shelf', async () => {
    serve()
    const send = vi.spyOn(uploadsApi, 'send').mockImplementation(async (_id, path, file, progress) => {
      progress(file.size)
      return { path, size: file.size }
    })
    await page.show(<UploadDialog onClose={() => undefined} />)
    await choose([new File(['abc'], 'Teil 1.mp3'), new File(['defg'], 'Teil 2.mp3')])
    // Two libraries take a recording: the person picks one.
    expect(document.querySelectorAll('.opt').length).toBe(2)
    await page.click(document.querySelectorAll<HTMLInputElement>('input[name="upload-library"]')[1])
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await page.click(document.querySelector('[data-act="upload"]'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(server.called('POST', '/api/v1/uploads')!.body).toEqual({ library_id: 2 })
    expect(send.mock.calls.map((call) => call[1])).toEqual(['Teil 1.mp3', 'Teil 2.mp3'])
    expect(server.called('POST', '/api/v1/uploads/5/finish')).toBeTruthy()
    expect(page.text).toContain('Das Buch liegt jetzt im Regal.')
    expect(document.querySelector('[data-act="open-book"]')!.getAttribute('href')).toBe('/work/42')
  })

  it('picks no library for the person when there are several, and the one there is when there is one', async () => {
    serve()
    await page.show(<UploadDialog onClose={() => undefined} />)
    await choose([new File(['x'], 'Buch.epub')])
    const go = () => document.querySelector<HTMLButtonElement>('[data-act="upload"]')!
    expect(document.querySelectorAll('input[name="upload-library"]').length).toBe(2)
    expect(document.querySelectorAll('input[name="upload-library"]:checked').length).toBe(0)
    expect(go().disabled).toBe(true)
    await page.click(document.querySelectorAll<HTMLInputElement>('input[name="upload-library"]')[1])
    expect(go().disabled).toBe(false)
    page.close()
    serve({ 'GET /api/v1/procure/targets': { targets: [{ id: 1, name: 'Bücher', kind: 'both' }] } })
    await page.show(<UploadDialog onClose={() => undefined} />)
    await choose([new File(['x'], 'Buch.epub')])
    expect(document.querySelectorAll('input[name="upload-library"]').length).toBe(0)
    expect(go().disabled).toBe(false)
  })

  it('asks before it stops while files are sent, and drops what came', async () => {
    serve()
    vi.spyOn(uploadsApi, 'send').mockImplementation(() => new Promise(() => undefined))
    const closed = vi.fn()
    await page.show(<UploadDialog onClose={closed} />)
    await choose([new File(['abc'], 'Buch.epub')])
    await page.click(document.querySelectorAll<HTMLInputElement>('input[name="upload-library"]')[0])
    await page.click(document.querySelector('[data-act="upload"]'))
    expect(document.querySelector('[data-testid="upload-progress"]')).not.toBeNull()
    await page.click(document.querySelector('[data-act="stop"]'))
    expect(page.dialog!.textContent).toContain('Was schon gesendet ist, wird gelöscht. Das lässt sich nicht zurückholen.')
    await page.inDialog('Abbrechen')
    expect(server.called('DELETE', '/api/v1/uploads/5')).toBeUndefined()
    await page.click(document.querySelector('[data-act="stop"]'))
    await page.inDialog('Hochladen abbrechen')
    expect(server.called('DELETE', '/api/v1/uploads/5')).toBeTruthy()
    expect(closed).toHaveBeenCalled()
  })

  it('offers only libraries that take the kind that is missing, for the form a book lacks', async () => {
    serve()
    await page.show(<UploadDialog workId={7} kind="ebook" title="Die Laternenstraße" onClose={() => undefined} />)
    expect(document.querySelectorAll('.opt').length).toBe(0)
    await choose([new File(['x'], 'b.mp3')])
    expect(page.text).toContain('Hier fehlt das E-Book.')
  })
})
