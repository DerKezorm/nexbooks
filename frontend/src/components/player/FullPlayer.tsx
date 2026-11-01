/**
 * The big player, over the whole window in the colours of the book, as the page of a book is: the cover large, the
 * chapter with its own bar ("Kapitel 14 von 38", what is left of it and of the book at the speed that plays), the jumps, play and
 * pause, and below them the speed, the sleep timer, the chapters, the bookmarks and the choices, each in a panel of its own.
 *
 * Keys: the space bar plays and pauses, the arrows jump back and ahead, Escape closes a panel and then the big player. On a phone
 * everything is large enough for a thumb, and the panels come from below.
 */
import { BookCheck, BookOpen, Bookmark, ChevronDown, ChevronsLeft, ChevronsRight, Download, Gauge, List, Pause, Play, Settings2, X } from 'lucide-react'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { ApiError, downloadUrl, historyApi, type HistoryOut } from '../../api/client'
import { formatBytes } from '../../lib/bytes'
import { dayText } from '../../lib/days'
import { useEditionLabel } from '../../lib/editionText'
import { errorText } from '../../lib/errors'
import { trapTab } from '../../lib/focusTrap'
import { clock, heardIn } from '../../lib/timeline'
import { usePlayer, usePlayerLive } from '../../state/player'
import { Cover } from '../BookCard'
import { useConfirm } from '../useConfirm'
import { JumpIcon, MoonIcon } from './icons'
import { PlayerPanel, speedText, type PanelKind } from './PlayerPanel'

