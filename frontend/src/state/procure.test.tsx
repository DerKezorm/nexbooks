/**
 * The state of fetching for the pages: a person who may not ask for books costs the server one question and no
 * more; one who may is told every fifteen seconds, and the lists are replaced only when they say something else; what is
 * asked for is found by its work or its token; the shelf marks only a kind the book lacks.
 */
import { act } from 'react'

import type { Me } from '../api/client'
import { changeLanguage } from '../i18n'
import { Page, Server } from '../test/harness'
import { counts, running, wish } from '../test/procure'
import { work } from '../test/books'
import { POLL_MS, ProcureProvider, useIncoming, useProcure } from './procure'

const auth = { status: 'signedIn', me: { id: 1, name: 'jule', role: 'member', profile: {} } as unknown as Me | null, setMe: vi.fn(), signOut: async () => undefined, refresh: async () => undefined }
vi.mock('./auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

function Probe() {
  const procure = useProcure()
  const incoming = useIncoming()
  const w = work(5)
  const asked = procure.kindsOf({ workId: 5 })
  return (
    <div>
      <span data-testid="allowed">{String(procure.allowed)}</span>
      <span data-testid="wishes">{procure.wishes.length}</span>
      <span data-testid="running">{procure.running.length}</span>
      <span data-testid="asked">{asked.map((item) => `${item.kind}:${item.wish?.state ?? '-'}:${item.download?.phase ?? '-'}`).join(',')}</span>
      <span data-testid="token">{procure.kindsOf({ token: 'tok1' }).length}</span>
      <span data-testid="incoming">{JSON.stringify(incoming(w))}</span>
    </div>
  )
}

function serve(answers: Record<string, unknown> = {}) {
  server.answers = { 'GET /api/v1/procure/counts': counts(), 'GET /api/v1/procure/wishes': { wishes: [] }, 'GET /api/v1/procure/running': { downloads: [] }, ...answers }
  server.fails = {}
  server.install()
}

const text = (id: string) => document.querySelector(`[data-testid="${id}"]`)!.textContent

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the provider', () => {
  it('asks the numbers only, for a person who may not ask for books', async () => {
    serve({ 'GET /api/v1/procure/counts': counts({ allowed: false }) })
    await page.show(
      <ProcureProvider>
        <Probe />
      </ProcureProvider>,
    )
    expect(text('allowed')).toBe('false')
    expect(server.calls.map((call) => call.url)).toEqual(['/api/v1/procure/counts'])
  })

  it('reads the lists, finds a book by its work and its token, and marks only a kind the book lacks', async () => {
    serve({
      'GET /api/v1/procure/wishes': { wishes: [wish(1, { kind: 'audio', state: 'searching', book: { work_id: 5, book: null, title: 'Buch 5', authors: [], series: '', series_pos: null, genre: '', color: '#7d2f2b', cover_url: null } }), wish(2, { book: { work_id: null, book: 'tok1', title: 'Ein Buch', authors: [], series: '', series_pos: null, genre: '', color: '#7d2f2b', cover_url: null } })] },
      'GET /api/v1/procure/running': { downloads: [running(3, { kind: 'audio', book: { work_id: 5, book: null, title: 'Buch 5', authors: [], series: '', series_pos: null, genre: '', color: '#7d2f2b', cover_url: null } })] },
    })
    await page.show(
      <ProcureProvider>
        <Probe />
      </ProcureProvider>,
    )
    expect(text('allowed')).toBe('true')
    expect(text('wishes')).toBe('2')
    expect(text('asked')).toBe('audio:searching:downloading')
    expect(text('token')).toBe('1')
    // The book has an e-book (work(5) has one), so an audiobook that loads is a kind it lacks: the band is "run".
    expect(JSON.parse(text('incoming')!)).toEqual({ state: 'run', kind: 'audio' })
  })

  it('marks nothing for an upgrade of a kind the book has, or for a book nobody asked anything of', async () => {
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1, { kind: 'ebook', state: 'upgrade', book: { work_id: 5, book: null, title: 'Buch 5', authors: [], series: '', series_pos: null, genre: '', color: '#7d2f2b', cover_url: null } })] } })
    await page.show(
      <ProcureProvider>
        <Probe />
      </ProcureProvider>,
    )
    expect(text('asked')).toBe('ebook:upgrade:-')
    expect(text('incoming')).toBe('null')
  })

  it('asks again every fifteen seconds and keeps the lists when they say the same', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(
      <ProcureProvider>
        <Probe />
      </ProcureProvider>,
    )
    const before = server.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    })
    expect(server.calls.length).toBe(before + 3)
    expect(text('wishes')).toBe('1')
    server.answers['GET /api/v1/procure/wishes'] = { wishes: [wish(1), wish(2)] }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    })
    expect(text('wishes')).toBe('2')
  })

  it('keeps what it knew when a round fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serve({ 'GET /api/v1/procure/wishes': { wishes: [wish(1)] } })
    await page.show(
      <ProcureProvider>
        <Probe />
      </ProcureProvider>,
    )
    server.fails['GET /api/v1/procure/counts'] = { status: 500, code: 'internal_error' }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS + 100)
    })
    expect(text('allowed')).toBe('true')
    expect(text('wishes')).toBe('1')
  })
})
