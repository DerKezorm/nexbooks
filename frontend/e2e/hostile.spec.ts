/**
 * Hostile books in real browsers (Chromium, Firefox and WebKit): books that give a
 * page a media type a browser would draw as a page, style sheets that spell `url(` and `@import` with CSS escapes, pages nested
 * so deeply that a browser's own stack gives out, and the test of the walls that the reader makes before it opens a book.
 *
 * What may never happen, in any browser: a script of a book runs (the marks it would leave on the window of the app, in the
 * storage and on a listener of another host stay empty), a request leaves for another host (neither one the browser saw leave
 * nor one that its policy refused: every request is counted, also the blocked ones), a window opens, the app's window goes
 * somewhere else, a tab crashes. The books are made by `backend/tools/make_hostile_books.py`.
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-hostile0' }
const CANARY = `http://127.0.0.1:${process.env.E2E_CANARY_PORT ?? 18588}`

const editions = new Map<string, number>()
let library = 0

async function ensureLibrary(page: Page): Promise<void> {
  if (library) return
  const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Hostile', path: path.join(process.env.E2E_BOOKS!, 'hostile'), kind: 'ebook', scan_enabled: true } })
  expect(made.ok(), await made.text()).toBe(true)
  library = ((await made.json()) as { id: number }).id
  await expect
    .poll(async () => ((await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { total: number }).total, { timeout: 120_000, intervals: [1000] })
    .toBe(24)
  const list = (await (await page.request.get(`/api/v1/works?library=${library}&per_page=100`)).json()) as { items: { title: string; editions: { id: number }[] }[] }
  for (const item of list.items) editions.set(item.title, item.editions[0].id)
}

test.afterAll(async ({ browser }) => {
  if (!library) return
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  await context.request.delete(`/api/v1/libraries/${library}`, { headers: TAB })
  await context.close()
})

test.beforeEach(async ({ page }) => {
  await ensureLibrary(page)
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { reader_font: 'fraunces', reader_size: 100, reader_line: 150, reader_margin: 'normal', reader_theme: 'light', reader_justify: false, reader_flow: 'paged' } })
})

const pageFrame = (page: Page) => page.frames().find((frame) => frame.url().startsWith('blob:'))

/** What the page of the book looks like once it is open, before the reader has turned a page. */
type Shown = { text: string; scripts: number; handlers: number; policy: string; css: string; attributes: string; depth: number }

type Outcome = {
  shown: Shown
  phase: string
  crashed: boolean
  foreign: string[]
  popups: string[]
  dialogs: string[]
  top: unknown
  stored: string | null
  hits: string[]
  url: string
  status: string
}

/** Opens a book, waits, turns a few pages, and reports everything a book must not be able to cause. */
async function attempt(page: Page, title: string, turns = 3): Promise<Outcome> {
  const own = test.info().project.use.baseURL!
  const foreign: string[] = []
  const popups: string[] = []
  const dialogs: string[] = []
  let crashed = false
  page.on('request', (request) => {
    const url = request.url()
    if (!url.startsWith(own) && !url.startsWith('blob:') && !url.startsWith('data:') && !url.startsWith('about:')) foreign.push(`${request.method()} ${url}`)
  })
  page.on('popup', (popup) => popups.push(popup.url()))
  page.on('dialog', (dialog) => {
    dialogs.push(dialog.message())
    void dialog.dismiss()
  })
  page.on('crash', () => (crashed = true))
  await page.request.get(`${CANARY}/__reset`)
  await page.goto(`/read/${editions.get(title)}`)
  await page.waitForFunction(() => {
    const reader = document.querySelector('[data-testid="reader"]')
    return (reader && reader.getAttribute('data-phase') !== 'loading') || document.querySelector('[data-testid="reader-failed"]')
  }, null, { timeout: 60_000 })
  let phase = (await page.locator('[data-testid="reader"]').getAttribute('data-phase')) ?? 'failed'
  if (phase === 'failed') phase = `failed:${await page.locator('[data-testid="reader-failed"]').getAttribute('data-reason')}`
  await page.waitForTimeout(1500)
  const shown: Shown = (await pageFrame(page)?.evaluate(() => ({
    text: document.body?.innerText ?? '',
    scripts: document.querySelectorAll('script').length,
    handlers: Array.from(document.querySelectorAll('*')).filter((element) => element.getAttributeNames().some((name) => name.startsWith('on'))).length,
    policy: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content') ?? '',
    css: Array.from(document.querySelectorAll('style')).map((style) => style.textContent ?? '').join(' '),
    attributes: Array.from(document.querySelectorAll('[style], [fill], [filter]')).map((element) => element.outerHTML).join(' '),
    depth: (() => {
      let depth = 0
      for (let node: Element | null = document.body; node; node = node.firstElementChild) depth++
      return depth
    })(),
  })).catch(() => undefined)) ?? { text: '', scripts: 0, handlers: 0, policy: '', css: '', attributes: '', depth: 0 }
  for (let turn = 0; turn < turns && !crashed; turn++) {
    await page.keyboard.press('ArrowRight')
    await page.waitForTimeout(500)
  }
  await page.waitForTimeout(500)
  const marks = crashed ? { top: 'crashed', stored: null } : await page.evaluate(() => ({ top: (window as unknown as { __pwn?: unknown }).__pwn ?? null, stored: (() => { try { return localStorage.getItem('pwn') } catch { return null } })() }))
  const status = crashed ? '' : ((await page.locator('[data-testid="reader-status"]').textContent({ timeout: 100 }).catch(() => '')) ?? '')
  return { shown, phase, crashed, foreign, popups, dialogs, top: marks.top, stored: marks.stored, hits: (await (await page.request.get(`${CANARY}/__hits`)).json()) as string[], url: page.url(), status }
}

