/**
 * The search of the catalogues in a real browser, headless: the quick search of the head and the page of the search in
 * Discover. The sources stay off in the tests (nothing may ask a real service), so the answers of the catalogues are the
 * ones the backend gives in its tests (invented books), handed to the page in place of `/api/v1/search/catalog`; the rest of
 * the page runs against the real backend. What the unit tests cannot see: that both places show the same hits in the same
 * order, that the quick search leads to the page with the words, that the page asks again by the ticket while the answer is
 * pending, that one press opens the dialog with the form chosen, that the editions open and close, and that the page keeps to
 * the width of a phone.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

import { shots } from './shots'

// The answers are handed to the page in place of the server's: a service worker would ask the server itself (WebKit routes
// nothing a worker asks).
test.use({ serviceWorkers: 'block' })

function hit(token: string, over: Record<string, unknown> = {}) {
  return {
    key: `key-${token}`, token, owned_work_id: null, title: 'Das Salztor', language: 'de', allowed: true, languages: ['de', 'en'],
    authors: ['Ada Sorrel'], year: 2014, series: 'Die Laternen-Saga', series_pos: 2, label: 'work', group: 'match', ebook: true, audio: true,
    procurable: true, why_not: '', cover_url: null, sources: ['openlibrary', 'dnb', 'audible'], editions: [], ...over,
  }
}

const EDITIONS = [
  { source: 'dnb', form: 'ebook', language: 'de', title: 'Das Salztor', subtitle: 'Roman', year: 2015, isbn: '9783000070042', asin: '', publisher: 'Beispielverlag', narrators: [], minutes: null, variant: '', pages: 470 },
  { source: 'audible', form: 'audio', language: 'de', title: 'Das Salztor', subtitle: '', year: 2019, isbn: '', asin: 'B070000002', publisher: '', narrators: ['Mara Quell'], minutes: 900, variant: 'unabridged', pages: null },
  { source: 'openlibrary', form: 'ebook', language: 'en', title: 'The Salt Gate', subtitle: '', year: 2014, isbn: '9780000070035', asin: '', publisher: '', narrators: [], minutes: null, variant: '', pages: 460 },
]
const ITEMS = [
  hit('tok-salt', { editions: EDITIONS }),
  hit('tok-road', { title: 'Die Laternenstraße', series_pos: 1, group: 'series', year: 2012 }),
  hit('tok-ember', { title: 'Die letzte Glut', series_pos: 3, group: 'series', year: 2016 }),
  hit('tok-play', { title: 'Das Salztor', label: 'drama', group: 'related', ebook: false, series: '', series_pos: null, procurable: false, why_not: 'not_a_book' }),
  hit('tok-guide', { title: 'Ada Sorrels Das Salztor', authors: ['Lena Brück'], label: 'guide', group: 'related', series: '', series_pos: null, procurable: false, why_not: 'not_a_book' }),
  hit('tok-phare', { title: 'La Maison du Phare', authors: ['Odile Ferrand'], allowed: false, language: '', languages: ['fr'], group: 'elsewhere', series: '', series_pos: null, year: 1998 }),
  ...Array.from({ length: 4 }, (_unused, index) => hit(`tok-more-${index}`, { title: `Salztor und Laterne ${index + 1}`, authors: ['Theo Brand'], group: 'more', series: '', series_pos: null })),
]
const ANSWER = { available: true, failed: false, pending: false, ticket: 'ticket-e2e-0001', items: ITEMS, people: [] }

/** The catalogues answer as the backend does in its tests; ``pending`` first when asked to. */
async function catalogues(page: Page, { pending = false } = {}): Promise<{ posts: string[] }> {
  const posts: string[] = []
  await page.route('**/api/v1/search/catalog', async (route: Route) => {
    posts.push(String(route.request().postDataJSON()?.q ?? ''))
    await route.fulfill({ json: pending ? { ...ANSWER, pending: true, items: ITEMS.slice(0, 2) } : ANSWER })
  })
  await page.route('**/api/v1/search/catalog/ticket-e2e-0001', (route: Route) => route.fulfill({ json: ANSWER }))
  // The person may acquire (the dialog itself is tried in acquire.spec.ts against the real backend).
  await page.route('**/api/v1/procure/counts', (route: Route) =>
    route.fulfill({ json: { allowed: true, seeking: 0, running: 0, needs: 0, blocklist: 0, automatic: true, indexers: 1, clients_down: [] } }),
  )
  await page.route('**/api/v1/procure/book-options?book=*', (route: Route) => {
    const kind = { state: 'free', wish_id: null, have: null, goal: 'epub', libraries: [{ id: 1, name: 'Books', writable: true, write_problem: '' }], default_library: 1, takes: ['epub'], abridged: false }
    return route.fulfill({
      json: { allowed: true, kinds: [{ ...kind, kind: 'ebook' }, { ...kind, kind: 'audio', goal: 'flac', takes: ['flac'] }], own_language: 'en', original_language: 'en', languages: ['de', 'en'], release_date: '', automatic: true, indexers: 1 },
    })
  })
  return { posts }
}

