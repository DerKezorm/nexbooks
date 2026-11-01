/** Every call names its browser tab: the server refuses a change without it (the wall against other sites). */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, tabId } from './client'

function answer(status = 200, body: unknown = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function seen(mock: ReturnType<typeof vi.fn>): Record<string, string> {
  return mock.mock.calls[mock.mock.calls.length - 1][1].headers as Record<string, string>
}

afterEach(() => vi.unstubAllGlobals())

describe('the header of the tab', () => {
  it('goes with every kind of call, changes and reads alike', async () => {
    const fetchMock = vi.fn(async () => answer())
    vi.stubGlobal('fetch', fetchMock)
    for (const method of ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const) {
      await api('/api/v1/probe', { method })
      expect(seen(fetchMock)['X-Nexbooks-Client'], method).toBe(tabId())
    }
    await api('/api/v1/probe', { method: 'POST', raw: new Blob(['x']) })
    expect(seen(fetchMock)['X-Nexbooks-Client']).toBe(tabId())
  })

  it('is one id per tab, in the shape the server accepts, and a caller cannot replace it', async () => {
    const fetchMock = vi.fn(async () => answer())
    vi.stubGlobal('fetch', fetchMock)
    expect(tabId()).toMatch(/^tab-[0-9a-f]{24}$/)
    expect(tabId()).toBe(tabId())
    await api('/api/v1/probe', { method: 'POST', headers: { 'X-Nexbooks-Client': 'tab-other' } })
    expect(seen(fetchMock)['X-Nexbooks-Client']).toBe(tabId())
  })
})

describe('the answers', () => {
  it('turn the code of the server into an error and a lost session into the event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => answer(401, { detail: { code: 'sign_in_required', message: 'x' } })))
    const gone = vi.fn()
    window.addEventListener('nexbooks:signed-out', gone)
    await expect(api('/api/v1/probe')).rejects.toMatchObject({ status: 401, code: 'sign_in_required' })
    expect(gone).toHaveBeenCalledTimes(1)
    window.removeEventListener('nexbooks:signed-out', gone)
    vi.stubGlobal('fetch', vi.fn(async () => answer(403, { detail: { code: 'browser_only', message: 'x' } })))
    const error: unknown = await api('/api/v1/probe').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('browser_only')
  })
})
