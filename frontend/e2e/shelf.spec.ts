/**
 * The shelf, the page of a book and the search in a real browser, headless, against the real backend with six
 * invented books (`e2e/shelf-books.json`, made by `backend/tools/make_library.py`) in a library the first test makes.
 * What the unit tests cannot see: that the turn from the spine really runs and ends, that Escape and the way back leave the
 * shelf where it was, that a reload on a book shows the book, that the key `/` and the arrows work in a real field, that
 * nothing moves with reduced motion, and that the phone keeps the page in one column.
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-shelf00' }

async function ensureLibrary(page: Page): Promise<void> {
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number }[]
  if (mine.length === 0) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBe(6)
}

async function shelf(page: Page): Promise<void> {
  await ensureLibrary(page)
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'shelf' } })
  await page.goto('/')
  await expect(page.locator('.spine').first()).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}

test('the covers of the library have one size, a recording alone as well', async ({ page }) => {
  await ensureLibrary(page)
  await page.request.put('/api/v1/me/preferences', { headers: TAB, data: { view: 'grid' } })
  await page.goto('/')
  await expect(page.locator('.cover.is-audio').first()).toBeVisible()
  const heights = await page.locator('.book .cover').evaluateAll((items) => items.map((item) => Math.round(item.getBoundingClientRect().height)))
  expect(heights.length).toBeGreaterThan(1)
  expect(new Set(heights).size).toBe(1)
})

test.describe('the shelf', () => {
  test('stands as sections of spines with the numbers of the genres, and shows a tip over a spine', async ({ page }) => {
    await shelf(page)
    await expect(page.locator('.spine')).toHaveCount(6)
    await expect(page.locator('section.section h2')).toContainText(['Novels', 'Crime & Thrillers', 'Fantasy', 'Non-fiction'])
    await expect(page.locator('.genres .g').first()).toContainText('All 6')
    // The recording alone is a case; a book with a recording wears headphones.
    await expect(page.locator('.spine.is-audio .top-deco')).toHaveText('AUDIOBOOK')
    await expect(page.locator('.spine:not(.is-audio) .ic')).toHaveCount(1)
    await page.locator('.spine[aria-label^="Nebel"]').hover()
    await expect(page.locator('[data-testid="spine-tip"]')).toContainText('Jonas Lindholm')
    await expect(page.locator('[data-testid="spine-tip"]')).toContainText('Die Deich-Krimis 1')
    await page.mouse.move(5, 5)
    await expect(page.locator('[data-testid="spine-tip"]')).toHaveCount(0)
  })

  test('shows the covers instead, and keeps the choice after a reload', async ({ page }) => {
    await shelf(page)
    await page.getByRole('button', { name: 'Covers' }).click()
    await expect(page.locator('[data-testid="book"]')).toHaveCount(6)
    await page.reload()
    await expect(page.locator('[data-testid="book"]').first()).toBeVisible()
    await page.getByRole('button', { name: 'Shelf' }).click()
    await expect(page.locator('.spine').first()).toBeVisible()
  })

  test('turns a spine to its cover, and Escape, the way back and a reload behave', async ({ page }) => {
    await shelf(page)
    await page.evaluate(() => window.scrollTo(0, 150))
    const before = await page.evaluate(() => window.scrollY)
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    // The turn is drawn over the page while it runs, and gone when it is over.
    await expect(page.locator('.flip')).toBeVisible()
    await expect(page.locator('.detail.show')).toBeVisible()
    await expect(page.locator('.flip')).toBeHidden({ timeout: 4000 })
    await expect(page.locator('.d-title')).toHaveText('Schwarzer Sand')
    expect(page.url()).toMatch(/\/work\/\d+$/)
    await expect(page.locator('.twin')).toHaveCount(2)
    await expect(page.locator('.twin.missing')).toContainText('No audiobook')
    // The way back of the browser closes the page and leaves the shelf where it was.
    await page.goBack()
    await expect(page.locator('.detail')).toHaveCount(0)
    // Where it was, give or take the pixel the lock of the page may take.
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThanOrEqual(2)
    await expect(page.locator('.spine[aria-label^="Schwarzer"]')).toBeFocused()
    // Escape does the same.
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe('/')
    // A reload on the page of a book shows the book (without a turn).
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await page.reload()
    await expect(page.locator('.d-title')).toHaveText('Schwarzer Sand')
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
  })

  test('shows the series as a small shelf and leads from one volume to the next', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine[aria-label^="Nebel"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    const mini = page.locator('.mini-shelf').first()
    // The volumes of the series the shelf has (Discover may have added ghosts for those it lacks: `discover.spec.ts`).
    await expect(mini.locator('.spine:not(.ghost)')).toHaveCount(2)
    await expect(mini.locator('.spine.current')).toHaveAttribute('aria-label', /Nebel/)
    await mini.locator('.spine:not(.current):not(.ghost)').click()
    await expect(page.locator('.d-title')).toHaveText('Schwarzer Sand')
  })

  test('the page of a book covers the whole window: no strip is kept beside it for a scrollbar', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine').first().click()
    await expect(page.locator('.detail.show')).toBeVisible()
    const edge = await page.evaluate(() => {
      const element = document.elementFromPoint(window.innerWidth - 3, window.innerHeight / 2)
      return { inDetail: !!element?.closest('.detail'), gap: window.innerWidth - document.documentElement.clientWidth }
    })
    expect(edge.gap).toBe(0)
    expect(edge.inDetail).toBe(true)
  })

  test('finds a book with the keys: slash, letters, the arrows, Enter; and Escape closes the list', async ({ page }) => {
    await shelf(page)
    await page.keyboard.press('/')
    await expect(page.locator('#q')).toBeFocused()
    await page.keyboard.type('deich')
    await expect(page.locator('.results .res')).toHaveCount(2)
    await page.keyboard.press('ArrowDown')
    await expect(page.locator('.results .res').nth(1)).toHaveClass(/act/)
    await page.keyboard.press('Escape')
    await expect(page.locator('.results')).toHaveCount(0)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    // To the spine (it glows), then the book opens.
    await expect(page.locator('.detail.show')).toBeVisible({ timeout: 8000 })
    await expect(page.locator('.d-title')).toHaveText(/Nebel|Schwarzer/)
    await expect(page.locator('.d-eyebrow')).toContainText('Die Deich-Krimis')
  })

  test('finds by the start of words in the people, the series and the description, and not by what is in no word', async ({ page }) => {
    await shelf(page)
    for (const [q, title] of [['feldk', 'Der Leuchtturmwaerter'], ['weberin', 'Die Krone der Weberinnen'], ['saline', 'Kleine Geschichte des Salzes'], ['vienne', 'Stimmen im Moor']] as const) {
      await page.fill('#q', q)
      // (The authors stand first: "feldk" names Mara Feldkamp before her book.)
      await expect(page.locator('.results .res:not(.res-au) b').first()).toHaveText(title)
    }
    await page.fill('#q', 'turm')
    await expect(page.locator('.results .none')).toHaveText('Nothing found.')
  })

  test('has no catalogue while no source is switched on, and says nothing about it', async ({ page }) => {
    await shelf(page)
    await page.fill('#q', 'deich')
    await expect(page.locator('.results .res')).toHaveCount(2)
    await page.waitForTimeout(1200)
    await expect(page.locator('.res-head')).toHaveText(['In your library'])
  })

  test('downloads a file of a book', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('.twin a.tw-file').first().click()])
    expect(download.suggestedFilename()).toMatch(/\.epub$/)
  })
})

/** Notes the pushes onto the history and the steps of every animation, before the page starts. */
async function listen(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __pushes: string[]; __steps: { cls: string; duration: number; easing: string }[] }
    w.__pushes = []
    w.__steps = []
    const push = history.pushState.bind(history)
    history.pushState = (...args: Parameters<History['pushState']>) => {
      w.__pushes.push(String(args[2]))
      return push(...args)
    }
    const animate = Element.prototype.animate
    Element.prototype.animate = function (this: Element, keyframes: Keyframe[] | PropertyIndexedKeyframes | null, options?: number | KeyframeAnimationOptions) {
      const timing = options as KeyframeAnimationOptions
      w.__steps.push({ cls: this.className, duration: Number(timing.duration), easing: String(timing.easing) })
      return animate.call(this, keyframes, options)
    }
  })
}

