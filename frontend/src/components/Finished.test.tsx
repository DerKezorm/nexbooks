/**
 * "Finished" on the card of an edition: one press is one request (a double click is one), the day can be named,
 * taking an entry back asks first and sends nothing until the answer is yes, and what the server refuses is said.
 */
import { act } from 'react'

import type { HistoryOut } from '../api/client'
import { changeLanguage } from '../i18n'
import { Page, Server, plain } from '../test/harness'
import { idle } from '../test/wait'
import { dayText } from '../lib/days'
import { FinishRow } from './Finished'

const server = new Server()
const page = new Page()

const entry = (over: Partial<HistoryOut> = {}): HistoryOut => ({ id: 5, work_id: 1, edition_id: 10, kind: 'read', finished_on: '2025-03-04', source: 'hand', created_at: '2025-03-04T10:00:00Z', ...over })

beforeEach(async () => {
  await changeLanguage('de', false)
  server.answers = {}
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('a book that was not finished', () => {
  it('has a button that enters it, once, however often it is pressed before the answer', async () => {
    server.answers = { 'POST /api/v1/editions/10/finished': entry() }
    server.install()
    const changed = vi.fn()
    await page.show(<FinishRow editionId={10} entries={[]} onChange={changed} />)
    const button = page.button('Als beendet markieren')!
    await page.click(button)
    expect(server.changes()).toEqual(['POST /api/v1/editions/10/finished'])
    expect(server.calls.find((c) => c.method === 'POST')!.body).toEqual({})
    expect(changed).toHaveBeenCalledTimes(1)
  })

  it('does not send two requests for two quick presses', async () => {
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => (release = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        server.calls.push({ method: init?.method ?? 'GET', url, body: undefined })
        await gate
        return new Response(JSON.stringify(entry()), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }),
    )
    await page.show(<FinishRow editionId={10} entries={[]} onChange={() => undefined} />)
    const button = page.button('Als beendet markieren')!
    await act(async () => {
      button.click()
      button.click()
      button.click()
    })
    await act(async () => {
      release()
      await idle()
    })
    expect(server.calls.filter((c) => c.method === 'POST').length).toBe(1)
  })

  it('names another day when asked, and sends exactly that day', async () => {
    server.answers = { 'POST /api/v1/editions/10/finished': entry({ finished_on: '2022-07-09' }) }
    server.install()
    await page.show(<FinishRow editionId={10} entries={[]} onChange={() => undefined} />)
    await page.click(document.querySelector('[data-testid="finish-other"]') as HTMLElement)
    await page.type(document.querySelector('[data-testid="finish-date"]') as HTMLInputElement, '2022-07-09')
    await page.click(document.querySelector('[data-testid="finish-date-ok"]') as HTMLElement)
    expect(server.calls.find((c) => c.method === 'POST')!.body).toEqual({ finished_on: '2022-07-09' })
  })

  it('does not offer a day in the future', async () => {
    await page.show(<FinishRow editionId={10} entries={[]} onChange={() => undefined} />)
    await page.click(document.querySelector('[data-testid="finish-other"]') as HTMLElement)
    const input = document.querySelector('[data-testid="finish-date"]') as HTMLInputElement
    expect(input.max).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(input.min).toBe('1900-01-01')
    expect(input.required).toBe(true)
  })

  it('says what the server refuses and sends nothing else', async () => {
    server.fails = { 'POST /api/v1/editions/10/finished': { status: 422, code: 'bad_day' } }
    server.install()
    await page.show(<FinishRow editionId={10} entries={[]} onChange={() => undefined} />)
    await page.click(page.button('Als beendet markieren'))
    expect(plain(document.querySelector('[data-testid="finish-problem"]')!.textContent)).toBe('Das ist kein Tag, den es gibt, oder er liegt in der Zukunft.')
  })
})

describe('a book that was finished', () => {
  it('says the day, in the language of the page, and how many earlier passes there were', async () => {
    await page.show(<FinishRow editionId={10} entries={[entry(), entry({ id: 4, finished_on: '2023-01-01' })]} onChange={() => undefined} />)
    expect(plain(document.querySelector('[data-testid="finish-done"]')!.textContent)).toBe('Beendet am 4. März 2025 · 1 früherer Durchgang')
    expect(page.button('Als beendet markieren')).toBeUndefined()
  })

  it('says it came by itself when the rule at the end of the book entered it', async () => {
    await page.show(<FinishRow editionId={10} entries={[entry({ source: 'auto' })]} onChange={() => undefined} />)
    expect(document.querySelector('[data-testid="finish-done"]')!.getAttribute('title')).toContain('Von nexbooks eingetragen')
  })

  it('asks before it takes the entry back, says whether it can be undone, and sends nothing until the answer is yes', async () => {
    server.answers = { 'DELETE /api/v1/history/5': 204 }
    server.install()
    const changed = vi.fn()
    await page.show(<FinishRow editionId={10} entries={[entry()]} onChange={changed} />)
    await page.click(document.querySelector('[data-testid="finish-undo"]') as HTMLElement)
    expect(page.dialog).toBeTruthy()
    expect(plain(page.dialog!.textContent)).toContain('Du kannst das Buch jederzeit wieder als beendet markieren.')
    expect(plain(page.dialog!.textContent)).toContain('4. März 2025')
    expect(server.changes()).toEqual([])
    // "Abbrechen" sends nothing either.
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    expect(changed).not.toHaveBeenCalled()
    await page.click(document.querySelector('[data-testid="finish-undo"]') as HTMLElement)
    await page.inDialog('Nicht beendet')
    expect(server.changes()).toEqual(['DELETE /api/v1/history/5'])
    expect(changed).toHaveBeenCalledTimes(1)
  })

  it('offers "once more" only when the last pass was not today', async () => {
    const today = new Date()
    const day = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
    await page.show(<FinishRow editionId={10} entries={[entry({ finished_on: day })]} onChange={() => undefined} />)
    expect(document.querySelector('[data-testid="finish-again"]')).toBeNull()
    page.close()
    await page.show(<FinishRow editionId={10} entries={[entry()]} onChange={() => undefined} />)
    expect(document.querySelector('[data-testid="finish-again"]')).toBeTruthy()
  })
})

describe('a day', () => {
  it('is written in the language of the page and left as it is when it is no day', () => {
    expect(dayText('2025-03-04', 'de')).toBe('4. März 2025')
    expect(dayText('2025-03-04', 'en')).toContain('March')
    expect(dayText('nonsense', 'de')).toBe('nonsense')
  })
})
