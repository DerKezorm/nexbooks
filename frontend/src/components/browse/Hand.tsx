/**
 * The cover taken in the hand: the mouse rests on a cover for 0.3 seconds
 * and the cover lifts while its back appears beside it, in the colours of the book (the words on the back, the length, the
 * forms it is known in, "Beschaffen", "Merken", "Ansehen"). A click opens the page of the book. On a screen without a mouse
 * the first tap shows the back as a sheet from below, and "Ansehen" opens the page.
 *
 * One hand at a time for the whole page: `HandProvider` holds it, a card asks it for the handlers of its mouse.
 */
import { Award, Download, X } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { catalogApi, discoverApi, type ShelfCard } from '../../api/client'
import { authorHref } from '../../lib/catalog'
import { languageName } from '../../lib/books'
import { coverOf, dayText } from '../../lib/discover'
import { focusables, trapTab } from '../../lib/focusTrap'
import { HOLD_MS, HOLD_NEXT_MS, LET_GO_MS, minutesText, touchOnly } from '../../lib/browse'
import { acquireBookOfCard } from '../../lib/procure'
import { useProcure } from '../../state/procure'
import { useWish } from '../../state/wish'
import { AudioIcon, BookIcon, Cover } from '../BookCard'
import { CheckIcon, MarkIcon } from '../icons'

type Held = { card: ShelfCard; box: DOMRect | null }

type Api = {
  /** The handlers of the mouse for a card: rest on it to take it in the hand, click to open it, tap to see its back. */
  bind: (card: ShelfCard) => {
    onMouseEnter: (event: MouseEvent<HTMLElement>) => void
    onMouseLeave: () => void
    onClick: (event: MouseEvent<HTMLElement>) => void
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => void
    onKeyUp: (event: KeyboardEvent<HTMLElement>) => void
  }
  /** The token of the card in the hand, for the card to stand lifted. */
  heldToken: string | null
  hide: () => void
}

const Context = createContext<Api | null>(null)

// eslint-disable-next-line react-refresh/only-export-components
export function useHand(): Api {
  const api = useContext(Context)
  if (!api) throw new Error('useHand is used inside a HandProvider')
  return api
}

/** What a page of a book says that a card does not: the words on the back and the pages. Kept for the visit, for a card that is
 * taken in the hand twice. */
const learned = new Map<string, { description: string; pages: number | null }>()

function useBackText(card: ShelfCard): {
  text: string
  pages: number | null
  waiting: boolean
} {
  const [extra, setExtra] = useState(learned.get(card.token) ?? null)
  const [waiting, setWaiting] = useState(false)
  useEffect(() => {
    if (card.blurb) return
    const kept = learned.get(card.token)
    if (kept) {
      setExtra(kept)
      return
    }
    let alive = true
    let timer = 0
    let tries = 0
    const run = () => {
      setWaiting(true)
      discoverApi.book(card.token).then(
        (page) => {
          if (!alive) return
          const pages = (page.forms_known ?? []).find((form) => form.pages)?.pages ?? null
          if (page.description || !page.pending || tries >= 3) {
            const found = { description: page.description, pages }
            learned.set(card.token, found)
            setExtra(found)
            setWaiting(false)
          } else {
            tries += 1
            timer = window.setTimeout(run, 3500)
          }
        },
        () => alive && setWaiting(false),
      )
    }
    run()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [card.token, card.blurb])
  return {
    text: card.blurb || extra?.description || '',
    pages: card.pages ?? extra?.pages ?? null,
    waiting,
  }
}

export function HandProvider({ children, onOpen }: { children: ReactNode; onOpen: (card: ShelfCard) => void }) {
  const [held, setHeld] = useState<(Held & { sheet: boolean }) | null>(null)
  const heldRef = useRef(held)
  heldRef.current = held
  const timers = useRef({ show: 0, hide: 0 })
  const open = useRef(onOpen)
  open.current = onOpen

  const hide = useCallback(() => {
    window.clearTimeout(timers.current.show)
    window.clearTimeout(timers.current.hide)
    setHeld(null)
  }, [])

  const bind = useCallback<Api['bind']>(
    (card) => ({
      onMouseEnter: (event) => {
        if (touchOnly()) return
        const element = event.currentTarget
        window.clearTimeout(timers.current.hide)
        window.clearTimeout(timers.current.show)
        timers.current.show = window.setTimeout(
          () => {
            if (!element.isConnected) return
            const cover = element.querySelector('.bcv')?.getBoundingClientRect() ?? element.getBoundingClientRect()
            setHeld({ card, box: cover, sheet: false })
          },
          heldRef.current ? HOLD_NEXT_MS : HOLD_MS,
        )
      },
      onMouseLeave: () => {
        window.clearTimeout(timers.current.show)
        window.clearTimeout(timers.current.hide)
        if (heldRef.current) timers.current.hide = window.setTimeout(hide, LET_GO_MS)
      },
      onClick: () => {
        if (touchOnly()) {
          setHeld({ card, box: null, sheet: true })
          return
        }
        hide()
        open.current(card)
      },
      // With the keyboard: the space bar turns the cover over (its back as a sheet, like a tap), Enter opens the page.
      onKeyDown: (event) => {
        if (event.key !== ' ') return
        event.preventDefault()
        window.clearTimeout(timers.current.show)
        window.clearTimeout(timers.current.hide)
        setHeld({ card, box: null, sheet: true })
      },
      onKeyUp: (event) => {
        // The button would be clicked when the space bar comes up: the page of the book is Enter's.
        if (event.key === ' ') event.preventDefault()
      },
    }),
    [hide],
  )

  // Out of the hand: a scroll anywhere (the strip under it moves), Escape.
  useEffect(() => {
    if (!held) return
    const away = () => {
      if (!heldRef.current?.sheet) hide()
    }
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') hide()
    }
    window.addEventListener('scroll', away, { passive: true, capture: true })
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('scroll', away, true)
      window.removeEventListener('keydown', key, true)
    }
  }, [held, hide])

  useEffect(() => () => hide(), [hide])

  const api = useMemo<Api>(
    () => ({
      bind,
      heldToken: held && !held.sheet ? held.card.token : null,
      hide,
    }),
    [bind, hide, held],
  )
  return (
    <Context.Provider value={api}>
      {children}
      {held &&
        createPortal(
          <Back
            held={held}
            sheet={held.sheet}
            onLetGo={() => {
              window.clearTimeout(timers.current.hide)
              timers.current.hide = window.setTimeout(hide, LET_GO_MS)
            }}
            onHold={() => window.clearTimeout(timers.current.hide)}
            onClose={hide}
            onOpen={(card) => {
              hide()
              open.current(card)
            }}
          />,
          document.body,
        )}
    </Context.Provider>
  )
}

