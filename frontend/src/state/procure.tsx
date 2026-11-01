/**
 * Fetching books for the pages: whether the person may ask for books at all, the numbers for the head, and the
 * wishes and downloads that run, shared by the tab "Beschaffen", the spines in the shelf, the cards in Discover and the page
 * of a book. The server is asked every fifteen seconds while the page is in view (and not at all for a person who may not
 * ask for books), and again at once after something was asked for.
 *
 * ``AcquireDialog`` is opened from here (``open``): one dialog for every place that has an "acquire" button.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { procureApi, type ProcureBook, type ProcureCounts, type ReleaseKind, type RunningOut, type WishOut } from '../api/client'
import { AcquireDialog, type AcquireBook } from '../components/procure/AcquireDialog'
import { genreKey } from '../lib/shelf'
import { useAuth } from './auth'

/** How often the numbers and the lists are read again. */
export const POLL_MS = 15_000

/** What is known about one kind of one book: the wish, and the download when one runs. */
export type KindState = { kind: ReleaseKind; wish: WishOut | null; download: RunningOut | null }

type Procure = {
  /** The person may ask for books in some library (the right ``acquire``). */
  allowed: boolean
  counts: ProcureCounts | null
  wishes: WishOut[]
  running: RunningOut[]
  /** Reads everything again (after an action). */
  reload: () => Promise<void>
  /** The wishes and downloads of a book, by its work or its token in Discover; nothing for a book nobody wished. */
  kindsOf: (book: { workId?: number | null; token?: string | null }) => KindState[]
  /** The kinds asked for or on their way, by work: for the spines and covers of the shelf (see ``useIncoming``). */
  byWork: Map<number, KindState[]>
  /** Opens the dialog for a book (or for many: the same kinds, language and library for all). */
  open: (books: AcquireBook[], options?: { kinds?: ReleaseKind[] }) => void
}

const NO_WORKS = new Map<number, KindState[]>()
const IDLE: Procure = { allowed: false, counts: null, wishes: [], running: [], reload: async () => undefined, kindsOf: () => [], byWork: NO_WORKS, open: () => undefined }
const Context = createContext<Procure>(IDLE)

// eslint-disable-next-line react-refresh/only-export-components
export function useProcure(): Procure {
  return useContext(Context)
}

