/**
 * The account's looks win over what the browser remembered: after the page has asked the server, light or dark of
 * the account is painted, whatever an earlier visit (or another account on this browser) left behind. And the script
 * that paints a remembered choice before the first frame knows the three modes and nothing else.
 */
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import '../i18n'
import script from '../../public/theme.js?raw'
import { SIGNED_OUT_EVENT } from '../api/client'
import { AuthProvider, useAuth } from './auth'
import { idle } from '../test/wait'

let root: Root
let box: HTMLDivElement
let kept: Map<string, string>

function Probe() {
  const { status } = useAuth()
  return <p>{status}</p>
}

function serve(profile: Record<string, unknown>): void {
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/v1/setup') return json({ needs_setup: false, code_required: false, signed_in: true, version: '0.1.0', min_password: 12 })
      if (url === '/api/v1/auth/me') {
        return json({ id: 1, name: 'jule', display_name: 'Jule', role: 'user', language: 'de', version: '0.1.0', whats_new_seen: '0.1.0', profile: { timezone: 'Europe/Berlin', timezone_source: 'manual', ...profile } })
      }
      return json({})
    }),
  )
}

async function start(inside: ReactNode = <Probe />): Promise<void> {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  box = document.createElement('div')
  document.body.appendChild(box)
  root = createRoot(box)
  await act(async () => root.render(<AuthProvider>{inside}</AuthProvider>))
  await idle()
}

beforeEach(() => {
  kept = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
    key: (index: number) => Array.from(kept.keys())[index] ?? null,
    get length() {
      return kept.size
    },
  })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined }))
  document.documentElement.removeAttribute('data-theme')
})

afterEach(async () => {
  await act(async () => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

describe('the looks of the account', () => {
  it('are painted once the server has said them, over what an earlier visit left in the browser', async () => {
    kept.set('nexbooks.theme', 'light')
    serve({ theme: 'dark' })
    await start()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(kept.get('nexbooks.theme')).toBe('dark')
  })

  it('bring light back when the account has light and the browser remembered dark', async () => {
    kept.set('nexbooks.theme', 'dark')
    document.documentElement.setAttribute('data-theme', 'dark')
    serve({ theme: 'light' })
    await start()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(kept.get('nexbooks.theme')).toBe('light')
  })

  it('leave the choice to the system when the account says so', async () => {
    kept.set('nexbooks.theme', 'dark')
    document.documentElement.setAttribute('data-theme', 'dark')
    serve({ theme: 'system' })
    await start()
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
    expect(kept.get('nexbooks.theme')).toBe('system')
  })

  it('ignore a mode that is none of the three', async () => {
    serve({ theme: 'sepia' })
    await start()
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })
})

describe('the script before the first frame', () => {
  function run(stored: Record<string, string>, dark = false): void {
    document.documentElement.removeAttribute('data-theme')
    document.head.innerHTML = '<meta name="theme-color" content="#f3ede3">'
    vi.stubGlobal('localStorage', { getItem: (key: string) => stored[key] ?? null })
    vi.stubGlobal('matchMedia', () => ({ matches: dark }))
    new Function(script)()
  }

  it('paints the mode of the last visit', () => {
    run({ 'nexbooks.theme': 'dark' })
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.querySelector('meta[name="theme-color"]')!.getAttribute('content')).toBe('#12100e')
    // A light mode chosen wins over a dark system.
    run({ 'nexbooks.theme': 'light' }, true)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(document.querySelector('meta[name="theme-color"]')!.getAttribute('content')).toBe('#f3ede3')
  })

  it('paints nothing for the system, for nothing remembered and for a value that is no mode', () => {
    const cases: Record<string, string>[] = [{ 'nexbooks.theme': 'system' }, {}, { 'nexbooks.theme': '"><script>' }, { 'nexbooks.theme': 'neon' }]
    for (const stored of cases) {
      run(stored)
      expect(document.documentElement.hasAttribute('data-theme'), JSON.stringify(stored)).toBe(false)
    }
  })

  it('leaves the system to the stylesheet, and tells the browser its colour when the system is dark', () => {
    run({}, true)
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
    expect(document.querySelector('meta[name="theme-color"]')!.getAttribute('content')).toBe('#12100e')
  })
})

describe('the person who signs out', () => {
  it('is signed out for the pages when a request finds the session gone', async () => {
    serve({ theme: 'system' })
    await start()
    expect(box.textContent).toBe('signedIn')
    await act(async () => window.dispatchEvent(new Event('nexbooks:signed-out')))
    await idle()
    expect(box.textContent).toBe('signedOut')
  })

  it('is signed out for the pages after pressing Sign out', async () => {
    serve({ theme: 'system' })
    await start(<SignOut />)
    expect(box.querySelector('p')!.textContent).toBe('signedIn')
    await act(async () => box.querySelector('button')!.click())
    await idle()
    expect(box.querySelector('p')!.textContent).toBe('signedOut')
  })

  it('forgets where the person was in their books when they sign out, and keeps what belongs to the browser', async () => {
    kept.set('nexbooks.read.1.7', '{"locator":"epubcfi(/6/4)","fraction":0.7,"at":1}')
    kept.set('nexbooks.device', 'bw-abcdef123456')
    kept.set('nexbooks.theme', 'dark')
    serve({ theme: 'dark' })
    await start(<SignOut />)
    await act(async () => box.querySelector('button')!.click())
    await idle()
    expect([...kept.keys()].filter((key) => key.startsWith('nexbooks.read.'))).toEqual([])
    expect(kept.get('nexbooks.device')).toBe('bw-abcdef123456')
    expect(kept.get('nexbooks.theme')).toBe('dark')
  })

  it('forgets it as well when the session is gone without the person pressing anything', async () => {
    kept.set('nexbooks.read.1.7', '{"locator":"epubcfi(/6/4)","fraction":0.7,"at":1}')
    serve({ theme: 'dark' })
    await start(<SignOut />)
    await act(async () => void window.dispatchEvent(new Event(SIGNED_OUT_EVENT)))
    expect(box.querySelector('p')!.textContent).toBe('signedOut')
    expect([...kept.keys()].filter((key) => key.startsWith('nexbooks.read.'))).toEqual([])
  })
})

function SignOut() {
  const { signOut, status } = useAuth()
  return (
    <>
      <p>{status}</p>
      <button type="button" onClick={() => void signOut()}>
        out
      </button>
    </>
  )
}
