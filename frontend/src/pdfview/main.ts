/**
 * The frame that draws a PDF: pdf.js turns the pages here, with their text (to select, copy and find). Like the frame of
 * a book (`../readview/main.ts`) it has no origin of its own and no network at all (its policy says `connect-src 'none'`):
 * the PDF and every file pdf.js asks for come from the page by message (`../lib/readFrame.ts`), and a PDF can carry code
 * (fonts, forms) that stays in here, away from the session and the API. The frame speaks the same language as the frame of
 * a book, so the page that holds it needs one controller for both. Messages count only from the page that holds the frame;
 * links of the PDF go to the page, which opens web addresses in a tab of its own.
 */
import 'pdfjs-dist/web/pdf_viewer.css'
import './pdfview.css'

import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import workerSource from 'pdfjs-dist/build/pdf.worker.min.mjs?raw'
import { EventBus, PDFFindController, PDFLinkService, PDFViewer } from 'pdfjs-dist/web/pdf_viewer.mjs'

import {
  DATA_FOLDERS, fromParent, openable, PAGE_COLOURS, plainName,
  type DataKind, type FromFrame, type ReaderLook, type TocLine, type ToFrame,
} from '../lib/readFrame'
import { classicWorker } from '../lib/pdfWorker'

const box = document.getElementById('box') as HTMLDivElement
const viewerDiv = document.getElementById('viewer') as HTMLDivElement

function tell(message: FromFrame): void {
  // The page's origin is not known here (this frame has none); what is said is only about the PDF on show.
  window.parent.postMessage(message, '*')
}

const workerUrl = URL.createObjectURL(new Blob([classicWorker(workerSource)], { type: 'text/javascript' }))
pdfjs.GlobalWorkerOptions.workerPort = new Worker(workerUrl)

// Files pdf.js asks for (character maps, the 14 standard fonts, decoders) come from the page, by name only.
let nextAsk = 1
const waiting = new Map<number, (bytes: ArrayBuffer | null) => void>()

class FromPage {
  constructor(_options: unknown) {}

  async fetch({ kind, filename }: { kind: DataKind; filename: string }): Promise<Uint8Array> {
    if (!(kind in DATA_FOLDERS) || !plainName(filename)) throw new Error('not asked for')
    const id = nextAsk++
    const bytes = await new Promise<ArrayBuffer | null>((resolve) => {
      waiting.set(id, resolve)
      tell({ type: 'data', id, kind, name: filename })
    })
    if (!bytes) throw new Error('not there')
    return new Uint8Array(bytes)
  }
}

// pdf.js would fetch its words from the server; the frame shows almost none of its own, and fetches nothing.
const quiet = {
  getLanguage: () => document.documentElement.lang || 'en',
  getDirection: () => 'ltr',
  get: async (_ids: unknown, _args: unknown, fallback?: string) => fallback ?? '',
  translate: async () => {},
  translateOnce: async () => {},
  pause: () => {},
  resume: () => {},
  destroy: async () => {},
}

const eventBus = new EventBus()
const linkService = new PDFLinkService({ eventBus })
const findController = new PDFFindController({ eventBus, linkService })
const viewer = new PDFViewer({
  container: box,
  viewer: viewerDiv,
  eventBus,
  linkService,
  findController,
  // Forms are shown as they are filled, never filled in here; nothing the PDF says runs as a script.
  annotationMode: pdfjs.AnnotationMode.ENABLE,
  l10n: quiet as never,
  removePageBorders: false,
})
linkService.setViewer(viewer)

const SCROLL_VERTICAL = 0
const SCROLL_PAGE = 3

let look: ReaderLook | null = null
let documentShown: PDFDocumentProxy | null = null
let firstPage = 1
let outline: { id: number; label: string; depth: number; dest: unknown; page: number }[] = []
let searchOn = false
let lastQuery = ''

/** The scale that fits the page (one page at a time) or its width (a column of pages), times the size the person chose. */
function fit(): void {
  if (!look) return
  const paged = look.flow === 'paged'
  viewer.currentScaleValue = paged ? 'page-fit' : 'page-width'
  if (look.size !== 100) viewer.currentScale = viewer.currentScale * (look.size / 100)
}

function paint(next: ReaderLook): void {
  const colours = PAGE_COLOURS[next.theme]
  const root = document.documentElement.style
  root.setProperty('--ground', colours.ground)
  root.setProperty('--ink', colours.ink)
  root.setProperty('color-scheme', next.theme === 'dark' ? 'dark' : 'light')
  document.body.classList.toggle('dark', next.theme === 'dark')
  document.body.classList.toggle('sepia', next.theme === 'sepia')
}