function Back({
  held,
  sheet,
  onLetGo,
  onHold,
  onClose,
  onOpen,
}: {
  held: Held
  sheet: boolean
  onLetGo: () => void
  onHold: () => void
  onClose: () => void
  onOpen: (card: ShelfCard) => void
}) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const wish = useWish()
  const procure = useProcure()
  const { card } = held
  const back = useBackText(card)
  const box = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<CSSProperties>({})
  const [shown, setShown] = useState(false)
  const asked = procure.allowed ? procure.kindsOf({ token: card.token }) : []
  const loading = asked.some((item) => item.download)
  const owned = card.owned_work_id !== null
  const wished = wish.wished(card)
  const author = card.authors[0] ?? ''
  const audio = card.minutes ?? 0
  const soon = card.upcoming

  // Beside the cover, to its right, else to its left, and on the screen; as a sheet it sits at the bottom (the stylesheet).
  useLayoutEffect(() => {
    if (sheet || !held.box || !box.current) return
    const width = Math.min(340, window.innerWidth - 24)
    const r = held.box
    let x = r.right + 16
    if (x + width > window.innerWidth - 12) x = r.left - 16 - width
    if (x < 12) x = Math.max(12, Math.min(window.innerWidth - width - 12, r.left))
    const height = box.current.offsetHeight
    const y = Math.max(76, Math.min(r.top - 8, window.innerHeight - height - 12))
    setPlace({ left: x, top: y, width })
  }, [held.box, sheet, card.token, back.text])

  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(true))
    return () => cancelAnimationFrame(frame)
  }, [])

  // A sheet is a dialog: the page behind stands still, the focus stays inside and goes back when it is closed.
  useEffect(() => {
    if (!sheet) return
    const before = document.activeElement as HTMLElement | null
    // The page behind stands still (the class the other layers over the page use), unless another layer holds it already.
    const holds = !document.body.classList.contains('lock')
    if (holds) document.body.classList.add('lock')
    const layer = box.current
    if (layer) focusables(layer)[0]?.focus({ preventScroll: true })
    const key = (event: globalThis.KeyboardEvent) => {
      if (layer) trapTab(event, layer)
    }
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('keydown', key, true)
      if (holds) document.body.classList.remove('lock')
      if (before?.isConnected) before.focus({ preventScroll: true })
    }
  }, [sheet])

  const lookUp = async () => {
    try {
      const found = (await catalogApi.people(author)).items[0]
      const href = found ? authorHref(found) : null
      if (href) {
        onClose()
        navigate(href)
      }
    } catch {
      // The name stays a name.
    }
  }
  const eyebrow = [
    card.genre ? t(`library.genre.${card.genre}`, { defaultValue: '' }) : '',
    card.series ? `${card.series}${card.series_pos !== null ? `, ${card.series_pos}` : ''}` : '',
  ]
    .filter(Boolean)
    .join(' · ')
  const when = card.released ? dayText(card.released, i18n.language, true) : card.year ? String(card.year) : ''

  return (
    <>
      {sheet && <div className="hand-back" onClick={onClose} data-testid="hand-back" />}
      <div
        ref={box}
        className={`hand acq ${sheet ? 'sheet' : ''} ${shown ? 'show' : ''}`}
        style={{ '--bc': card.color, ...(sheet ? {} : place) } as CSSProperties}
        role={sheet ? 'dialog' : 'group'}
        aria-modal={sheet || undefined}
        aria-label={card.title}
        data-testid="hand"
        onMouseEnter={onHold}
        onMouseLeave={onLetGo}
      >
        {sheet && (
          <button type="button" className="h-x" onClick={onClose} aria-label={t('browse.hand.close')} data-testid="hand-close">
            <X size={18} aria-hidden />
          </button>
        )}
        <div className="h-in">
          {sheet && (
            <span className="h-cv">
              <Cover work={coverOf(card)} />
            </span>
          )}
          <div className="h-body">
            {eyebrow && <p className="h-eye">{eyebrow}</p>}
            <b className="h-t" dir="auto">
              {card.title}
            </b>
            {author && (
              <span className="h-by">
                {t('browse.hand.by')}{' '}
                <button type="button" onClick={() => void lookUp()} aria-label={t('browse.hand.author', { name: author })} dir="auto">
                  {author}
                </button>
              </span>
            )}
            {card.award && (
              <span className="h-award">
                <Award aria-hidden />
                {card.award}
              </span>
            )}
            <p className={`h-blurb ${back.text ? '' : 'waiting'}`} dir="auto" data-testid="hand-blurb">
              {back.text || (back.waiting ? t('browse.hand.waiting') : t('browse.hand.none'))}
            </p>
            <div className="h-facts">
              {back.pages && (
                <span>
                  <b>{back.pages}</b>
                  {t('browse.hand.pages')}
                </span>
              )}
              {audio > 0 && (
                <span>
                  <b>{minutesText(audio, t)}</b>
                  {t('browse.hand.audio')}
                </span>
              )}
              {when && (
                <span>
                  <b>{when}</b>
                  {soon ? t('browse.hand.comes') : t('browse.hand.released')}
                </span>
              )}
            </div>
            <div className="chips h-fass">
              <FormChips forms={card.forms} />
            </div>
            <div className="h-act">
              {owned ? (
                <button type="button" className="bbtn small primary" onClick={() => onOpen(card)}>
                  {t('browse.hand.viewOwned')}
                </button>
              ) : (
                <>
                  {procure.allowed && asked.length === 0 && (
                    <button
                      type="button"
                      className="bbtn small primary"
                      data-procure={card.token}
                      onClick={() => {
                        onClose()
                        procure.open([acquireBookOfCard(card)])
                      }}
                    >
                      <Download aria-hidden />
                      {t('acquire.entry.get')}
                    </button>
                  )}
                  {asked.length > 0 && (
                    <span className="rchip ok" data-testid="hand-asked">
                      <CheckIcon />
                      {loading ? t('acquire.entry.loading') : t('acquire.entry.wished')}
                    </span>
                  )}
                  <button
                    type="button"
                    className={`bbtn small ${!procure.allowed && !wished ? 'primary' : ''}`}
                    aria-pressed={wished}
                    data-wish={card.token}
                    onClick={() => void wish.toggle(card)}
                  >
                    <MarkIcon />
                    {wished ? t('discover.badge.wished') : t('discover.badge.wish')}
                  </button>
                  <button type="button" className="bbtn small" onClick={() => onOpen(card)}>
                    {t('browse.hand.view')}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

/** The forms the book is known in, as the back of the design names them: "E-Book · Deutsch", "Hörbuch · gekürzt". */
function FormChips({ forms }: { forms: ShelfCard['forms'] }) {
  const { t, i18n } = useTranslation()
  const seen = new Set<string>()
  return (
    <>
      {forms
        .filter((form) => {
          const key = `${form.kind}|${form.language}|${form.variant}`
          if (seen.has(key)) return false
          seen.add(key)
          return true
        })
        .map((form) => {
          const audio = form.kind === 'audio'
          const variant = audio && form.variant && form.variant !== 'unabridged' ? t(`library.variant.${form.variant}`) : ''
          const text = audio
            ? variant
              ? t('work.twin.audio', { variant })
              : t('work.twin.audioPlain')
            : form.language
              ? t('work.twin.ebook', {
                  language: languageName(form.language, i18n.language),
                })
              : t('discover.format.ebook')
          return (
            <span key={`${form.kind}${form.language}${form.variant}`} className="chip" data-kind={form.kind}>
              {audio ? <AudioIcon /> : <BookIcon />}
              {text}
            </span>
          )
        })}
    </>
  )
}
