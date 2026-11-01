/**
 * The frame that draws a book, from the page's side: fetches the file with the person's session, hands the bytes to the frame
 * once it says it is ready, and turns what the frame says (where the reader is, the contents, what a search found) into state.
 * One controller for both frames (`/readview.html` for EPUB, MOBI and AZW3, `/pdfview.html` for PDF): they speak one language
 * (`lib/readFrame.ts`). The frame fetches nothing itself; messages count only from the frame this page holds.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { contentUrl } from '../../api/client'
import {
  addressOf, DATA_FOLDERS, frameFor, MAX_BOOK_BYTES, plainName,
  type BookFormat, type FailReason, type FromFrame, type Place, type ReaderLook, type SearchHit, type TocLine, type ToFrame,
} from '../../lib/readFrame'
import type { Start } from '../../lib/readerProgress'

/** `url`: where the bytes come from when it is not the usual file of a library (a file of a download that asks a question). */
export type FrameFile = { id: number; size: number; format: BookFormat; url?: string }
export type Failure = FailReason | 'network' | 'notFound'

export type Found = { label: string; hits: SearchHit[] }

export type FrameState = {
  phase: 'loading' | 'open' | 'failed'
  reason: Failure | null
  title: string
  toc: TocLine[]
  marks: number[]
  fixed: boolean
  rtl: boolean
  pages: number
  place: (Place & { reason?: string }) | null
  found: Found[]
  searching: boolean
  searchProgress: number
  searchDone: { total: number; more: boolean } | null
  count: { current: number; total: number } | null
}

const FIRST: FrameState = {
  phase: 'loading', reason: null, title: '', toc: [], marks: [], fixed: false, rtl: false, pages: 0, place: null, found: [],
  searching: false, searchProgress: 0, searchDone: null, count: null,
}

type Handlers = {
  /** The reader moved (the place is new). */
  onPlace?: (place: Place & { reason?: string }) => void
  onKey?: (key: Extract<FromFrame, { type: 'key' }>) => void
  /** A tap in the middle of the page. */
  onTap?: () => void
  /** A page of the book was changed to be shown (too deep, too big). */
  onNotice?: (kind: 'flattened' | 'cut' | 'missing') => void
  /** A click on a link of the book that leads to the web: nothing is opened until the person has seen where it leads. */
  onLink?: (href: string) => void
}

/** The sandbox of the frame: a PDF frame has no origin; the frame of a book has this page's origin (the reader reaches into the
 * pages of the book, which a browser allows only inside one origin) and what keeps a book from running scripts is in the frame. */
export function sandboxFor(format: BookFormat): string {
  return format === 'pdf' ? 'allow-scripts' : 'allow-same-origin allow-scripts'
}

