/**
 * What the tests of the pages share: a stand-in of the server (`Server`, answers by `METHOD /path`), a page shown in a
 * router, and the few things a test does to it (find a button, click, type).
 */
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import '../i18n'
import { idle } from './wait'

export type Call = { method: string; url: string; body: Record<string, unknown> | undefined }

/** A text without the isolating marks around a person's text (U+2068, U+2069). */
export function plain(text: string | null | undefined): string {
  return (text ?? '').split(String.fromCharCode(0x2068)).join('').split(String.fromCharCode(0x2069)).join('')
}

export class Server {
  calls: Call[] = []
  answers: Record<string, unknown> = {}
  fails: Record<string, { status: number; code: string; values?: Record<string, unknown> }> = {}

  install(): void {
    this.calls = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        const body = init?.body ? JSON.parse(String(init.body)) : undefined
        this.calls.push({ method, url, body })
        const failed = this.fails[`${method} ${url}`]
        if (failed) return new Response(JSON.stringify({ detail: { code: failed.code, ...failed.values } }), { status: failed.status, headers: { 'Content-Type': 'application/json' } })
        const found = this.answers[`${method} ${url}`]
        if (found === 204) return new Response(null, { status: 204 })
        return new Response(JSON.stringify(found ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }),
    )
  }

  /** The calls that changed something, as `METHOD url`. */
  changes(): string[] {
    return this.calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.url}`)
  }

  called(method: string, url: string): Call | undefined {
    return this.calls.find((c) => c.method === method && c.url === url)
  }
}

export class Page {
  root!: Root
  box!: HTMLDivElement

  /** Draws the page and waits for the fake API to be quiet (see `idle`). */
  async show(element: ReactNode, at: string | { pathname: string; state?: unknown } = '/'): Promise<void> {
    await this.start(element, at)
    await idle()
  }

  /**
   * Draws the page and returns at once, without waiting for any request. For a test that holds an answer back on
   * purpose and then waits for something with `eventually`: `show` must not be left running beside such a wait, because
   * both loop on `act`, and React's `act` does not allow two to be open at once ("overlapping act() calls"). Which one
   * ends first then depends on the speed of the fake API, and the wrong order breaks the rendering of every later test of
   * the file.
   */
  async start(element: ReactNode, at: string | { pathname: string; state?: unknown } = '/'): Promise<void> {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    this.box = document.createElement('div')
    document.body.appendChild(this.box)
    this.root = createRoot(this.box)
    await act(async () => this.root.render(<MemoryRouter initialEntries={[at]}>{element}</MemoryRouter>))
  }

  close(): void {
    act(() => this.root.unmount())
    this.box.remove()
  }

  /** The text of the page as a person reads it: without the invisible marks that keep a person's text apart from the
   * sentence around it (`lib/say.ts`, `iso`). */
  get text(): string {
    return plain(document.body.textContent ?? '')
  }

  button(text: string, within: ParentNode = document.body): HTMLButtonElement | undefined {
    return [...within.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text || item.getAttribute('aria-label') === text)
  }

  async click(target: Element | undefined | null): Promise<void> {
    expect(target).toBeTruthy()
    await act(async () => (target as HTMLElement).click())
    await idle()
  }

  async type(input: HTMLInputElement | null | undefined, value: string): Promise<void> {
    expect(input).toBeTruthy()
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!
    await act(async () => {
      setter.call(input, value)
      input!.dispatchEvent(new Event('input', { bubbles: true }))
      input!.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await idle()
  }

  async check(input: HTMLInputElement | null | undefined): Promise<void> {
    await this.click(input)
  }

  /** The dialog that stands on top (the last one drawn). */
  get dialog(): HTMLElement | null {
    const all = document.body.querySelectorAll<HTMLElement>('[role="dialog"]')
    return all.length ? all[all.length - 1] : null
  }

  /** Presses a button of the dialog on top. */
  async inDialog(text: string): Promise<void> {
    const dialog = this.dialog
    expect(dialog).toBeTruthy()
    await this.click(this.button(text, dialog!))
  }

  select(label: string): HTMLSelectElement | null {
    return document.body.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)
  }

  async choose(select: Element | null, value: string): Promise<void> {
    expect(select).toBeTruthy()
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(select, value)
      select!.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await idle()
  }
}