export function FullPlayer() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { duration } = useEditionLabel()
  const player = usePlayer()
  const live = usePlayerLive()
  const { book, engine, speed, sleep, hint, chapter, choices, said, loading } = live
  const [panel, setPanel] = useState<PanelKind | null>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const commit = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { ask, dialog } = useConfirm()

  // "Finished" by hand: the entry of this recording, if the person made one; one press is one request.
  const editionId = book?.edition.id
  const workId = book?.work.id
  const [finished, setFinished] = useState<HistoryOut | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const finishing = useRef(false)
  useEffect(() => {
    if (editionId === undefined || workId === undefined) return
    historyApi.ofWork(workId).then(
      (list) => setFinished((Array.isArray(list) ? list : []).find((entry) => entry.edition_id === editionId) ?? null),
      () => setFinished(null),
    )
  }, [editionId, workId])
  useEffect(() => {
    if (!note) return
    const timer = window.setTimeout(() => setNote(null), 4000)
    return () => window.clearTimeout(timer)
  }, [note])
  const toggleFinished = async () => {
    if (editionId === undefined || finishing.current) return
    if (finished) {
      ask({
        title: t('finish.undoTitle'),
        text: t('finish.undoText', { date: dayText(finished.finished_on, i18n.language) }),
        confirm: t('finish.undo'),
        danger: false,
        run: async () => {
          await historyApi.remove(finished.id)
          setFinished(null)
          setNote(t('finish.removed'))
        },
      })
      return
    }
    finishing.current = true
    try {
      setFinished(await historyApi.finish(editionId))
      setNote(t('finish.marked'))
    } catch (error) {
      setNote(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
    } finally {
      finishing.current = false
    }
  }

  // The page under it stands still, the focus comes in and stays in.
  useEffect(() => {
    document.body.classList.add('lock')
    root.current?.focus({ preventScroll: true })
    const layer = root.current
    const key = (event: KeyboardEvent) => void (layer && trapTab(event, layer))
    layer?.addEventListener('keydown', key)
    return () => {
      document.body.classList.remove('lock')
      layer?.removeEventListener('keydown', key)
    }
  }, [])

  // The keys of the player. Taken before the page under it (capture), so that Escape folds the player up and does not close
  // the page of the book under it as well, and "/" does not go to a search field the player covers.
  const panelRef = useRef(panel)
  panelRef.current = panel
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const target = event.target as HTMLElement | null
      const typing = target && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)
      if (event.key === 'Escape') {
        // A dialog over the player (a question, `Dialog`) answers first: it takes the key itself. The page of a book under the
        // player is a dialog too, but not one over it.
        if (document.body.classList.contains('has-dialog')) return
        event.preventDefault()
        event.stopPropagation()
        if (panelRef.current) setPanel(null)
        else player.collapse()
        return
      }
      if (typing || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === '/') {
        event.stopPropagation()
      } else if (event.key === ' ' && !(target && ['BUTTON', 'A'].includes(target.tagName))) {
        event.preventDefault()
        event.stopPropagation()
        player.toggle()
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        event.stopPropagation()
        player.skip(event.key === 'ArrowLeft' ? -choices.back : choices.ahead)
      }
    }
    window.addEventListener('keydown', key, true)
    return () => window.removeEventListener('keydown', key, true)
  }, [player, choices.back, choices.ahead])

  if (!book) {
    return (
      <div className="pl" role="dialog" aria-modal="true" aria-label={t('player.region')} ref={root} tabIndex={-1} data-testid="full-player">
        <p className="pl-loading" role="status">
          {t('player.loading')}
        </p>
      </div>
    )
  }

  const span = book.spans[chapter]
  const spanLength = span ? Math.max(0, span.end - span.start) : 0
  const inChapter = span ? Math.min(spanLength, Math.max(0, engine.position - span.start)) : 0
  const shownInChapter = drag ?? inChapter
  const bookLeft = heardIn(book.total - engine.position, speed)
  const percent = book.total > 0 ? Math.round((engine.position / book.total) * 100) : 0
  const authors = book.work.authors.map((author) => author.name).join(', ')
  const narrators = book.edition.narrators.join(', ')
  const style = { '--bc': book.work.color } as CSSProperties
  const failure = engine.failure
  const formats = [...new Set(book.parts.map((part) => part.format.toUpperCase()))].join(', ')

  const slide = (value: number) => {
    setDrag(value)
    if (commit.current) clearTimeout(commit.current)
    commit.current = setTimeout(() => {
      commit.current = null
      player.seek((span?.start ?? 0) + value)
      setDrag(null)
    }, 250)
  }

  const sleepLabel =
    sleep.kind === 'minutes' ? clock(sleep.left) : sleep.kind === 'chapter' ? t('player.sleep.toChapterEnd') : t('player.sleep.title')
  const hintWhere = hint?.position !== null && hint?.position !== undefined ? clock(hint.position) : ''

  return (
    <div className="pl" role="dialog" aria-modal="true" aria-label={t('player.region')} style={style} ref={root} tabIndex={-1} data-testid="full-player" data-playing={engine.playing ? 'yes' : 'no'} data-position={engine.position.toFixed(2)}>
      <div className="pl-glow" aria-hidden />
      <div className="pl-wrap">
        <header className="pl-top">
          <button type="button" className="pl-icon" onClick={player.collapse} aria-label={t('player.collapse')} title={t('player.collapse')} data-testid="player-collapse">
            <ChevronDown size={22} aria-hidden />
          </button>
          <span className="pl-eyebrow">{t('player.now')}</span>
          <span className="pl-top-end">
            <button
              type="button"
              className="pl-icon"
              onClick={() => {
                // The page of the book takes the place of the big view in the history: the way back leads to the page before.
                navigate(`/work/${book.work.id}`, { replace: true })
              }}
              aria-label={t('player.toBook')}
              title={t('player.toBook')}
            >
              <BookOpen size={20} aria-hidden />
            </button>
            <button type="button" className="pl-icon" onClick={player.close} aria-label={t('player.close')} title={t('player.close')} data-testid="player-close">
              <X size={20} aria-hidden />
            </button>
          </span>
        </header>

        <div className="pl-main">
          <div className="pl-cover">
            <Cover work={book.work} />
          </div>
          <div className="pl-side">
            <p className="pl-chapter-of" data-testid="player-chapter-of">
              {t('player.chapterOf', { n: chapter + 1, total: book.spans.length })}
            </p>
            <h2 className="pl-title" dir="auto">
              {book.work.title}
            </h2>
            <p className="pl-by" dir="auto">
              {authors}
              {narrators && <span> · {t('work.twin.readBy', { name: narrators })}</span>}
            </p>
            <p className="pl-chapter" dir="auto" data-testid="player-chapter">
              {span?.title}
            </p>

            <div className="pl-scrub">
              <input
                type="range"
                min={0}
                max={Math.max(1, Math.floor(spanLength))}
                step={1}
                value={Math.floor(shownInChapter)}
                onChange={(event) => slide(Number(event.target.value))}
                aria-label={t('player.inChapter')}
                aria-valuetext={`${clock(shownInChapter)} / ${clock(spanLength)}`}
                style={{ '--done': `${spanLength > 0 ? (shownInChapter / spanLength) * 100 : 0}%` } as CSSProperties}
                disabled={loading || failure === 'unsupported'}
                data-testid="player-scrub"
              />
              <div className="pl-times">
                <span data-testid="player-elapsed">{clock(shownInChapter)}</span>
                <span data-testid="player-chapter-left">−{clock(heardIn(spanLength - shownInChapter, speed))}</span>
              </div>
            </div>

            <div className="pl-controls">
              <button type="button" className="pl-icon pl-big" onClick={player.chapterBack} aria-label={t('player.chapterBack')} title={t('player.chapterBack')} disabled={loading} data-testid="player-chapter-back">
                <ChevronsLeft size={24} aria-hidden />
              </button>
              <button type="button" className="pl-icon pl-big" onClick={() => player.skip(-choices.back)} aria-label={t('player.back', { n: choices.back })} title={t('player.back', { n: choices.back })} disabled={loading} data-testid="player-back">
                <JumpIcon dir="back" seconds={choices.back} size={34} />
              </button>
              <button type="button" className="pl-play" onClick={player.toggle} aria-label={engine.playing ? t('player.pause') : t('player.play')} disabled={loading || failure !== null} data-testid="player-play">
                {engine.playing ? <Pause size={30} aria-hidden fill="currentColor" /> : <Play size={30} aria-hidden fill="currentColor" />}
              </button>
              <button type="button" className="pl-icon pl-big" onClick={() => player.skip(choices.ahead)} aria-label={t('player.ahead', { n: choices.ahead })} title={t('player.ahead', { n: choices.ahead })} disabled={loading} data-testid="player-ahead">
                <JumpIcon dir="ahead" seconds={choices.ahead} size={34} />
              </button>
              <button type="button" className="pl-icon pl-big" onClick={player.chapterAhead} aria-label={t('player.chapterAhead')} title={t('player.chapterAhead')} disabled={loading || chapter + 1 >= book.spans.length} data-testid="player-chapter-ahead">
                <ChevronsRight size={24} aria-hidden />
              </button>
            </div>

            <div className="pl-book" aria-hidden>
              <span className="line" />
              <span className="done" style={{ width: `${percent}%` }} />
              {book.spans.length <= 120 && book.spans.slice(1).map((one) => <i key={one.number} className="tick" style={{ left: `${(one.start / Math.max(1, book.total)) * 100}%` }} />)}
            </div>
            <p className="pl-left" data-testid="player-book-left">
              {t('player.bookLeft', { time: duration(bookLeft), percent })}
              {engine.waiting && !failure && <span className="pl-wait"> · {t('player.waiting')}</span>}
            </p>

            <div className="pl-tools">
              <button type="button" className={`pl-tool ${panel === 'speed' ? 'on' : ''}`} onClick={() => setPanel(panel === 'speed' ? null : 'speed')} aria-label={t('player.speed.title')} data-testid="open-speed">
                <Gauge size={17} aria-hidden />
                <span>{speedText(speed, i18n.language)}</span>
              </button>
              <button type="button" className={`pl-tool ${panel === 'sleep' ? 'on' : ''} ${sleep.kind !== 'off' ? 'active' : ''}`} onClick={() => setPanel(panel === 'sleep' ? null : 'sleep')} aria-label={t('player.sleep.title')} data-testid="open-sleep">
                <MoonIcon size={17} />
                <span data-testid="sleep-label">{sleepLabel}</span>
              </button>
              <button type="button" className={`pl-tool ${panel === 'chapters' ? 'on' : ''}`} onClick={() => setPanel(panel === 'chapters' ? null : 'chapters')} aria-label={t('player.chapters.title')} data-testid="open-chapters">
                <List size={17} aria-hidden />
                <span>{t('player.chapters.title')}</span>
              </button>
              <button type="button" className={`pl-tool ${panel === 'marks' ? 'on' : ''}`} onClick={() => setPanel(panel === 'marks' ? null : 'marks')} aria-label={t('player.marks.title')} data-testid="open-marks">
                <Bookmark size={17} aria-hidden />
                <span>{t('player.marks.title')}</span>
              </button>
              <button type="button" className={`pl-tool ${finished ? 'on' : ''}`} onClick={() => void toggleFinished()} aria-pressed={finished !== null} aria-label={finished ? t('finish.done', { date: dayText(finished.finished_on, i18n.language) }) : t('finish.menu')} title={finished ? t('finish.done', { date: dayText(finished.finished_on, i18n.language) }) : t('finish.menu')} data-testid="player-finish">
                <BookCheck size={17} aria-hidden />
                <span>{finished ? t('finish.done', { date: dayText(finished.finished_on, i18n.language) }) : t('finish.mark')}</span>
              </button>
              <button type="button" className={`pl-tool ${panel === 'settings' ? 'on' : ''}`} onClick={() => setPanel(panel === 'settings' ? null : 'settings')} aria-label={t('player.settings.title')} title={t('player.settings.title')} data-testid="open-player-settings">
                <Settings2 size={17} aria-hidden />
              </button>
            </div>

            {note && (
              <p className="pl-note" role="status" data-testid="player-finish-note">
                {note}
              </p>
            )}

            {hint && (
              <div className="pl-hint" role="status" data-testid="player-hint">
                <p>{hint.device ? t('player.hint.text', { device: hint.device, where: hintWhere }) : t('player.hint.textNoName', { where: hintWhere })}</p>
                <div>
                  <button type="button" className="go" onClick={player.jumpToHint} data-testid="player-hint-jump">
                    {t('player.hint.jump')}
                  </button>
                  <button type="button" className="pl-quiet" onClick={player.stayHere} data-testid="player-hint-stay">
                    {t('player.hint.stay')}
                  </button>
                </div>
              </div>
            )}

            {failure && (
              <div className="pl-failed" role="alert" data-testid="player-failed" data-reason={failure}>
                {engine.failedPart !== null && book.parts.length > 1 && (
                  <p className="pl-failed-part" data-testid="player-failed-part">
                    {t('player.failed.partOf', { n: engine.failedPart + 1, count: book.parts.length })}
                  </p>
                )}
                <p>{t(`player.failed.${failure}`, { formats })}</p>
                <div className="pl-failed-go">
                  {failure !== 'network' && engine.failedPart !== null && engine.failedPart + 1 < book.parts.length && (
                    <button type="button" className="go" onClick={player.skipPart} data-testid="player-skip-part">
                      {t('player.failed.skip')}
                    </button>
                  )}
                  {failure === 'network' && (
                    <button type="button" className="go" onClick={player.retry}>
                      {t('player.failed.retry')}
                    </button>
                  )}
                  {failure !== 'network' &&
                    (book.parts.length === 1 ? (
                      <a className="go" href={downloadUrl(book.parts[0].id)} download data-testid="player-download">
                        <Download size={15} aria-hidden /> {t('player.failed.download')}
                      </a>
                    ) : (
                      <details className="pl-parts">
                        <summary>{t('player.failed.downloadParts', { count: book.parts.length })}</summary>
                        <ul>
                          {book.parts.map((part, index) => (
                            <li key={part.id}>
                              <a href={downloadUrl(part.id)} download>
                                <Download size={13} aria-hidden /> {t('work.twin.part', { n: index + 1 })} · {part.format.toUpperCase()} · {formatBytes(part.size, i18n.language, 1000)}
                              </a>
                            </li>
                          ))}
                        </ul>
                      </details>
                    ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {said && (
        <p className="pl-toast" role="status" data-testid="player-said">
          {said}
        </p>
      )}
      {panel && <div className="pl-scrim" onClick={() => setPanel(null)} aria-hidden />}
      {panel && <PlayerPanel kind={panel} onClose={() => setPanel(null)} />}
      {dialog}
    </div>
  )
}