/** The turn runs and is over: the layer of the turn shows, then it is gone. */
async function turnDone(page: Page): Promise<void> {
  await expect(page.locator('.flip')).toBeVisible({ timeout: 4000 })
  await expect(page.locator('.flip')).toBeHidden({ timeout: 4000 })
}

const pushes = (page: Page) => page.evaluate(() => (window as unknown as { __pushes: string[] }).__pushes.length)

/** The page has stopped scrolling: the wheel of a mouse starts a smooth scroll that goes on for a while after the poll that saw
 * it move, and a scroll that is still on its way carries on over a `scrollTo` and over the place the app restores. */
async function scrollSettled(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        let last = -1
        let still = 0
        const look = () => {
          const now = window.scrollY
          still = now === last ? still + 1 : 0
          last = now
          if (still >= 8) resolve()
          else requestAnimationFrame(look)
        }
        look()
      }),
  )
}

/** The shelf as it must be after any way of closing a book: no layer over it, free to scroll, a spine under the pointer. */
async function expectFreeShelf(page: Page): Promise<void> {
  await expect(page.locator('.detail')).toHaveCount(0)
  expect(new URL(page.url()).pathname).toBe('/')
  expect(await page.evaluate(() => document.body.classList.contains('lock'))).toBe(false)
  await scrollSettled(page)
  const spine = await page.locator('.spine').first().boundingBox()
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x!, y!)?.closest('.spine, .detail, .flip')?.className ?? '', [spine!.x + spine!.width / 2, spine!.y + spine!.height / 2])
  expect(hit).toContain('spine')
  const before = await page.evaluate(() => window.scrollY)
  await page.mouse.move(640, 400)
  await page.mouse.wheel(0, 300)
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before)
  // Back to the top, for the next way of leaving: only once the scroll has come to rest, and only once it is at the top.
  await scrollSettled(page)
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  await scrollSettled(page)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
}

