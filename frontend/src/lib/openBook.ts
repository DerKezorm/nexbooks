/**
 * Opening the page of a book from a shelf, a grid or a list, and closing it again.
 *
 * Two things go wrong when this is left to each caller. A double click (or two quick presses on two spines) would push
 * the book twice, and the way back would then land on the same book again, under a layer nobody can see. And a close that
 * blindly goes "one step back" lands somewhere else whenever the entry before is not the page the book was opened from.
 * So: a page opens at most one book at a time (the lock ends when the layer of the book is gone), and the book remembers
 * the position in the history of the entry it came from, so that closing goes back only when that entry is the one before.
 */
import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate, type Location } from 'react-router-dom'

import type { DiscoverCard, WorkBrief } from '../api/client'

/** What the page of a book is opened with: the page under it, the book as the shelf knows it (so the page can start at once),
 * the position in the history of the entry it was opened from, and a sentence to say that the page it replaced wanted said. */
export type BookRoute = { background?: Location; work?: WorkBrief; card?: DiscoverCard; said?: string; from?: number | null } | null

/** The position of the current entry in the history of the browser (the router keeps it there), or null if it is not known. */
export function historyIndex(): number | null {
  try {
    const state = window.history.state as { idx?: unknown } | null
    return typeof state?.idx === 'number' ? state.idx : null
  } catch {
    return null
  }
}

/** Whether the entry before the current one is the one the book was opened from (so that "back" leads there). */
export function cameStraightFrom(route: BookRoute): boolean {
  const here = historyIndex()
  return Boolean(route?.background) && typeof route?.from === 'number' && here !== null && here === route.from + 1
}

// How many pages of a book stand now. A page that opened a book is locked until none stands any more.
let standing = 0
const listeners = new Set<() => void>()

/** The page of a book calls this while it is there. */
export function bookLayerStands(): () => void {
  standing += 1
  listeners.forEach((listener) => listener())
  return () => {
    standing -= 1
    listeners.forEach((listener) => listener())
  }
}

/** For a page that shows books: `open(path, state)` opens the page of a book over this page, once. A second press before the
 * page of the book is gone again does nothing. */
export function useOpenAt(): (path: string, over: Pick<NonNullable<BookRoute>, 'work' | 'card'>) => void {
  const location = useLocation()
  const navigate = useNavigate()
  const locked = useRef(false)
  useEffect(() => {
    const release = () => {
      // The page of a book replaced by that of another (a volume of the series) is gone and there again in one step.
      queueMicrotask(() => {
        if (standing === 0) locked.current = false
      })
    }
    listeners.add(release)
    return () => void listeners.delete(release)
  }, [])
  return useCallback(
    (path: string, over: Pick<NonNullable<BookRoute>, 'work' | 'card'>) => {
      if (locked.current) return
      locked.current = true
      const state: BookRoute = { background: { ...location, state: null }, ...over, from: historyIndex() }
      navigate(path, { state })
    },
    [navigate, location],
  )
}

/** For a page that shows books of a library: `open(id, work)` opens the book over this page, once. */
export function useOpenBook(): (id: number, work?: WorkBrief) => void {
  const at = useOpenAt()
  return useCallback((id: number, work?: WorkBrief) => at(`/work/${id}`, { work }), [at])
}

/** For a page that shows books of the sources (Discover): a book that is on the person's shelf opens as the book of the
 * shelf, any other as the page of a book that is not (`ForeignPage`). */
export function useOpenCard(): (card: DiscoverCard) => void {
  const at = useOpenAt()
  return useCallback((card: DiscoverCard) => at(card.owned_work_id !== null ? `/work/${card.owned_work_id}` : `/discover/book/${encodeURIComponent(card.token)}`, card.owned_work_id !== null ? {} : { card }), [at])
}

/** Where closing the page of a book leads: back to the entry it came from if that is the one before, else to the page it was
 * opened from (replacing the book, so that the history does not keep it), else to the library. */
export function leaveBook(route: BookRoute, navigate: ReturnType<typeof useNavigate>): void {
  if (cameStraightFrom(route)) {
    navigate(-1)
    return
  }
  const background = route?.background
  navigate(background ? { pathname: background.pathname, search: background.search, hash: background.hash } : '/', { replace: true })
}
