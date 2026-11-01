/**
 * The frame that draws a book (EPUB, MOBI, AZW3): foliate-js turns the pages here. The frame has no network (its policy,
 * from the server, says so) and keeps the book from running code (see `sanitize.ts` and `selftest.ts`); the page that holds it fetches the file and hands over the bytes, and
 * hears back where the reader is, the contents and what a search found (`lib/readFrame.ts`). A book is made by somebody
 * else: what it contains is cleaned (`sanitize.ts`), kept within limits (`guards.ts`) and shown in a page that carries a
 * policy of its own. Messages count only from the page that holds the frame.
 */
import './readview.css'

import '../vendor/foliate-js/view.js'
import type { FoliateBook, Relocation, TocNode, View } from '../vendor/foliate-js/view.js'
import { Overlayer } from '../vendor/foliate-js/overlayer.js'
import {
  fromParent, isWebKit, openable, PAGE_COLOURS,
  type FromFrame, type ReaderLook, type SearchHit, type TocLine, type ToFrame,
} from '../lib/readFrame'
import { openEpub, openMobi } from './book'
import { chaptersOf, type Chapters } from './chapters'
import { LimitError, onLimit, onNotice } from './guards'
import { scriptRuns } from './selftest'
import { followTextStart, type BookLike } from './start'
import { MARGINS, pageStyles } from './styles'

/** The address of the server this frame came from: the one host the pages of the book may take their typefaces from. */
const origin = new URL(document.baseURI).origin

// How the pages of a book are kept from running scripts. A frame for each page without `allow-scripts` is the plain wall
// (the browser runs no script in it, whatever the page says); Safari, which does not let the reader's events reach such a
// frame (WebKit bug 218086), gets the frame upstream uses, and there the policy that every page carries (`sanitize.ts`)
// forbids every script. Either way the page is cleaned first and the loader loads no script.
const safari = isWebKit(navigator.userAgent, navigator.platform, navigator.maxTouchPoints)
const sandbox = safari ? 'allow-same-origin allow-scripts' : 'allow-same-origin'
;(globalThis as unknown as { nexbooksSandbox: string }).nexbooksSandbox = sandbox

/** The most hits a search lists, and the shortest query it takes. */
const MAX_HITS = 400
const MIN_QUERY = 2
const MAX_QUERY = 200

function tell(message: FromFrame): void {
  // The page's origin is not known here (this frame has none); what is said is only about the book on show.
  window.parent.postMessage(message, '*')
}

let view: View | null = null
let book: FoliateBook | null = null
let look: ReaderLook | null = null
let toc: TocNode[] = []
let chapters: Chapters = chaptersOf([], () => undefined)
let rtl = false
let searchToken = 0
let hitCursor = -1
const hitList: string[] = []

function paint(next: ReaderLook): void {
  const colours = PAGE_COLOURS[next.theme]
  const root = document.documentElement.style
  root.setProperty('--ground', colours.ground)
  root.setProperty('--ink', colours.ink)
  root.setProperty('color-scheme', next.theme === 'dark' ? 'dark' : 'light')
}

function fail(reason: Extract<FromFrame, { type: 'failed' }>['reason']): void {
  tell({ type: 'failed', reason })
}

// A part of the book that breaks a limit while pages are turned (a lie about its size that only shows when it is unpacked).
onLimit((error) => fail(error.reason))

// A page that had to be changed (flattened, cut): the person is told once of each.
const noticed = new Set<string>()
onNotice((kind) => {
  if (noticed.has(kind)) return
  noticed.add(kind)
  tell({ type: 'notice', kind })
})

// ---- The look ----------------------------------------------------------------------------------------------------------

function applyLook(): void {
  if (!view || !look) return
  paint(look)
  const renderer = view.renderer
  if (view.isFixedLayout) return
  const margins = MARGINS[look.margin]
  renderer.setAttribute('flow', look.flow === 'scroll' ? 'scrolled' : 'paginated')
  renderer.setAttribute('gap', margins.gap)
  renderer.setAttribute('max-inline-size', margins.inline)
  renderer.setAttribute('margin', '56px')
  renderer.setAttribute('max-column-count', '2')
  if (look.animate) renderer.setAttribute('animated', '')
  else renderer.removeAttribute('animated')
  renderer.setStyles?.(pageStyles(look))
}

// ---- Taps, keys, wheel -------------------------------------------------------------------------------------------------

let wheelLockedUntil = 0

function turn(direction: 'next' | 'prev'): void {
  if (!view) return
  if (direction === 'next') void view.next()
  else void view.prev()
}