test.describe('a quick hand', () => {
  test('a double click opens the book once, and Escape, the Library button and the way back each leave a free shelf', async ({ page }) => {
    await listen(page)
    await shelf(page)
    let opened = 0
    for (const way of ['escape', 'button', 'back'] as const) {
      await page.locator('.spine[aria-label^="Schwarzer"]').dblclick()
      opened += 1
      await expect(page.locator('.detail.show')).toBeVisible()
      await turnDone(page)
      // One entry in the history for each opening, never two.
      expect(await pushes(page)).toBe(opened)
      if (way === 'escape') await page.keyboard.press('Escape')
      if (way === 'button') await page.locator('.d-back').click()
      if (way === 'back') await page.goBack()
      await expectFreeShelf(page)
    }
  })

  test('two quick presses on two spines open the first book only', async ({ page }) => {
    await listen(page)
    await page.setViewportSize({ width: 1280, height: 1000 })
    await shelf(page)
    // The first two spines of the shelf, both on the screen.
    const first = await page.locator('.spine').nth(0).boundingBox()
    const second = await page.locator('.spine').nth(1).boundingBox()
    const names = await page.locator('.spine').evaluateAll((spines) => spines.slice(0, 2).map((spine) => spine.getAttribute('aria-label') ?? ''))
    expect(first && second && first.y + first.height < 1000 && second.y + second.height < 1000).toBeTruthy()
    await page.mouse.click(first!.x + first!.width / 2, first!.y + first!.height / 2)
    await page.mouse.click(second!.x + second!.width / 2, second!.y + second!.height / 2)
    await expect(page.locator('.detail.show')).toBeVisible()
    await turnDone(page)
    await expect(page.locator('.d-title')).toHaveText(names[0]!.split(', ')[0]!)
    expect(await pushes(page)).toBe(1)
    await page.keyboard.press('Escape')
    await expectFreeShelf(page)
  })

  test('a double click on a card of the grid opens the book once as well', async ({ page }) => {
    await listen(page)
    await shelf(page)
    await page.getByRole('button', { name: 'Covers' }).click()
    await page.locator('[data-testid="book"]', { hasText: 'Schwarzer Sand' }).dblclick()
    await expect(page.locator('.detail.show')).toBeVisible()
    await turnDone(page)
    expect(await pushes(page)).toBe(1)
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe('/')
    await page.getByRole('button', { name: 'Shelf' }).click()
  })
})