export function useBookFrame(file: FrameFile, start: Start, look: ReaderLook, handlers: Handlers) {
  const frame = useRef<HTMLIFrameElement>(null)
  const bytes = useRef<Promise<ArrayBuffer> | null>(null)
  const [state, setState] = useState<FrameState>(FIRST)
  const [attempt, setAttempt] = useState(0)
  const callbacks = useRef(handlers)
  callbacks.current = handlers
  // What the frame is told on opening: where, and how. Read once, when it says it is ready.
  const opening = useRef({ start, look })
  opening.current = { start, look }
  const sent = useRef(false)

  const say = useCallback((message: ToFrame, transfer: Transferable[] = []) => {
    frame.current?.contentWindow?.postMessage(message, '*', transfer)
  }, [])

  // The file: fetched with the session; a refusal or a lost connection is told as a failure of the page.
  useEffect(() => {
    sent.current = false
    setState(FIRST)
    const task = (async () => {
      // The size is known from the list of files: a book that is too large is not fetched at all.
      if (file.size > MAX_BOOK_BYTES) throw new Error('too_large')
      const control = new AbortController()
      let response: Response
      try {
        response = await fetch(file.url ?? contentUrl(file.id), { credentials: 'same-origin', signal: control.signal })
      } catch {
        throw new Error('network')
      }
      if (response.status === 404) throw new Error('notFound')
      if (!response.ok) throw new Error('network')
      const length = Number(response.headers.get('content-length') ?? file.size)
      if (length > MAX_BOOK_BYTES) {
        // What the server says now is more than the list said: stop the download.
        control.abort()
        throw new Error('too_large')
      }
      return response.arrayBuffer()
    })()
    bytes.current = task
    task.catch((error: Error) => {
      const reason = ['notFound', 'too_large'].includes(error.message) ? (error.message as Failure) : 'network'
      setState((current) => ({ ...current, phase: 'failed', reason }))
    })
  }, [file.id, file.size, file.url, attempt])

  useEffect(() => {
    const listen = (event: MessageEvent<FromFrame>) => {
      if (!frame.current || event.source !== frame.current.contentWindow) return
      const message = event.data
      if (!message || typeof message !== 'object') return
      switch (message.type) {
        case 'ready':
          if (sent.current) break
          void bytes.current?.then(
            (data) => {
              if (sent.current) return
              sent.current = true
              // The bytes themselves go over (they are handed on, not copied), and this page lets go of them: a book is held once.
              say({ type: 'open', data, format: file.format, look: opening.current.look, locator: opening.current.start.locator, fraction: opening.current.start.fraction }, [data])
              bytes.current = null
            },
            () => undefined,
          )
          break
        case 'opened':
          setState((current) => ({ ...current, phase: 'open', title: message.title, toc: message.toc, marks: message.marks, fixed: message.fixed, rtl: message.rtl, pages: message.pages }))
          break
        case 'relocate': {
          const { type: _type, ...place } = message
          setState((current) => ({ ...current, phase: 'open', place }))
          callbacks.current.onPlace?.(place)
          break
        }
        case 'tap':
          callbacks.current.onTap?.()
          break
        case 'key':
          callbacks.current.onKey?.(message)
          break
        case 'link':
          {
            const address = addressOf(message.href)
            if (address) callbacks.current.onLink?.(address)
          }
          break
        case 'search-hits':
          setState((current) => ({ ...current, found: [...current.found, { label: message.label, hits: message.hits }] }))
          break
        case 'search-progress':
          setState((current) => ({ ...current, searchProgress: message.fraction }))
          break
        case 'search-done':
          setState((current) => ({ ...current, searching: false, searchDone: { total: message.total, more: message.more } }))
          break
        case 'search-count':
          setState((current) => ({ ...current, searching: false, count: { current: message.current, total: message.total } }))
          break
        case 'failed':
          setState((current) => ({ ...current, phase: 'failed', reason: message.reason }))
          break
        case 'notice':
          if (message.kind === 'flattened' || message.kind === 'cut' || message.kind === 'missing') callbacks.current.onNotice?.(message.kind)
          break
        case 'data': {
          // pdf.js asks for character maps, fonts and decoders by name; the page hands them over from its own files.
          const folder = DATA_FOLDERS[message.kind]
          if (!folder || !plainName(message.name)) {
            say({ type: 'data', id: message.id, bytes: null })
            break
          }
          fetch(`/pdfjs/${folder}/${message.name}`).then(
            async (response) => {
              const data = response.ok ? await response.arrayBuffer() : null
              say({ type: 'data', id: message.id, bytes: data }, data ? [data] : [])
            },
            () => say({ type: 'data', id: message.id, bytes: null }),
          )
          break
        }
      }
    }
    window.addEventListener('message', listen)
    return () => window.removeEventListener('message', listen)
  }, [say, file.format])

  // The look changed (the person, or the app's colours): the frame follows.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    if (state.phase === 'open') say({ type: 'look', look })
    // Only a change of the look; opening hands it over itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [look])

  const actions = {
    turn: (direction: 'next' | 'prev') => say({ type: 'turn', direction }),
    goto: (locator: string) => say({ type: 'goto', locator }),
    gotoFraction: (fraction: number) => say({ type: 'goto-fraction', fraction }),
    gotoToc: (id: number) => say({ type: 'goto-toc', id }),
    search: (query: string) => {
      setState((current) => ({ ...current, found: [], searching: query.trim().length >= 2, searchProgress: 0, searchDone: null, count: null }))
      say({ type: 'search', query })
    },
    searchStep: (direction: 'next' | 'prev') => say({ type: 'search-step', direction }),
    searchClear: () => {
      setState((current) => ({ ...current, found: [], searching: false, searchProgress: 0, searchDone: null, count: null }))
      say({ type: 'search-clear' })
    },
    retry: () => setAttempt((count) => count + 1),
  }

  return { frameRef: frame, state, actions, src: frameFor(file.format), sandbox: sandboxFor(file.format), attempt }
}