test('the page of the search: the book asked for first, its series, the rest, what is set apart and the books elsewhere', async ({ page }) => {
  const { posts } = await catalogues(page)
  await page.goto('/discover')
  const field = page.getByTestId('discover-search').locator('input')
  await field.fill('salztor')
  await field.press('Enter')
  await expect(page).toHaveURL(/\/discover\/search\?q=salztor$/)
  await expect.poll(() => posts).toEqual(['salztor'])
  const groups = page.locator('[data-testid^="search-group-"]')
  await expect(groups).toHaveCount(5)
  // The order of a library, whatever order the hits come in.
  expect(await groups.evaluateAll((sections) => sections.map((section) => section.getAttribute('data-testid')))).toEqual([
    'search-group-match', 'search-group-series', 'search-group-more', 'search-group-related', 'search-group-elsewhere',
  ])
  await expect(groups.nth(0).getByTestId('search-hit')).toHaveCount(1)
  const first = page.getByTestId('search-hit').first()
  await expect(first.locator('.hit-title b')).toHaveText('Das Salztor')
  await expect(first.locator('.hit-body > small')).toHaveText('Ada Sorrel · 2014 · Book 2 · Die Laternen-Saga')
  await expect(page.getByTestId('search-group-series').getByTestId('search-hit').locator('.hit-title b')).toHaveText(['Die Laternenstraße', 'Die letzte Glut'])
  await expect(page.getByTestId('search-group-related').getByTestId('hit-label')).toHaveText(['Audio drama', 'Study guide'])
  await expect(page.getByTestId('search-group-elsewhere').getByTestId('hit-elsewhere')).toHaveText('available in French')
  // The editions open and close.
  await expect(first.getByTestId('hit-edition')).toHaveCount(0)
  await first.getByTestId('hit-editions').click()
  await expect(first.getByTestId('hit-edition')).toHaveCount(3)
  await expect(first.getByTestId('hit-edition').nth(1)).toContainText('read by Mara Quell')
  await shots(page, 'suche-seite')
  await first.getByTestId('hit-editions').click()
  await expect(first.getByTestId('hit-edition')).toHaveCount(0)
})

test('one press opens the dialog to acquire with the form chosen', async ({ page }) => {
  await catalogues(page)
  await page.goto('/discover/search?q=salztor')
  const first = page.getByTestId('search-hit').first()
  await expect(first.locator('[data-testid^="hit-acquire-"]')).toHaveText(['E-book', 'Audiobook', 'Both'])
  await first.getByTestId('hit-acquire-audio').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Das Salztor')
  await expect(dialog.locator('[data-what="audio"]')).toHaveAttribute('aria-checked', 'true')
})

test('the quick search shows the first of the same hits and leads to all of them', async ({ page }) => {
  await catalogues(page)
  await page.goto('/')
  await page.locator('#q').fill('salztor')
  const list = page.getByTestId('results')
  const hits = list.getByTestId('catalog-hit')
  await expect(hits).toHaveCount(6)
  await expect(hits.locator('b')).toHaveText(ITEMS.slice(0, 6).map((item) => String(item.title)))
  await shots(page, 'suche-kopf')
  await list.getByTestId('catalog-all').click()
  await expect(page).toHaveURL(/\/discover\/search\?q=salztor$/)
  await expect(page.getByTestId('search-hit')).toHaveCount(ITEMS.length)
})

test('while the sources are still asked, the page asks again by the ticket', async ({ page }) => {
  await catalogues(page, { pending: true })
  const again = page.waitForRequest('**/api/v1/search/catalog/ticket-e2e-0001')
  await page.goto('/discover/search?q=salztor')
  await again
  await expect(page.getByTestId('search-hit')).toHaveCount(ITEMS.length)
  await expect(page.getByTestId('search-pending')).toHaveCount(0)
})