test.describe('the turn', () => {
  test('runs with the times and curves of the design: 360 ms and 420 ms from a spine, 520 ms from a cover of the grid', async ({ page }) => {
    await listen(page)
    await shelf(page)
    const steps = () => page.evaluate(() => (window as unknown as { __steps: { cls: string; duration: number; easing: string }[] }).__steps.filter((step) => /f-spine|f-cover/.test(step.cls)))
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await turnDone(page)
    expect(await steps()).toEqual([
      { cls: 'f-spine', duration: 360, easing: 'cubic-bezier(.5,0,.75,0)' },
      { cls: 'f-cover', duration: 420, easing: 'cubic-bezier(.2,.7,.3,1)' },
    ])
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
    await page.getByRole('button', { name: 'Covers' }).click()
    await page.locator('[data-testid="book"]', { hasText: 'Schwarzer Sand' }).click()
    await turnDone(page)
    expect((await steps()).slice(2)).toEqual([{ cls: 'f-cover', duration: 520, easing: 'cubic-bezier(.2,.8,.2,1)' }])
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Shelf' }).click()
  })

  test('grows the cover of a card of the grid from the cover of the card to its place, in proportion, without a jump at the end', async ({ page }) => {
    await shelf(page)
    await page.getByRole('button', { name: 'Covers' }).click()
    const card = page.locator('[data-testid="book"]', { hasText: 'Schwarzer Sand' })
    await card.scrollIntoViewIfNeeded()
    const cover = (await card.locator('.cover').boundingBox())!
    const whole = (await card.boundingBox())!
    expect(whole.height).toBeGreaterThan(cover.height + 20) // the card is more than its cover
    await page.evaluate(() => {
      const w = window as unknown as { __rec: { x: number; y: number; w: number; h: number }[] }
      w.__rec = []
      const loop = () => {
        const flip = document.querySelector<HTMLElement>('.flip')
        const host = document.querySelector<HTMLElement>('.flip .f-cover')
        if (flip && !flip.hidden && host && host.style.display !== 'none') {
          const r = host.getBoundingClientRect()
          w.__rec.push({ x: r.x, y: r.y, w: r.width, h: r.height })
        }
        requestAnimationFrame(loop)
      }
      loop()
    })
    await card.click()
    await turnDone(page)
    const target = (await page.locator('.d-cover .cover').boundingBox())!
    const rec = await page.evaluate(() => (window as unknown as { __rec: { x: number; y: number; w: number; h: number }[] }).__rec)
    expect(rec.length).toBeGreaterThan(6)
    // The first picture is (nearly) the cover of the card, the last is (nearly) the place on the page, and the picture is
    // a cover all along (two to three), never the tall box of the whole card.
    expect(Math.abs(rec[0]!.w - cover.width)).toBeLessThan(cover.width * 0.2)
    expect(Math.abs(rec[0]!.h - cover.height)).toBeLessThan(cover.height * 0.2)
    for (const frame of rec) {
      expect(frame.h / frame.w).toBeGreaterThan(1.45)
      expect(frame.h / frame.w).toBeLessThan(1.55)
    }
    const last = rec[rec.length - 1]!
    expect(Math.abs(last.w - target.width)).toBeLessThan(target.width * 0.06)
    expect(Math.abs(last.h - target.height)).toBeLessThan(target.height * 0.06)
    expect(Math.abs(last.x - target.x)).toBeLessThan(target.width * 0.06)
    expect(Math.abs(last.y - target.y)).toBeLessThan(target.height * 0.06)
    // It only grows.
    for (let index = 1; index < rec.length; index++) expect(rec[index]!.w).toBeGreaterThanOrEqual(rec[index - 1]!.w - 0.5)
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Shelf' }).click()
  })
})