/** The same demand for every hostile book. */
function expectNothingHappened(outcome: Outcome, title: string): void {
  expect(outcome.crashed, `${title}: the tab crashed`).toBe(false)
  expect(outcome.top, `${title}: a script marked the window of the app`).toBeNull()
  expect(outcome.stored, `${title}: a script wrote to the storage`).toBeNull()
  expect(outcome.foreign, `${title}: a request left for another host`).toEqual([])
  expect(outcome.hits, `${title}: another host was visited`).toEqual([])
  expect(outcome.popups, `${title}: a window was opened`).toEqual([])
  expect(outcome.dialogs, `${title}: a dialog was shown`).toEqual([])
  expect(outcome.url, `${title}: the app's window went elsewhere`).toMatch(/\/read\/\d+$/)
}

test.describe('a page with a media type a browser would draw as a page', () => {
  // The pages the cleaning knows by the type of the manifest, whatever its case or parameters.
  for (const title of ['type-xml', 'type-text-xml', 'type-param', 'type-capitals', 'type-html-capitals', 'type-xml-param']) {
    test(`${title}: cleaned like any page, shown, and no script runs`, async ({ page }) => {
      const outcome = await attempt(page, title)
      expect(outcome.phase).toBe('open')
      expectNothingHappened(outcome, title)
      expect(outcome.shown.text).toContain('Visible text of the page')
      expect(outcome.shown.scripts).toBe(0)
      expect(outcome.shown.handlers).toBe(0)
      expect(outcome.shown.policy).toContain("script-src 'none'")
    })
  }

  // A type that is no page at all, or an XML document that is no XHTML: an empty page, and not a byte of the file.
  for (const title of ['type-plain', 'type-dtbook', 'type-octet', 'type-mathml', 'type-xml-rss']) {
    test(`${title}: an empty page instead of the file`, async ({ page }) => {
      const outcome = await attempt(page, title)
      expect(outcome.phase).toBe('open')
      expectNothingHappened(outcome, title)
      expect(outcome.shown.text).not.toContain('Visible text of the page')
      expect(outcome.shown.text).not.toContain('feed')
    })
  }

  test('an SVG as a page of the spine: no script runs, whatever the browser does with it', async ({ page }) => {
    const outcome = await attempt(page, 'page-svg', 4)
    expectNothingHappened(outcome, 'page-svg')
  })
})

test.describe('a style sheet that writes what fetches in every way CSS allows', () => {
  test('causes no request at all: not one that was refused by the policy either', async ({ page }) => {
    const outcome = await attempt(page, 'css-escapes')
    expect(outcome.phase).toBe('open')
    expectNothingHappened(outcome, 'css-escapes')
    // The page is there, with its styles, and no address of the listener left in them.
    expect(outcome.shown.text).toContain('Visible text of the page')
    expect(outcome.shown.css).not.toContain(CANARY.replace('http://', ''))
    expect(outcome.shown.attributes).not.toContain(CANARY.replace('http://', ''))
  })
})