function applyLook(): void {
  if (!look) return
  paint(look)
  const mode = look.flow === 'paged' ? SCROLL_PAGE : SCROLL_VERTICAL
  if (viewer.scrollMode !== mode) viewer.scrollMode = mode
  fit()
}

function place(): void {
  const pages = viewer.pagesCount
  const page = viewer.currentPageNumber
  let current = -1
  for (const [index, item] of outline.entries()) if (item.page <= page) current = index
  tell({
    type: 'relocate',
    reason: 'page',
    locator: `page:${page}`,
    fraction: pages > 1 ? (page - 1) / (pages - 1) : 0,
    tocId: current,
    unit: 'page',
    chapter: page,
    chapterCount: pages,
    chapterTitle: current >= 0 ? outline[current].label : '',
  })
}

eventBus.on('pagesinit', () => {
  applyLook()
  if (firstPage > 1) viewer.currentPageNumber = Math.min(firstPage, viewer.pagesCount)
  place()
})
eventBus.on('pagechanging', () => place())
eventBus.on('scalechanging', () => undefined)
const found = (event: { matchesCount?: { current: number; total: number } }) => {
  if (!searchOn) return
  const count = event.matchesCount ?? { current: 0, total: 0 }
  tell({ type: 'search-count', current: count.current, total: count.total })
}
eventBus.on('updatefindmatchescount', found)
eventBus.on('updatefindcontrolstate', found)

/** A tap on a page (not on a link, selecting nothing): the edges turn the page, the middle is the page's. */
box.addEventListener('click', (event) => {
  const link = (event.target as HTMLElement).closest('a')
  if (link) {
    const href = link.getAttribute('href') ?? ''
    if (href && !href.startsWith('#')) {
      // A link of the PDF to the web: the frame opens nothing; the page decides.
      event.preventDefault()
      event.stopPropagation()
      if (openable(link.href)) tell({ type: 'link', href: link.href })
    }
    return
  }
  if (window.getSelection()?.toString()) return
  const part = event.clientX / Math.max(1, window.innerWidth)
  if (look?.flow === 'paged' && part < 0.25) turn('prev')
  else if (look?.flow === 'paged' && part > 0.75) turn('next')
  else tell({ type: 'tap', zone: 'center' })
}, true)

function turn(direction: 'next' | 'prev'): void {
  if (look?.flow === 'paged') {
    if (direction === 'next') viewer.nextPage()
    else viewer.previousPage()
  } else {
    box.scrollBy({ top: (direction === 'next' ? 1 : -1) * box.clientHeight * 0.9, behavior: look?.animate ? 'smooth' : 'auto' })
  }
}

window.addEventListener('keydown', (event) => {
  const plain = !event.ctrlKey && !event.metaKey && !event.altKey
  const key = event.key
  if (plain && (key === 'ArrowRight' || key === 'PageDown' || (key === ' ' && !event.shiftKey))) turn('next')
  else if (plain && (key === 'ArrowLeft' || key === 'PageUp' || (key === ' ' && event.shiftKey))) turn('prev')
  else {
    tell({ type: 'key', key, shift: event.shiftKey, ctrl: event.ctrlKey, alt: event.altKey, meta: event.metaKey })
    if (key === 'Escape' || key === '/' || ((event.ctrlKey || event.metaKey) && key.toLowerCase() === 'f')) event.preventDefault()
    return
  }
  event.preventDefault()
})

// ---- The contents ------------------------------------------------------------------------------------------------------------

type OutlineItem = { title: string; dest: unknown; items: OutlineItem[] }

async function buildOutline(pdf: PDFDocumentProxy): Promise<void> {
  const raw = (await pdf.getOutline()) as OutlineItem[] | null
  outline = []
  if (!raw) return
  const walk = async (items: OutlineItem[], depth: number) => {
    for (const item of items) {
      if (outline.length >= 500) return
      let page = 1
      try {
        const dest = typeof item.dest === 'string' ? await pdf.getDestination(item.dest) : (item.dest as unknown[] | null)
        const ref = dest?.[0]
        if (ref && typeof ref === 'object') page = (await pdf.getPageIndex(ref as never)) + 1
        else if (typeof ref === 'number') page = ref + 1
      } catch {
        // A destination that cannot be followed: the entry stays and leads to the first page.
      }
      outline.push({ id: outline.length, label: String(item.title ?? '').trim().slice(0, 300), depth, dest: item.dest, page })
      if (depth < 4) await walk(item.items ?? [], depth + 1)
    }
  }
  await walk(raw, 0)
}

