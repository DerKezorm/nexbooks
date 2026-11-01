/**
 * Asking the catalogues (`askCatalog`): an answer that is still pending is asked for again a limited number of times, a search
 * the page gives up on while the sources are still asked is stopped on the server, an answer that comes after the page gave
 * up is never handed on, and the page of a hit is made only when it has none (`tokenOfHit`).
 */
import { catalogHit } from '../test/catalog'
import { Server } from '../test/harness'
import { askCatalog, POLL_TIMES, tokenOfHit } from './catalogSearch'

const server = new Server()
const pending = { available: true, failed: false, pending: true, ticket: 'ticket-0001', items: [catalogHit('tok-1')], people: [] }
const done = { ...pending, pending: false }

afterEach(() => vi.unstubAllGlobals())

function count(method: string, url: string): number {
  return server.calls.filter((call) => call.method === method && call.url === url).length
}

describe('asking the catalogues', () => {
  it('asks again by the ticket while the answer is pending, and no more than a few times', async () => {
    server.answers = { 'POST /api/v1/search/catalog': pending, 'GET /api/v1/search/catalog/ticket-0001': pending, 'DELETE /api/v1/search/catalog/ticket-0001': 204 }
    server.install()
    const answers: unknown[] = []
    await askCatalog('salztor', new AbortController().signal, (answer) => answers.push(answer))
    expect(POLL_TIMES).toBeLessThanOrEqual(15)
    expect(count('GET', '/api/v1/search/catalog/ticket-0001')).toBe(POLL_TIMES)
    expect(answers).toHaveLength(POLL_TIMES + 1)
    // Still pending when the page stops asking: the server is told to stop as well.
    expect(count('DELETE', '/api/v1/search/catalog/ticket-0001')).toBe(1)
  })

  it('stops asking once every source answered, and tells the server nothing then', async () => {
    server.answers = { 'POST /api/v1/search/catalog': pending, 'GET /api/v1/search/catalog/ticket-0001': done }
    server.install()
    await askCatalog('salztor', new AbortController().signal, () => undefined)
    expect(count('GET', '/api/v1/search/catalog/ticket-0001')).toBe(1)
    expect(count('DELETE', '/api/v1/search/catalog/ticket-0001')).toBe(0)
  })

  it('stops the search on the server when the page gives up while the sources are still asked, and hands nothing on after', async () => {
    let release: (value: Response) => void = () => undefined
    server.answers = { 'POST /api/v1/search/catalog': pending, 'DELETE /api/v1/search/catalog/ticket-0001': 204 }
    server.install()
    const fake = globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === '/api/v1/search/catalog/ticket-0001' && (init?.method ?? 'GET') === 'GET') {
          server.calls.push({ method: 'GET', url, body: undefined })
          return new Promise<Response>((resolve) => (release = resolve))
        }
        return fake(url, init)
      }),
    )
    const controller = new AbortController()
    const answers: unknown[] = []
    const asking = askCatalog('salztor', controller.signal, (answer) => answers.push(answer))
    await vi.waitFor(() => expect(count('GET', '/api/v1/search/catalog/ticket-0001')).toBe(1))
    controller.abort()
    release(new Response(JSON.stringify(done), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await asking.catch(() => undefined)
    expect(answers).toHaveLength(1)
    expect(count('DELETE', '/api/v1/search/catalog/ticket-0001')).toBe(1)
  })

  it('hands on no first answer that comes after the page gave up', async () => {
    let release: (value: Response) => void = () => undefined
    let asked = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        asked += 1
        return new Promise<Response>((resolve) => (release = resolve))
      }),
    )
    const controller = new AbortController()
    const answers: unknown[] = []
    const asking = askCatalog('salztor', controller.signal, (answer) => answers.push(answer))
    await vi.waitFor(() => expect(asked).toBe(1))
    controller.abort()
    release(new Response(JSON.stringify(done), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await asking.catch(() => undefined)
    expect(answers).toHaveLength(0)
  })

  it('tells the server nothing when the page leaves a search that is over', async () => {
    server.answers = { 'POST /api/v1/search/catalog': done }
    server.install()
    const controller = new AbortController()
    await askCatalog('salztor', controller.signal, () => undefined)
    controller.abort()
    expect(server.calls.filter((call) => call.method === 'DELETE')).toHaveLength(0)
  })
})

describe('the page of a hit', () => {
  it('is the one the hit has, or is made now by the search it belongs to', async () => {
    server.answers = { 'POST /api/v1/search/catalog/ticket-0001/books': { token: 'tok-made' } }
    server.install()
    expect(await tokenOfHit(catalogHit('tok-own'), 'ticket-0001')).toBe('tok-own')
    expect(server.calls).toHaveLength(0)
    expect(await tokenOfHit(catalogHit(null, { key: 'a'.repeat(20) }), 'ticket-0001')).toBe('tok-made')
    expect(server.called('POST', '/api/v1/search/catalog/ticket-0001/books')!.body).toEqual({ key: 'a'.repeat(20) })
    await expect(tokenOfHit(catalogHit(null), null)).rejects.toThrow()
  })
})