/** Where a tap landed across the width of the frame: the edges turn the page, the middle belongs to the page that holds us. */
function onTap(x: number, target: EventTarget | null): void {
  if (!view) return
  if (target instanceof Element && target.closest('a[href]')) return
  const scrolled = look?.flow === 'scroll'
  const part = x / Math.max(1, window.innerWidth)
  // A touch ends with a request of the library to settle on the nearest page, one frame later (paginator.js, `touchend`); a tap
  // that turns the page at once is undone by it in Firefox and Safari, where the click comes in the same breath. The turn
  // waits a frame, which puts it behind that.
  const later = (turn: () => Promise<void>) => requestAnimationFrame(() => void turn())
  if (!scrolled && part < 0.25) later(() => view!.goLeft())
  else if (!scrolled && part > 0.75) later(() => view!.goRight())
  else tell({ type: 'tap', zone: 'center' })
}

function onKey(event: KeyboardEvent): void {
  if (!view) return
  const key = event.key
  const plain = !event.ctrlKey && !event.metaKey && !event.altKey
  if (plain && key === 'ArrowLeft') void view.goLeft()
  else if (plain && key === 'ArrowRight') void view.goRight()
  else if (plain && (key === 'PageDown' || (key === ' ' && !event.shiftKey))) turn('next')
  else if (plain && (key === 'PageUp' || (key === ' ' && event.shiftKey))) turn('prev')
  else {
    // The rest belongs to the page that holds us (Escape, the keys for search): the browser's own use of them is kept off.
    tell({ type: 'key', key, shift: event.shiftKey, ctrl: event.ctrlKey, alt: event.altKey, meta: event.metaKey })
    if (key === 'Escape' || key === '/' || ((event.ctrlKey || event.metaKey) && key.toLowerCase() === 'f')) event.preventDefault()
    return
  }
  event.preventDefault()
}

function onWheel(event: WheelEvent): void {
  if (!view || look?.flow === 'scroll' || Math.abs(event.deltaY) < 8 || event.ctrlKey) return
  const now = performance.now()
  if (now < wheelLockedUntil) return
  wheelLockedUntil = now + 450
  turn(event.deltaY > 0 ? 'next' : 'prev')
}

function listen(doc: Document): void {
  doc.addEventListener('click', (event) => {
    if (doc.getSelection()?.toString()) return
    const frameElement = doc.defaultView?.frameElement
    const left = frameElement ? frameElement.getBoundingClientRect().left : 0
    onTap(left + event.clientX, event.target)
  })
  doc.addEventListener('keydown', onKey)
  doc.addEventListener('wheel', onWheel, { passive: true })
}

document.addEventListener('click', (event) => {
  // The margins around the pages belong to the frame itself.
  if (event.target === document.body || (event.target as Element | null)?.localName === 'foliate-view') onTap(event.clientX, event.target)
})
document.addEventListener('keydown', onKey)
document.addEventListener('wheel', onWheel, { passive: true })

// ---- Contents and place ----------------------------------------------------------------------------------------------------

function flatten(nodes: TocNode[], depth = 0, out: (TocNode & { depth: number })[] = []): (TocNode & { depth: number })[] {
  for (const node of nodes) {
    out.push({ ...node, depth })
    if (node.subitems && depth < 8) flatten(node.subitems, depth + 1, out)
  }
  return out
}

function tocLines(): { lines: TocLine[]; marks: number[] } {
  const flat = flatten(toc).slice(0, 5000)
  const lines = flat.map((node, index) => ({ id: node.id ?? index, label: String(node.label ?? '').trim().slice(0, 300), depth: node.depth }))
  const fractions = view?.getSectionFractions() ?? []
  const marks = new Set<number>()
  // The strokes on the bar are the chapters, counted as "Chapter n of m" counts them (not the parts, not the sub-points).
  for (const node of chapters.entries as TocNode[]) {
    const where = node.href ? view?.resolveNavigation(node.href) : undefined
    const at = where ? fractions[where.index] : undefined
    if (typeof at === 'number' && at > 0.001 && at < 0.999) marks.add(Math.round(at * 10000) / 10000)
  }
  return { lines, marks: Array.from(marks).sort((a, b) => a - b).slice(0, 400) }
}

function onRelocate(detail: Relocation): void {
  const item = detail.tocItem
  tell({
    type: 'relocate',
    reason: detail.reason,
    locator: detail.cfi,
    fraction: Math.min(1, Math.max(0, detail.fraction || 0)),
    tocId: typeof item?.id === 'number' ? item.id : -1,
    unit: 'chapter',
    chapter: chapters.numberOf(item),
    chapterCount: chapters.entries.length,
    chapterTitle: String(item?.label ?? '').trim().slice(0, 300),
  })
}

// ---- Search ----------------------------------------------------------------------------------------------------------------------

