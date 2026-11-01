/**
 * The reader: a book on the whole screen, with a bar above and a bar below that come and go (a tap in the middle of the page),
 * and the book itself in a frame of its own (`useBookFrame`). Above: back to the page of the book, the title, the contents, the
 * search, a bookmark and the look. Below: where the reader is ("Kapitel 14 von 38 · 37 %") over a bar with a stroke for every
 * chapter, which can be dragged. The place is saved while the person reads and when they leave (`lib/readerProgress.ts`); a
 * book that is only opened saves nothing, and when another device is further along the person is asked before anything is
 * saved.
 *
 * Phones first: the edges of the page turn it (a tap, or a swipe in the frame), the middle shows or hides the bars, the panels
 * come over the page from the side, and the look is set in a sheet from below.
 */
import { ArrowLeft, BookCheck, Bookmark, ChevronLeft, ChevronRight, List, Search, Type } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, authApi, historyApi, readingApi, type BookmarkOut, type EditionOut, type HistoryOut, type ProgressOut, type WorkBrief } from '../../api/client'
import { dayText } from '../../lib/days'
import { errorText } from '../../lib/errors'
import { reducedMotion } from '../../lib/motion'
import { deviceName, lookOf, MAX_BOOK_MB, PAGE_COLOURS, type PageTheme, type Place, type ProfileLook, type ReaderTheme } from '../../lib/readFrame'
import { chooseStart, deviceKey, loadLocal, ProgressSaver, saveLocal, whereOf } from '../../lib/readerProgress'
import { storedTheme } from '../../lib/theme'
import { useAuth } from '../../state/auth'
import { useConfirm } from '../useConfirm'
import { LookPanel, type LookChange } from './LookPanel'
import { ReaderPanel, type Tab } from './ReaderPanel'
import { useBookFrame, type FrameFile } from './useBookFrame'

type Props = {
  editionId: number
  work: WorkBrief
  edition: EditionOut
  file: FrameFile
  progress: ProgressOut | null
  bookmarks: BookmarkOut[]
  onBack: () => void
}

const ACCENT: Record<PageTheme, string> = { light: '#8e3b46', sepia: '#8e3b46', dark: '#d98591' }

function useAppIsDark(): boolean {
  const [dark, setDark] = useState(() => storedTheme() === 'dark')
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    const update = () => setDark(storedTheme() === 'dark')
    query?.addEventListener?.('change', update)
    return () => query?.removeEventListener?.('change', update)
  }, [])
  return dark
}

function profileLook(profile: Record<string, unknown> | undefined): ProfileLook {
  return (profile ?? {}) as ProfileLook
}