test.describe('a page nested, or filled, beyond what a browser can draw', () => {
  for (const title of ['deep-xhtml', 'deep-html', 'deep-100']) {
    test(`${title}: opens without a crash, with its text, flattened, and says so`, async ({ page }) => {
      test.setTimeout(120_000)
      const outcome = await attempt(page, title, 4)
      expect(outcome.phase).toBe('open')
      expectNothingHappened(outcome, title)
      await expect(page.locator('[data-testid="reader-status"]')).toContainText('nested so deeply')
      expect(outcome.shown.text).toMatch(/deep text|below the depth/)
      expect(outcome.shown.depth).toBeLessThan(80)
    })
  }

  test('many-nodes: opens without a crash and says that the rest was cut off', async ({ page }) => {
    test.setTimeout(120_000)
    const outcome = await attempt(page, 'many-nodes', 2)
    expect(outcome.phase).toBe('open')
    expectNothingHappened(outcome, 'many-nodes')
    await expect(page.locator('[data-testid="reader-status"]')).toContainText('cut off', { timeout: 20_000 })
  })

  test('inline-run: 20,000 inline elements in one place are drawn in good time in every engine, with all their text', async ({ page }) => {
    test.setTimeout(120_000)
    const outcome = await attempt(page, 'inline-run', 1)
    expect(outcome.phase).toBe('open')
    expectNothingHappened(outcome, 'inline-run')
    expect(outcome.shown.text).toContain('w'.repeat(20000))
    expect(outcome.status).not.toContain('nested so deeply')
    // Laying the page out must not take a minute (the engine that was slow measured 54 seconds for it).
    const before = Date.now()
    await page.screenshot()
    expect(Date.now() - before).toBeLessThan(15_000)
  })

  // Structures up to the depth that is not flattened: the page is drawn as it is, in every browser, and without a notice.
  for (const title of ['nest-div', 'nest-span', 'nest-quote', 'nest-list', 'nest-table', 'nest-inline']) {
    test(`${title}: drawn as it is`, async ({ page }) => {
      const outcome = await attempt(page, title, 2)
      expect(outcome.phase).toBe('open')
      expectNothingHappened(outcome, title)
      expect(outcome.status).not.toContain('nested so deeply')
      expect(outcome.shown.text).toContain('deepest words of the nest')
    })
  }
})

test.describe('the walls around a page are tried before a book is opened', () => {
  /** Serves the frame of the reader as it would be if one wall were gone. */
  async function loosen(page: Page, what: { policy?: boolean; sandbox?: boolean }): Promise<void> {
    await page.route('**/readview.html', async (route) => {
      const response = await route.fetch()
      const headers = { ...response.headers() }
      if (what.policy) delete headers['content-security-policy']
      await route.fulfill({ response, headers })
    })
    if (what.sandbox) {
      await page.route('**/assets/readview-*.js', async (route) => {
        const response = await route.fetch()
        const body = (await response.text()).replace(/:"allow-same-origin"(?=[;,)])/g, ':"allow-same-origin allow-scripts"')
        await route.fulfill({ response, body })
      })
    }
  }

  async function open(page: Page): Promise<string> {
    await page.goto(`/read/${editions.get('type-plain')}`)
    await page.waitForFunction(() => document.querySelector('[data-testid="reader"][data-phase="open"]') || document.querySelector('[data-testid="reader-failed"]'), null, { timeout: 30_000 })
    return (await page.locator('[data-testid="reader"]').getAttribute('data-phase')) ?? ''
  }

  test('with every wall in place the book is opened', async ({ page }) => {
    expect(await open(page)).toBe('open')
  })

  test('where the frame of a page may run scripts and its policy is gone too, no book is opened, and the person is told in plain words', async ({ page }) => {
    await loosen(page, { policy: true, sandbox: true })
    await page.goto(`/read/${editions.get('type-plain')}`)
    const failed = page.locator('[data-testid="reader-failed"]')
    await expect(failed).toBeVisible({ timeout: 30_000 })
    await expect(failed).toHaveAttribute('data-reason', 'unsafe')
    await expect(failed).toContainText('does not keep a book from running its own code')
    await expect(page.getByRole('link', { name: /Download the file/ })).toBeVisible()
    await expect(page.locator('[data-testid="reader-frame"]')).toBeHidden()
  })

  test('where only the policy of the frame is gone: the browsers whose pages cannot run scripts by their frame alone still open the book, Safari does not', async ({ page, browserName }) => {
    await loosen(page, { policy: true })
    const phase = await open(page)
    // WebKit needs `allow-scripts` for its frames (events do not get through otherwise), so there the policy is the wall.
    expect(phase).toBe(browserName === 'webkit' ? 'failed' : 'open')
  })
})
