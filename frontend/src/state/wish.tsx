/**
 * The wish list of the person: what the page of Discover, the page of a book and the count on the tab share. The list is the
 * person's own (the server answers only for the caller). Pressing "Merken" puts a book on it at once and says so to the
 * server; if the server refuses (the list is full, the connection broke) the card goes back to what it was and the reason
 * is said. Taking a book off the list asks nothing, but is shown for a few seconds with "Rückgängig": nothing is lost by
 * a slip of the finger.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, wishApi, type DiscoverCard } from '../api/client'
import { errorText } from '../lib/errors'
import { useAuth } from './auth'

/** How long "Rückgängig" stands. */
export const UNDO_MS = 6000

type Wish = {
  items: DiscoverCard[]
  count: number
  /** The books a watch of an author put on the list: the token of the book to the author's name, and why it is not wished. */
  via: Record<string, string>
  viaWhy: Record<string, string>
  /** Whether the card is on the list now (the answer of the server, or the press that is on its way). */
  wished: (card: Pick<DiscoverCard, 'token' | 'wished'>) => boolean
  /** Puts the book on the list, or takes it off. Resolves when the server has answered. */
  toggle: (card: DiscoverCard) => Promise<void>
  /** The sentence about a refusal; null when all is well. */
  problem: string | null
  /** Reads the list again (after the pages that change it elsewhere). */
  reload: () => Promise<void>
}

const Context = createContext<Wish | null>(null)

export function WishProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { status } = useAuth()
  const [items, setItems] = useState<DiscoverCard[]>([])
  const [count, setCount] = useState(0)
  const [via, setVia] = useState<Record<string, string>>({})
  const [viaWhy, setViaWhy] = useState<Record<string, string>>({})
  // What a press decided, by the token of the card: wins over the `wished` the card came with until the page reads again.
  const [decided, setDecided] = useState<Record<string, boolean>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [undo, setUndo] = useState<DiscoverCard | null>(null)
  // A press on a card is handled once until the server answered (a double click is one press).
  const busy = useRef(new Set<string>())

  const apply = useCallback((list: { count: number; items: DiscoverCard[]; via?: Record<string, string>; via_why?: Record<string, string> }) => {
    setItems(list.items)
    setCount(list.count)
    setVia(list.via ?? {})
    setViaWhy(list.via_why ?? {})
  }, [])

  const reload = useCallback(async () => {
    try {
      apply(await wishApi.list())
    } catch {
      // The count stays as it was; the next page that asks tries again.
    }
  }, [apply])

  useEffect(() => {
    if (status === 'signedIn') void reload()
    else {
      setItems([])
      setCount(0)
      setVia({})
      setViaWhy({})
      setDecided({})
    }
  }, [status, reload])

  useEffect(() => {
    if (!undo) return
    const timer = window.setTimeout(() => setUndo(null), UNDO_MS)
    return () => window.clearTimeout(timer)
  }, [undo])

  const wished = useCallback((card: Pick<DiscoverCard, 'token' | 'wished'>) => decided[card.token] ?? card.wished, [decided])

  const toggle = useCallback(
    async (card: DiscoverCard) => {
      if (busy.current.has(card.token)) return
      busy.current.add(card.token)
      const was = decided[card.token] ?? card.wished
      setDecided((now) => ({ ...now, [card.token]: !was }))
      setProblem(null)
      try {
        apply(was ? await wishApi.remove(card.token) : await wishApi.add(card.token))
        setUndo(was ? card : null)
      } catch (error) {
        setDecided((now) => ({ ...now, [card.token]: was }))
        setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}))
      } finally {
        busy.current.delete(card.token)
      }
    },
    [apply, decided],
  )

  const value = useMemo<Wish>(() => ({ items, count, via, viaWhy, wished, toggle, problem, reload }), [items, count, via, viaWhy, wished, toggle, problem, reload])
  return (
    <Context.Provider value={value}>
      {children}
      {undo && (
        <div className="dundo" data-testid="wish-undo">
          <p role="status">
            {t('discover.wish.removed', { title: undo.title })}
            <button
              type="button"
              onClick={() => {
                const back = undo
                setUndo(null)
                void toggle({ ...back, wished: false })
              }}
            >
              {t('discover.wish.undo')}
            </button>
          </p>
        </div>
      )}
    </Context.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWish(): Wish {
  const found = useContext(Context)
  if (!found) throw new Error('useWish outside a WishProvider')
  return found
}