export function Reader({ editionId, work, edition, file, progress, bookmarks: first, onBack }: Props) {
  const { t, i18n } = useTranslation()
  const { me, setMe } = useAuth()
  const { ask, dialog } = useConfirm()
  const appDark = useAppIsDark()
  const pdf = file.format === 'pdf'

  // ---- The look ----------------------------------------------------------------------------------------------------------------
  const [choices, setChoices] = useState<ProfileLook>(() => profileLook(me?.profile as unknown as Record<string, unknown>))
  const look = useMemo(() => {
    const base = lookOf(choices, appDark, '', !reducedMotion())
    return { ...base, accent: ACCENT[base.theme] }
  }, [choices, appDark])
  const colours = PAGE_COLOURS[look.theme]
  const saveChoices = useRef<{ timer: ReturnType<typeof setTimeout> | null; pending: LookChange }>({ timer: null, pending: {} })
  const changeLook = (change: LookChange) => {
    setChoices((current) => ({ ...current, ...change }))
    const slot = saveChoices.current
    slot.pending = { ...slot.pending, ...change }
    if (slot.timer) clearTimeout(slot.timer)
    slot.timer = setTimeout(() => {
      const send = slot.pending
      slot.pending = {}
      slot.timer = null
      authApi.preferences(send).then(
        (profile) => me && setMe({ ...me, profile }),
        () => undefined,
      )
    }, 500)
  }

  // ---- Where it opens ------------------------------------------------------------------------------------------------------------
  const device = useMemo(() => ({ key: deviceKey(), name: deviceName(navigator.userAgent, navigator.platform, navigator.maxTouchPoints) }), [])
  const opening = useMemo(() => chooseStart(progress, loadLocal(me?.id ?? 0, editionId), device.key), [progress, editionId, device.key, me?.id])
  const [hint, setHint] = useState<ProgressOut | null>(opening.ask)

  const [panel, setPanel] = useState<Tab | null>(null)
  const [lookOpen, setLookOpen] = useState(false)
  const [chrome, setChrome] = useState(true)
  const [status, setStatus] = useState<string | null>(null)
  const [notices, setNotices] = useState<('flattened' | 'cut' | 'missing')[]>([])
  const [bookmarks, setBookmarks] = useState<BookmarkOut[]>(first)
  const [query, setQuery] = useState('')
  const [drag, setDrag] = useState<number | null>(null)

  // ---- The place -----------------------------------------------------------------------------------------------------------------
  const armed = useRef(false)
  const actedAt = useRef(-1e9)
  const saver = useRef<ProgressSaver | null>(null)
  const hinted = useRef<ProgressOut | null>(hint)
  hinted.current = hint
  const ours = useRef(0)
  if (saver.current === null) {
    saver.current = new ProgressSaver(
      (body, keep) => readingApi.save(editionId, body, keep),
      device,
      (result) => {
        // A newer place from another device was there: the person is told, and nothing of ours is saved until they answer.
        if (!result.applied && result.progress.device_key !== device.key && result.progress.fraction - ours.current > 0.004) {
          setHint(result.progress)
          saver.current?.hold()
        }
      },
    )
    if (opening.ask) saver.current.hold()
  }

  const closeHint = useCallback(() => {
    setHint(null)
    saver.current?.release()
  }, [])

  const onPlace = useCallback(
    (place: Place & { reason?: string }) => {
      const byPerson = place.reason === 'page' || place.reason === 'snap' || place.reason === 'scroll' || performance.now() - actedAt.current < 2000
      ours.current = place.fraction
      if (byPerson) {
        // On a phone the bars go once reading has begun; with a mouse they stay (the middle of the page still shows or hides them).
        if (!armed.current && window.matchMedia?.('(pointer: coarse)').matches) setChrome(false)
        armed.current = true
        if (hinted.current) closeHint()
      }
      if (!armed.current) return
      saveLocal(me?.id ?? 0, editionId, { locator: place.locator, fraction: place.fraction, at: Date.now() })
      saver.current?.report(place)
    },
    [editionId, closeHint, me?.id],
  )

  // ---- The frame -----------------------------------------------------------------------------------------------------------------
  const side = useRef({ panel, lookOpen })
  side.current = { panel, lookOpen }
  /** Anything the person asks the reader to do: the move it makes is theirs (and so is saved). */
  const act = (action: () => void) => {
    actedAt.current = performance.now()
    action()
  }
  const handleKey = useCallback(
    (key: { key: string; ctrl: boolean; meta: boolean }) => {
      if (key.key === 'Escape') {
        // Escape closes what is open, and leaves the book when nothing is.
        if (side.current.panel) setPanel(null)
        else if (side.current.lookOpen) setLookOpen(false)
        else onBack()
      } else if (key.key === '/' || ((key.ctrl || key.meta) && key.key.toLowerCase() === 'f')) {
        setLookOpen(false)
        setPanel('search')
        setChrome(true)
      }
    },
    [onBack],
  )
  const askLink = (address: string) =>
    ask({
      title: t('reader.linkTitle'),
      text: (
        <>
          <p>{t('reader.linkText')}</p>
          <p className="mt-2 max-h-32 overflow-auto">
            <code className="text-xs break-all" dir="ltr" data-testid="reader-link-address">
              {address}
            </code>
          </p>
        </>
      ),
      confirm: t('reader.linkOpen'),
      danger: false,
      // Opened inside the click on the button, so that the browser takes it for the person's own.
      run: async () => {
        window.open(address, '_blank', 'noopener,noreferrer')
      },
    })
  const { frameRef, state, actions, src, sandbox, attempt } = useBookFrame(file, opening.start, look, {
    onPlace,
    onKey: handleKey,
    onTap: () => setChrome((on) => !on || panel !== null || lookOpen),
    // A page that had to be flattened or cut: said once, in the line that also says a bookmark is set.
    onNotice: (kind) => setNotices((list) => (list.includes(kind) ? list : [...list, kind])),
    // A link of the book is not followed on the spot: the person sees the address first and decides.
    onLink: askLink,
  })

  // The place is saved when the page is left or hidden (a closed tab, another app on the phone), and once more on the way out.
  useEffect(() => {
    const keep = saver.current
    const hide = () => {
      if (document.visibilityState === 'hidden') void keep?.flush(true)
    }
    const leave = () => void keep?.flush(true)
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', leave)
    return () => {
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', leave)
      void keep?.flush(true)
      keep?.stop()
    }
  }, [])

  useEffect(() => {
    document.body.classList.add('lock')
    return () => document.body.classList.remove('lock')
  }, [])

  // The keys of the page itself: the arrows turn, Escape closes. Inside the frame the frame handles them and tells us.
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
      if (event.key === 'Escape') {
        handleKey({ key: 'Escape', ctrl: false, meta: false })
        return
      }
      if (typing || event.ctrlKey || event.metaKey || event.altKey) {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
          event.preventDefault()
          handleKey({ key: event.key, ctrl: event.ctrlKey, meta: event.metaKey })
        }
        return
      }
      if (event.key === '/') {
        event.preventDefault()
        handleKey({ key: '/', ctrl: false, meta: false })
      } else if ((event.key === ' ' || event.key === 'PageDown' || event.key === 'PageUp') && !(target && ['BUTTON', 'A', 'INPUT'].includes(target.tagName))) {
        // The space bar and the page keys turn the page, unless a button or the bar has the focus (then they are its own).
        event.preventDefault()
        act(() => actions.turn(event.key === 'PageUp' || (event.key === ' ' && event.shiftKey) ? 'prev' : 'next'))
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        // Over a button the arrows are the button's (a slider); elsewhere they turn the page.
        if (target && ['BUTTON', 'A'].includes(target.tagName) && target.closest('.rd-panel, .rd-look')) return
        if (target?.tagName === 'INPUT') return
        event.preventDefault()
        act(() => actions.turn((event.key === 'ArrowLeft') === state.rtl ? 'next' : 'prev'))
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
    // `act` and `actions` only wrap the frame's messages; the keys follow the frame's direction of reading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleKey, state.rtl])

  useEffect(() => {
    if (!status) return
    const timer = window.setTimeout(() => setStatus(null), 3500)
    return () => window.clearTimeout(timer)
  }, [status])

  // What was done to a page of the book is said for longer than "Bookmark set": it is long, and it is news.
  useEffect(() => {
    if (notices.length === 0) return
    const timer = window.setTimeout(() => setNotices([]), 9000)
    return () => window.clearTimeout(timer)
  }, [notices])
  const toast = status ?? (notices.length > 0 ? notices.map((kind) => t(`reader.notice.${kind}`)).join(' ') : null)

  // ---- The bars ----------------------------------------------------------------------------------------------------------------------
  const place = state.place
  const fraction = place?.fraction ?? 0
  const shown = drag ?? fraction
  const percent = Math.round(shown * 100)
  const label = !place
    ? ''
    : place.chapter > 0 && place.chapterCount > 0
      ? t(place.unit === 'page' ? 'reader.pageOf' : 'reader.chapterOf', { n: place.chapter, total: place.chapterCount, percent })
      : t('reader.percent', { percent })

  const half = 0.5 / Math.max(50, pdf ? state.pages : edition.pages || 300)
  const marked = place ? bookmarks.some((bookmark) => Math.abs(bookmark.fraction - place.fraction) < half) : false

  const addBookmark = async () => {
    if (!place) return
    if (marked) {
      setStatus(t('reader.bookmarkHere'))
      setPanel('bookmarks')
      return
    }
    try {
      const made = await readingApi.addBookmark(editionId, { locator: place.locator, fraction: place.fraction, chapter_title: place.chapterTitle })
      setBookmarks((list) => [...list.filter((mark) => mark.id !== made.id), made].sort((a, b) => a.fraction - b.fraction || a.id - b.id))
      setStatus(t('reader.bookmarkSet'))
    } catch (error) {
      setStatus(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
    }
  }

  // "Finished" by hand: the day the person entered this book as finished, if they did. One press is one request.
  const [finished, setFinished] = useState<HistoryOut | null>(null)
  const finishing = useRef(false)
  useEffect(() => {
    historyApi.ofWork(work.id).then(
      (list) => setFinished((Array.isArray(list) ? list : []).find((entry) => entry.edition_id === editionId) ?? null),
      () => undefined,
    )
  }, [work.id, editionId])
  const toggleFinished = async () => {
    if (finishing.current) return
    if (finished) {
      ask({
        title: t('finish.undoTitle'),
        text: t('finish.undoText', { date: dayText(finished.finished_on, i18n.language) }),
        confirm: t('finish.undo'),
        danger: false,
        run: async () => {
          await historyApi.remove(finished.id)
          setFinished(null)
          setStatus(t('finish.removed'))
        },
      })
      return
    }
    finishing.current = true
    try {
      setFinished(await historyApi.finish(editionId))
      setStatus(t('finish.marked'))
    } catch (error) {
      setStatus(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
    } finally {
      finishing.current = false
    }
  }

  const deleteBookmark = (bookmark: BookmarkOut) => {
    ask({
      title: t('reader.bookmarkDeleteTitle'),
      text: t('reader.bookmarkDeleteText', { label: bookmark.chapter_title || `${Math.round(bookmark.fraction * 100)} %` }),
      confirm: t('reader.bookmarkDeleteConfirm'),
      run: async () => {
        await readingApi.removeBookmark(bookmark.id)
        setBookmarks((list) => list.filter((mark) => mark.id !== bookmark.id))
      },
    })
  }

  const commit = useRef<ReturnType<typeof setTimeout> | null>(null)
  const slide = (value: number) => {
    setDrag(value)
    if (commit.current) clearTimeout(commit.current)
    commit.current = setTimeout(() => {
      commit.current = null
      act(() => actions.gotoFraction(value))
      setDrag(null)
    }, 250)
  }

  const openPanel = (tab: Tab) => {
    setLookOpen(false)
    setPanel((open) => (open === tab ? null : tab))
    setChrome(true)
  }
  const sideOpen = panel !== null || lookOpen
  const barsOn = chrome || sideOpen || hint !== null
  const first_ = hint ? whereOf(hint) : null
  const where = first_ ? t(`reader.hint.${first_.key}`, first_.key === 'percent' ? { percent: first_.n } : { n: first_.n }) : ''
  const kind = pdf ? 'pdf' : state.fixed ? 'fixed' : 'text'
  const values = {
    font: look.font,
    size: look.size,
    line: look.line,
    margin: look.margin,
    theme: (choices.reader_theme ?? 'auto') as ReaderTheme,
    justify: look.justify,
    flow: look.flow,
  }
  const rtl = state.rtl
  const failed = state.phase === 'failed'

  return (
    <div
      className={`reader ${barsOn ? 'bars' : 'bare'}`}
      data-testid="reader"
      data-phase={state.phase}
      data-page-theme={look.theme}
      data-chrome={barsOn ? 'on' : 'off'}
      data-fraction={fraction.toFixed(4)}
      data-format={file.format}
      style={{ '--rd-ground': colours.ground, '--rd-ink': colours.ink, '--rd-muted': colours.muted, '--rd-line': colours.line, '--rd-accent': look.accent } as React.CSSProperties}
    >
      <iframe
        key={`${file.id}-${attempt}`}
        ref={frameRef}
        className="rd-frame"
        src={src}
        sandbox={sandbox}
        title={t('reader.frame', { title: work.title })}
        data-testid="reader-frame"
        hidden={failed}
      />

      {state.phase === 'loading' && (
        <p className="rd-loading" role="status" data-testid="reader-loading">
          {t('reader.loading')}
        </p>
      )}

      {failed && (
        <div className="rd-failed" role="alert" data-testid="reader-failed" data-reason={state.reason}>
          <p>{t(`reader.failed.${state.reason ?? 'broken'}`, { limit: MAX_BOOK_MB })}</p>
          <div className="rd-failed-go">
            {state.reason === 'network' && (
              <button type="button" className="go" onClick={actions.retry}>
                {t('reader.failed.retry')}
              </button>
            )}
            {['protected', 'too_large', 'too_many_entries', 'zip_bomb', 'broken', 'password', 'unsupported', 'unsafe'].includes(state.reason ?? '') && (
              <a className="go" href={`/api/v1/files/${file.id}/download`} download>
                {t('reader.failed.download')}
              </a>
            )}
            <button type="button" className="rd-quiet" onClick={onBack}>
              {t('reader.failed.toBook')}
            </button>
          </div>
        </div>
      )}

      <header className="rd-top">
        <button type="button" className="rd-icon" onClick={onBack} aria-label={t('reader.back')} title={t('reader.back')} data-testid="reader-back">
          <ArrowLeft size={20} aria-hidden />
        </button>
        <div className="rd-title" dir="auto">
          <b>{work.title}</b>
          {state.place?.chapterTitle && <span>{state.place.chapterTitle}</span>}
        </div>
        <div className="rd-tools">
          <button type="button" className={`rd-icon ${panel === 'contents' ? 'on' : ''}`} onClick={() => openPanel('contents')} aria-label={t('reader.contents')} title={t('reader.contents')} data-testid="open-contents">
            <List size={20} aria-hidden />
          </button>
          <button type="button" className={`rd-icon ${panel === 'search' ? 'on' : ''}`} onClick={() => openPanel('search')} aria-label={t('reader.search')} title={t('reader.search')} data-testid="open-search">
            <Search size={20} aria-hidden />
          </button>
          <button type="button" className={`rd-icon ${marked ? 'on filled' : ''}`} onClick={() => void addBookmark()} aria-label={t('reader.bookmarkAdd')} title={t('reader.bookmarkAdd')} aria-pressed={marked} disabled={!place} data-testid="add-bookmark">
            <Bookmark size={20} aria-hidden />
          </button>
          <button type="button" className={`rd-icon ${finished ? 'on' : ''}`} onClick={() => void toggleFinished()} aria-label={finished ? t('finish.done', { date: dayText(finished.finished_on, i18n.language) }) : t('finish.menu')} title={finished ? t('finish.done', { date: dayText(finished.finished_on, i18n.language) }) : t('finish.menu')} aria-pressed={finished !== null} data-testid="reader-finish">
            <BookCheck size={20} aria-hidden />
          </button>
          <button type="button" className={`rd-icon ${lookOpen ? 'on' : ''}`} onClick={() => { setPanel(null); setLookOpen((open) => !open); setChrome(true) }} aria-label={t('reader.settings')} title={t('reader.settings')} data-testid="open-look">
            <Type size={20} aria-hidden />
          </button>
        </div>
      </header>

      <footer className="rd-bottom">
        <p className="rd-where" role="status" aria-live="polite" data-testid="reader-where">
          {label}
        </p>
        <div className="rd-nav">
          <button type="button" className="rd-icon rd-step-page" onClick={() => act(() => actions.turn(rtl ? 'next' : 'prev'))} aria-label={t('reader.prev')} data-testid="reader-prev">
            <ChevronLeft size={20} aria-hidden />
          </button>
          <div className="rd-track" dir={rtl ? 'rtl' : 'ltr'}>
            <span className="line" />
            <span className="done" style={{ width: `${shown * 100}%` }} />
            {state.marks.map((mark) => (
              <i key={mark} className="tick" style={{ [rtl ? 'right' : 'left']: `${mark * 100}%` }} />
            ))}
            <input
              type="range"
              min={0}
              max={1000}
              step={1}
              value={Math.round(shown * 1000)}
              onChange={(event) => slide(Number(event.target.value) / 1000)}
              disabled={!place}
              aria-label={t('reader.progress')}
              aria-valuetext={label}
              data-testid="reader-slider"
            />
          </div>
          <button type="button" className="rd-icon rd-step-page" onClick={() => act(() => actions.turn(rtl ? 'prev' : 'next'))} aria-label={t('reader.next')} data-testid="reader-next">
            <ChevronRight size={20} aria-hidden />
          </button>
        </div>
      </footer>

      {hint && (
        <div className="rd-hint" role="status" data-testid="reader-hint">
          <p>{hint.device ? t('reader.hint.text', { device: hint.device, where }) : t('reader.hint.textNoName', { where })}</p>
          <div>
            <button
              type="button"
              className="go"
              onClick={() => {
                armed.current = true
                // A place that came from KOReader's sync has no CFI, only the share of the book.
                act(() => (hint.locator ? actions.goto(hint.locator) : actions.gotoFraction(hint.fraction)))
                closeHint()
              }}
              data-testid="hint-jump"
            >
              {t('reader.hint.jump')}
            </button>
            <button type="button" className="rd-quiet" onClick={closeHint} data-testid="hint-stay">
              {t('reader.hint.stay')}
            </button>
          </div>
        </div>
      )}

      {toast && (
        <p className="rd-toast" role="status" data-testid="reader-status">
          {toast}
        </p>
      )}

      {sideOpen && <div className="rd-scrim" onClick={() => { setPanel(null); setLookOpen(false) }} data-testid="reader-scrim" />}
      {panel && (
        <ReaderPanel
          tab={panel}
          onTab={setPanel}
          toc={state.toc}
          currentToc={place?.tocId ?? -1}
          onToc={(id) => {
            act(() => actions.gotoToc(id))
            setPanel(null)
          }}
          bookmarks={bookmarks}
          onBookmark={(bookmark) => {
            act(() => actions.goto(bookmark.locator))
            setPanel(null)
          }}
          onDeleteBookmark={deleteBookmark}
          query={query}
          onQuery={setQuery}
          found={state.found}
          searching={state.searching}
          searchProgress={state.searchProgress}
          searchDone={state.searchDone}
          count={state.count}
          pdf={pdf}
          onSearch={actions.search}
          onStep={actions.searchStep}
          onClear={actions.searchClear}
          onHit={(cfi) => {
            act(() => actions.goto(cfi))
            if (window.matchMedia?.('(max-width: 720px)').matches) setPanel(null)
          }}
          onClose={() => setPanel(null)}
        />
      )}
      {lookOpen && <LookPanel values={values} kind={kind} onChange={changeLook} onClose={() => setLookOpen(false)} />}
      {dialog}
    </div>
  )
}