test.describe('the page of a book', () => {
  test('offers the search of a missing edition as the design draws its button: not dimmed, its size and its border', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await turnDone(page)
    // The operator made the library and manages it: the right to fetch books is in it. Beside it may stand "Get" and
    // "Upload" (not when another test left a wish for the book): the one measured is the search.
    const button = page.locator('.twin.missing [data-testid="releases"]')
    await expect(button).toBeEnabled()
    await expect(button).toHaveText('See releases')
    // The values the design measures on the same button.
    const style = await button.evaluate((element) => {
      const css = getComputedStyle(element)
      return { opacity: css.opacity, height: css.height, padding: css.padding, radius: css.borderRadius, size: css.fontSize, weight: css.fontWeight, border: css.borderTopWidth, fill: css.backgroundColor }
    })
    expect(style).toEqual({ opacity: '1', height: '36px', padding: '0px 14px', radius: '10px', size: '13.5px', weight: '700', border: '1px', fill: 'rgba(0, 0, 0, 0)' })
  })

  test('keeps the Tab key on the page: forwards and backwards, the focus never reaches the shelf behind it', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine[aria-label^="Nebel"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await turnDone(page)
    const inside = () => page.evaluate(() => Boolean(document.activeElement?.closest('.detail')))
    for (let step = 0; step < 40; step++) {
      await page.keyboard.press('Tab')
      expect(await inside(), `Tab ${step}`).toBe(true)
    }
    for (let step = 0; step < 40; step++) {
      await page.keyboard.press('Shift+Tab')
      expect(await inside(), `Shift+Tab ${step}`).toBe(true)
    }
    // From the page itself, backwards: to the last thing on it.
    await page.locator('.detail').focus()
    await page.keyboard.press('Shift+Tab')
    expect(await inside()).toBe(true)
  })
})

test.describe('without motion', () => {
  test.use({ reducedMotion: 'reduce' })

  test('opens the page at once, nothing turns and nothing glows', async ({ page }) => {
    await shelf(page)
    await page.evaluate(() => {
      ;(window as unknown as { __flip: number }).__flip = 0
      const loop = () => {
        const flip = document.querySelector('.flip')
        if (flip && !(flip as HTMLElement).hidden) (window as unknown as { __flip: number }).__flip++
        requestAnimationFrame(loop)
      }
      loop()
    })
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await page.waitForTimeout(600)
    expect(await page.evaluate(() => (window as unknown as { __flip: number }).__flip)).toBe(0)
    expect(await page.evaluate(() => document.getAnimations().length)).toBe(0)
    await page.keyboard.press('Escape')
    await expect(page.locator('.detail')).toHaveCount(0)
  })
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('keeps the search in a row of its own, the shelf lower, and the twins one under the other', async ({ page }) => {
    await shelf(page)
    const search = await page.locator('.search').boundingBox()
    const tabs = await page.locator('.tabs').boundingBox()
    expect(search!.width).toBeGreaterThan(330)
    expect(search!.y).toBeGreaterThan(tabs!.y)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    const spine = await page.locator('.spine:not(.is-audio)').first().boundingBox()
    expect(spine!.height).toBeLessThan(170)
    await page.locator('.spine[aria-label^="Der Leucht"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    await expect(page.locator('.twin')).toHaveCount(2)
    const twins = await page.locator('.twin').all()
    const first = await twins[0]!.boundingBox()
    const second = await twins[1]!.boundingBox()
    expect(second!.y).toBeGreaterThan(first!.y + first!.height - 1)
    expect(Math.abs(first!.x - second!.x)).toBeLessThan(2)
  })

  test('gives the title of a search result the width of the line: the chips of its editions go under it', async ({ page }) => {
    await shelf(page)
    await page.locator('#q').fill('deich')
    await expect(page.locator('.results .res')).toHaveCount(2)
    const title = await page.locator('.results .res b').first().boundingBox()
    const chips = await page.locator('.results .res .chips').first().boundingBox()
    expect(title!.width).toBeGreaterThan(200)
    expect(chips!.y).toBeGreaterThan(title!.y + title!.height - 1)
  })
})

test.describe('in the dark', () => {
  test.use({ colorScheme: 'dark' })

  test('puts the page of a book in the dark colours of the book', async ({ page }) => {
    await shelf(page)
    await page.locator('.spine[aria-label^="Schwarzer"]').click()
    await expect(page.locator('.detail.show')).toBeVisible()
    // The colour comes as rgb(...) or, for a mix, as color(srgb r g b) with the parts from 0 to 1.
    const brightest = await page.locator('.detail').evaluate((e) => {
      const text = getComputedStyle(e).backgroundColor
      const parts = (text.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
      return Math.max(...parts) * (text.startsWith('color(') ? 255 : 1)
    })
    expect(brightest).toBeLessThan(90)
  })
})