async function search(query: string): Promise<void> {
  if (!view) return
  const token = ++searchToken
  hitList.length = 0
  hitCursor = -1
  view.clearSearch()
  const text = query.trim().slice(0, MAX_QUERY)
  if (text.length < MIN_QUERY) {
    tell({ type: 'search-done', total: 0, more: false })
    return
  }
  let total = 0
  let more = false
  try {
    const accent = look?.accent ?? '#8e3b46'
    for await (const result of view.search({ query: text, draw: Overlayer.outline, drawOptions: { color: accent, width: 2 } } as never)) {
      if (token !== searchToken) return
      if (result === 'done') break
      if ('progress' in result) {
        tell({ type: 'search-progress', fraction: result.progress })
        continue
      }
      if (total >= MAX_HITS) {
        more = true
        break
      }
      const hits: SearchHit[] = result.subitems.slice(0, MAX_HITS - total).map((item) => ({
        cfi: item.cfi,
        pre: item.excerpt.pre,
        match: item.excerpt.match,
        post: item.excerpt.post,
      }))
      if (hits.length < result.subitems.length) more = true
      total += hits.length
      hitList.push(...hits.map((hit) => hit.cfi))
      tell({ type: 'search-hits', label: String(result.label ?? '').slice(0, 300), hits })
    }
  } catch {
    // A section that cannot be searched ends the search there; what was found stands.
  }
  if (token === searchToken) tell({ type: 'search-done', total, more })
}

// ---- Opening ---------------------------------------------------------------------------------------------------------------------

async function open(message: Extract<ToFrame, { type: 'open' }>): Promise<void> {
  look = message.look
  paint(look)
  // The walls around a page are the browser's. Before a book is opened they are tried (`selftest.ts`): where a script in a page
  // made like a page of a book runs, no book is opened.
  if (await scriptRuns(sandbox)) {
    fail('unsafe')
    return
  }
  try {
    book = message.format === 'epub' ? await openEpub(message.data, origin) : await openMobi(message.data, origin)
    const element = document.createElement('foliate-view') as View
    document.body.append(element)
    view = element
    await view.open(book)
    toc = book.toc ?? []
    chapters = chaptersOf(toc, (href) => (href ? view?.resolveNavigation(href)?.index : undefined))
    rtl = book.dir === 'rtl'
    view.addEventListener('relocate', (event) => onRelocate((event as CustomEvent<Relocation>).detail))
    view.addEventListener('load', (event) => listen((event as CustomEvent<{ doc: Document }>).detail.doc))
    view.addEventListener('external-link', (event) => {
      // The book never opens a window itself (this frame may not); the page opens an address of the web in a tab of its own.
      event.preventDefault()
      const href = String((event as CustomEvent<{ href_?: string }>).detail.href_ ?? '')
      if (openable(href)) tell({ type: 'link', href })
    })
    applyLook()
    const title = book.metadata?.title
    const { lines, marks } = tocLines()
    tell({
      type: 'opened',
      title: (typeof title === 'string' ? title : Object.values(title ?? {})[0] ?? '').slice(0, 300),
      toc: lines,
      marks,
      fixed: view.isFixedLayout,
      rtl,
      pages: 0,
    })
    // A start of the text the book names far back (a real book named its last section) is not followed: see start.ts.
    const showTextStart = followTextStart(view.book as unknown as BookLike)
    if (message.locator) await view.init({ lastLocation: message.locator, showTextStart: false })
    else if (message.fraction !== null && message.fraction > 0) {
      await view.init({ showTextStart })
      await view.goToFraction(message.fraction)
    } else await view.init({ showTextStart })
  } catch (error) {
    fail(error instanceof LimitError ? error.reason : 'broken')
  }
}

window.addEventListener('message', (event: MessageEvent<ToFrame>) => {
  if (!fromParent(event, window.parent)) return
  const message = event.data
  if (!message || typeof message !== 'object') return
  switch (message.type) {
    case 'open':
      if (!view && message.data instanceof ArrayBuffer) void open(message)
      break
    case 'look':
      look = message.look
      applyLook()
      break
    case 'turn':
      turn(message.direction)
      break
    case 'goto':
      if (typeof message.locator === 'string' && message.locator.length <= 1000) void view?.goTo(message.locator)
      break
    case 'goto-fraction':
      if (typeof message.fraction === 'number' && message.fraction >= 0 && message.fraction <= 1) void view?.goToFraction(message.fraction)
      break
    case 'goto-toc': {
      const node = flatten(toc).find((each) => each.id === message.id)
      if (node?.href) void view?.goTo(node.href)
      break
    }
    case 'search':
      void search(String(message.query ?? ''))
      break
    case 'search-step': {
      if (hitList.length === 0) break
      hitCursor = (hitCursor + (message.direction === 'next' ? 1 : -1) + hitList.length) % hitList.length
      void view?.goTo(hitList[hitCursor])
      break
    }
    case 'search-clear':
      searchToken++
      hitList.length = 0
      view?.clearSearch()
      break
  }
})

tell({ type: 'ready' })