export function ProcureProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { status } = useAuth()
  const [notice, setNotice] = useState<string | null>(null)
  const [counts, setCounts] = useState<ProcureCounts | null>(null)
  const [wishes, setWishes] = useState<WishOut[]>([])
  const [running, setRunning] = useState<RunningOut[]>([])
  const [asked, setAsked] = useState<{ books: AcquireBook[]; kinds?: ReleaseKind[] } | null>(null)
  // A read that is still on its way is not started twice.
  const reading = useRef(false)

  const reload = useCallback(async () => {
    if (reading.current) return
    reading.current = true
    try {
      const numbers = await procureApi.counts()
      setCounts((before) => (JSON.stringify(before) === JSON.stringify(numbers) ? before : numbers))
      if (numbers.allowed) {
        const [seeking, going] = await Promise.all([procureApi.wishes(), procureApi.running()])
        // The lists are replaced only when they say something else, so that a shelf of thousands of spines is not drawn anew
        // every fifteen seconds for nothing.
        setWishes((before) => (JSON.stringify(before) === JSON.stringify(seeking) ? before : seeking))
        setRunning((before) => (JSON.stringify(before) === JSON.stringify(going) ? before : going))
      } else {
        setWishes([])
        setRunning([])
      }
    } catch {
      // What was known stays; the next round asks again.
    } finally {
      reading.current = false
    }
  }, [])

  useEffect(() => {
    if (status !== 'signedIn') {
      setCounts(null)
      setWishes([])
      setRunning([])
      return
    }
    void reload()
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') void reload()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [status, reload])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 8000)
    return () => window.clearTimeout(timer)
  }, [notice])

  const allowed = counts?.allowed === true
  const value = useMemo<Procure>(() => {
    const kindsOf: Procure['kindsOf'] = ({ workId, token }) => {
      const mine = (item: { book: { work_id: number | null; book: string | null } }) =>
        (workId != null && item.book.work_id === workId) || (!!token && item.book.book === token)
      const found: KindState[] = []
      for (const kind of ['ebook', 'audio'] as const) {
        const wish = wishes.find((item) => item.kind === kind && mine(item)) ?? null
        const download = running.find((item) => item.kind === kind && mine(item)) ?? null
        if (wish || download) found.push({ kind, wish, download })
      }
      return found
    }
    const byWork = new Map<number, KindState[]>()
    for (const wish of wishes) if (wish.book.work_id !== null) byWork.set(wish.book.work_id, kindsOf({ workId: wish.book.work_id }))
    for (const item of running) if (item.book.work_id !== null && !byWork.has(item.book.work_id)) byWork.set(item.book.work_id, kindsOf({ workId: item.book.work_id }))
    return { allowed, counts, wishes, running, reload, kindsOf, byWork, open: (books, options) => setAsked({ books, kinds: options?.kinds }) }
  }, [allowed, counts, wishes, running, reload])

  return (
    <Context.Provider value={value}>
      {children}
      {asked && (
        <AcquireDialog
          books={asked.books}
          kinds={asked.kinds}
          onClose={() => setAsked(null)}
          onDone={(done) => {
            setAsked(null)
            setNotice(done.text)
            void reload()
          }}
        />
      )}
      {notice && (
        <div role="status" className="rise fixed inset-x-0 z-40 flex justify-center px-4" style={{ bottom: 'calc(2rem + var(--player-space, 0px))' }} data-testid="acquire-notice">
          <p className="rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-paper shadow-soft">
            {notice}{' '}
            <Link to="/acquire" className="underline underline-offset-2" onClick={() => setNotice(null)}>
              {t('acquire.done.see')}
            </Link>
          </p>
        </div>
      )}
    </Context.Provider>
  )
}

export type IncomingBook = { book: ProcureBook; running: boolean; progress: number }

/** Every book that is asked for and not on a shelf, once, newest first; `running` when a release of it loads. */
// eslint-disable-next-line react-refresh/only-export-components
export function useIncomingBooks(genre: string | null): IncomingBook[] {
  const { allowed, wishes, running } = useProcure()
  if (!allowed) return []
  const found = new Map<string, IncomingBook>()
  for (const wish of wishes) {
    if (wish.book.work_id !== null || !wish.book.book) continue
    if (!found.has(wish.book.book)) found.set(wish.book.book, { book: wish.book, running: false, progress: 0 })
  }
  for (const item of running) {
    if (item.book.work_id !== null || !item.book.book) continue
    const kept = found.get(item.book.book) ?? { book: item.book, running: true, progress: 0 }
    kept.running = true
    kept.progress = Math.max(kept.progress, item.progress ?? 0)
    found.set(item.book.book, kept)
  }
  return [...found.values()].filter((item) => !genre || genreKey(item.book.genre) === genre)
}

/** How the shelf marks a book that lacks a kind somebody asked for (the band at the foot of the spine, the mark on the
 * cover): ``run`` while a release of it loads, ``find`` while it is looked for. Nothing for a book nobody asks anything of,
 * and nothing for an upgrade of a kind the book has. */
// eslint-disable-next-line react-refresh/only-export-components
export function useIncoming(): (work: { id: number; editions: { kind: string }[] }) => { state: 'find' | 'run'; kind: ReleaseKind } | null {
  const { allowed, byWork } = useProcure()
  return useCallback(
    (work) => {
      if (!allowed) return null
      const asked = byWork.get(work.id)?.filter((item) => !work.editions.some((edition) => edition.kind === item.kind))
      if (!asked || asked.length === 0) return null
      const loading = asked.find((item) => item.download)
      return { state: loading ? 'run' : 'find', kind: (loading ?? asked[0]).kind }
    },
    [allowed, byWork],
  )
}