// ---- What the page says ------------------------------------------------------------------------------------------------------

async function open(message: Extract<ToFrame, { type: 'open' }>): Promise<void> {
  look = message.look
  paint(look)
  if (message.locator?.startsWith('page:')) firstPage = Math.max(1, parseInt(message.locator.slice(5), 10) || 1)
  try {
    const task = pdfjs.getDocument({
      data: new Uint8Array(message.data),
      isEvalSupported: false,
      enableXfa: false,
      // Not the system's fonts: pdf.js would name the standard fonts by an address (`page:/...`) in a FontFace, which the
      // frame's policy refuses; without them it asks for their bytes like any other file (FromPage).
      useSystemFonts: false,
      useWorkerFetch: false,
      // Only a name to hand to FromPage; pdf.js wants them to end in a slash.
      cMapUrl: 'page:/', cMapPacked: true, standardFontDataUrl: 'page:/', wasmUrl: 'page:/',
      BinaryDataFactory: FromPage,
    } as never)
    task.onPassword = () => {
      void task.destroy()
      fail('password')
    }
    const pdf = await task.promise
    documentShown = pdf
    await buildOutline(pdf)
    const toc: TocLine[] = outline.map((item) => ({ id: item.id, label: item.label, depth: item.depth }))
    const marks = Array.from(new Set(outline.map((item) => (pdf.numPages > 1 ? Math.round(((item.page - 1) / (pdf.numPages - 1)) * 10000) / 10000 : 0))))
      .filter((at) => at > 0.001 && at < 0.999).sort((a, b) => a - b)
    const info = await pdf.getMetadata().catch(() => null)
    const title = String((info?.info as { Title?: string } | undefined)?.Title ?? '').slice(0, 300)
    tell({ type: 'opened', title, toc, marks, fixed: true, rtl: false, pages: pdf.numPages })
    viewer.setDocument(pdf)
    linkService.setDocument(pdf, null)
  } catch {
    fail('broken')
  }
}

function fail(reason: Extract<FromFrame, { type: 'failed' }>['reason']): void {
  tell({ type: 'failed', reason })
}

window.addEventListener('message', (event: MessageEvent<ToFrame>) => {
  if (!fromParent(event, window.parent)) return
  const message = event.data
  if (!message || typeof message !== 'object') return
  switch (message.type) {
    case 'open':
      if (!documentShown && message.data instanceof ArrayBuffer) void open(message)
      break
    case 'look':
      look = message.look
      applyLook()
      break
    case 'turn':
      turn(message.direction)
      break
    case 'goto': {
      const page = message.locator.startsWith('page:') ? parseInt(message.locator.slice(5), 10) : NaN
      if (Number.isInteger(page) && page >= 1) viewer.currentPageNumber = Math.min(page, viewer.pagesCount)
      break
    }
    case 'goto-fraction':
      if (viewer.pagesCount > 0 && message.fraction >= 0 && message.fraction <= 1) {
        viewer.currentPageNumber = Math.min(viewer.pagesCount, Math.max(1, Math.round(message.fraction * (viewer.pagesCount - 1)) + 1))
      }
      break
    case 'goto-toc': {
      const item = outline.find((each) => each.id === message.id)
      if (item) viewer.currentPageNumber = Math.min(Math.max(1, item.page), viewer.pagesCount)
      break
    }
    case 'search':
      searchOn = true
      lastQuery = String(message.query).slice(0, 200)
      eventBus.dispatch('find', {
        source: null, type: '', query: lastQuery,
        caseSensitive: false, entireWord: false, highlightAll: true, findPrevious: false, matchDiacritics: false,
      })
      break
    case 'search-step':
      eventBus.dispatch('find', {
        source: null, type: 'again', query: lastQuery,
        caseSensitive: false, entireWord: false, highlightAll: true, findPrevious: message.direction === 'prev', matchDiacritics: false,
      })
      break
    case 'search-clear':
      searchOn = false
      eventBus.dispatch('findbarclose', { source: null })
      break
    case 'data': {
      const resolve = waiting.get(message.id)
      if (resolve) {
        waiting.delete(message.id)
        resolve(message.bytes instanceof ArrayBuffer ? message.bytes : null)
      }
      break
    }
  }
})

tell({ type: 'ready' })