test('the page keeps to the width of a phone', async ({ page }) => {
  await catalogues(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/discover/search?q=salztor')
  await expect(page.getByTestId('search-hit')).toHaveCount(ITEMS.length)
  await page.getByTestId('search-hit').first().getByTestId('hit-editions').click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
})

test('a hit gets its page when it is opened, and a search left while the sources are still asked is stopped on the server', async ({ page }) => {
  await catalogues(page, { pending: true })
  const stopped: string[] = []
  const made: unknown[] = []
  const fresh = ITEMS.slice(0, 2).map((item) => ({ ...item, token: null }))
  await page.unroute('**/api/v1/search/catalog')
  await page.route('**/api/v1/search/catalog', (route: Route) => route.fulfill({ json: { ...ANSWER, pending: true, items: fresh } }))
  await page.unroute('**/api/v1/search/catalog/ticket-e2e-0001')
  await page.route('**/api/v1/search/catalog/ticket-e2e-0001', async (route: Route) => {
    if (route.request().method() === 'DELETE') {
      stopped.push(route.request().url())
      return route.fulfill({ status: 204, body: '' })
    }
    // (Still pending: the sources are slow, so slow that the page would not give up on its own for a long while.)
    await new Promise((resolve) => setTimeout(resolve, 1500))
    return route.fulfill({ json: { ...ANSWER, pending: true, items: fresh } })
  })
  await page.route('**/api/v1/search/catalog/ticket-e2e-0001/books', (route: Route) => {
    made.push(route.request().postDataJSON())
    return route.fulfill({ json: { token: 'tok-made' } })
  })
  await page.goto('/discover/search?q=salztor')
  await expect(page.getByTestId('search-hit')).toHaveCount(2)
  await expect(page.getByTestId('search-pending')).toBeVisible()
  expect(stopped).toEqual([])
  // Opened: its page is made now.
  await page.getByTestId('search-hit').first().locator('.hit-title').click()
  await expect(page).toHaveURL(/\/discover\/book\/tok-made$/)
  expect(made).toEqual([{ key: 'key-tok-salt' }])
  // (The page of the book lies over the search, which goes on.)
  expect(stopped).toEqual([])
  // Left for another place: the server is told at once, not when the page would have stopped asking on its own.
  await expect(page.getByTestId('foreign')).toBeVisible()
  await page.goBack()
  await expect(page.getByTestId('foreign')).toHaveCount(0)
  await expect(page.getByTestId('search-hit')).toHaveCount(2)
  expect(stopped).toEqual([])
  await page.locator('nav.tabs a').first().click()
  await expect.poll(() => stopped.length, { timeout: 2000 }).toBe(1)
})

test('the search speaks German to a person who reads German, in the head and on its page', async ({ page }) => {
  const TAB = { 'X-Nexbooks-Client': 'tab-e2e-search' }
  await catalogues(page)
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  try {
    await page.goto('/')
    await page.locator('#q').fill('salztor')
    const list = page.getByTestId('results')
    await expect(list.getByTestId('catalog-hit')).toHaveCount(6)
    await expect(list.getByTestId('catalog-hit').first().locator('[data-testid^="hit-acquire-"]')).toHaveText(['E-Book', 'Hörbuch', 'Beides'])
    await expect(list.locator('.res-head').last()).toContainText('Im Katalog')
    await shots(page, 'suche-kopf-de')
    await page.goto('/discover/search?q=salztor')
    await expect(page.getByTestId('search-group-related').getByTestId('hit-label')).toHaveText(['Hörspiel', 'Lektürehilfe'])
    await expect(page.getByTestId('search-group-series').locator('h2')).toHaveText('Weitere Bände von Die Laternen-Saga')
    await shots(page, 'suche-seite-de')
  } finally {
    await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
  }
})

test('a source that is not answering is named in both places, and the quick search gives a pending question up on Escape', async ({ page }) => {
  await catalogues(page, { pending: true })
  const stopped: string[] = []
  const resting = [{ source: 'openlibrary', until: null }]
  await page.unroute('**/api/v1/search/catalog')
  await page.route('**/api/v1/search/catalog', (route: Route) => route.fulfill({ json: { ...ANSWER, pending: true, items: ITEMS.slice(0, 2), resting } }))
  await page.unroute('**/api/v1/search/catalog/ticket-e2e-0001')
  await page.route('**/api/v1/search/catalog/ticket-e2e-0001', async (route: Route) => {
    if (route.request().method() === 'DELETE') {
      stopped.push(route.request().url())
      return route.fulfill({ status: 204, body: '' })
    }
    await new Promise((resolve) => setTimeout(resolve, 1500))
    return route.fulfill({ json: { ...ANSWER, pending: true, items: ITEMS.slice(0, 2), resting } })
  })
  await page.goto('/')
  await page.locator('#q').fill('salztor')
  const list = page.getByTestId('results')
  await expect(list.getByTestId('catalog-resting')).toHaveText('Open Library is not answering right now, the results are incomplete.')
  expect(stopped).toEqual([])
  await page.locator('#q').press('Escape')
  await expect.poll(() => stopped.length, { timeout: 2000 }).toBe(1)
  await page.unroute('**/api/v1/search/catalog')
  await page.route('**/api/v1/search/catalog', (route: Route) => route.fulfill({ json: { ...ANSWER, items: [], resting } }))
  await page.goto('/discover/search?q=salztor')
  await expect(page.getByTestId('search-resting')).toHaveText('Open Library is not answering right now, so there are no results yet.')
  await expect(page.getByText('Nothing found.')).toHaveCount(0)
})
