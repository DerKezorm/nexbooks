/**
 * A short listen into a recording from the review list: a small player in a dialog over the page with a play button, the three
 * places a person jumps to to hear how the book begins and how it goes on (the start, a third in, the middle) and the chapters.
 * **Nothing is saved**: the place a person has in the book stays as it was, and the big player of the app is not touched, apart
 * from being paused while this one plays. It plays the same files as the player does (`/files/{id}/content`, with `Range`), so the
 * rights are those of the library.
 */
import { Pause, Play } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, contentUrl, lookContentUrl, procureApi, readingApi } from '../../api/client'
import { browserProbe, PROBE, unplayable } from '../../lib/audioFormats'
import { PlayerEngine, type EngineState } from '../../lib/playerEngine'
import { jumpPoints } from '../../lib/reviewFacts'
import { chapterAt, clock, partsOf, spansOf, totalOf, type Part, type Span } from '../../lib/timeline'
import { usePlayer } from '../../state/player'

const IDLE: EngineState = { position: 0, playing: false, waiting: false, finished: false, failure: null, failedPart: null, part: 0 }

/** The one that plays now: a second one stops it (two recordings are never heard over each other). */
let playing: (() => void) | null = null

type Loaded = { parts: Part[]; spans: Span[]; total: number }

type Source = { files: { id: number; format: string; size: number; part: number; duration: number }[]; chapters: { number: number; title: string; start: number }[]; duration: number }

/** The recording of an edition of the library (`editionId`), or the files of a download that waits for an answer (`downloadId`). */
export function PeekListen({ editionId, downloadId, title }: { editionId?: number; downloadId?: number; title: string }) {
  const { t } = useTranslation()
  const main = usePlayer()
  const first = useRef<HTMLAudioElement>(null)
  const second = useRef<HTMLAudioElement>(null)
  const engine = useRef<PlayerEngine | null>(null)
  const [state, setState] = useState<EngineState>(IDLE)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [problem, setProblem] = useState<'notFound' | 'network' | 'unsupported' | null>(null)
  const [chapters, setChapters] = useState(false)
  // How this card is stopped from outside (another card starts): one function for as long as the card lives.
  const stopper = useRef(() => engine.current?.pause())
  // Whether the files are handed to the engine yet. That happens in the person's tap and not when the card opens: Safari gives
  // an element no data until it was played in a tap, so an element pointed at a file before the tap never fetches it.
  const handed = useRef(false)

  useEffect(() => {
    if (!first.current || !second.current) return
    const made = new PlayerEngine(first.current, second.current, {
      urlOf: (part) => (downloadId !== undefined ? lookContentUrl(downloadId, part.id) : contentUrl(part.id)),
      supported: (part) => browserProbe()(PROBE[part.format] ?? '') !== '',
      onChange: () => setState(made.state),
    })
    engine.current = made
    const mine = stopper.current
    return () => {
      if (playing === mine) playing = null
      made.dispose()
      engine.current = null
    }
  }, [downloadId])

  useEffect(() => {
    let gone = false
    handed.current = false
    engine.current?.stop()
    const asked: Promise<Source> =
      downloadId !== undefined
        ? procureApi.look(downloadId).then((look) => ({ files: look.files, chapters: look.chapters, duration: look.says.duration }))
        : readingApi.edition(editionId ?? 0).then((read) => ({ files: read.edition.files, chapters: read.edition.chapters, duration: read.edition.duration }))
    asked.then(
      (edition) => {
        if (gone) return
        const parts = partsOf(edition.files)
        const total = totalOf(parts) || edition.duration
        if (parts.length === 0) {
          setProblem('notFound')
          return
        }
        if (parts.every((part) => unplayable([part.format], browserProbe()).length > 0)) {
          setProblem('unsupported')
          return
        }
        const spans = spansOf(edition.chapters, total, title)
        setLoaded({ parts, spans, total })
      },
      (error) => !gone && setProblem(error instanceof ApiError && (error.status === 404 || error.status === 409) ? 'notFound' : 'network'),
    )
    return () => {
      gone = true
    }
  }, [editionId, downloadId, title])

  /** Makes the sound possible (in the tap) and opens the recording at `seconds`; true when this opened it. */
  const hand = (seconds: number): boolean => {
    const current = engine.current
    if (!current || !loaded || handed.current) return false
    current.unlock()
    current.load(loaded.parts, seconds)
    handed.current = true
    return true
  }
  const toggle = () => {
    const current = engine.current
    if (!current || !loaded) return
    if (state.playing) {
      current.pause()
      return
    }
    // In the tap: a browser that wants a tap for sound lets the elements play later only if one was made here.
    hand(0)
    playing?.()
    playing = stopper.current
    main.pause()
    current.play()
  }
  const jump = (seconds: number) => {
    const current = engine.current
    if (!current || !loaded) return
    if (!hand(seconds)) current.seek(seconds)
  }

  const total = loaded?.total ?? 0
  const at = Math.min(state.position, total)
  const current = loaded ? Math.max(0, chapterAt(loaded.spans, at)) : 0
  const failure = problem ?? (state.failure ? 'broken' : null)
  return (
    <div className="peek-listen" data-testid="peek-listen">
      {failure ? (
        <p className="text-sm text-muted" role="alert" data-testid="peek-listen-failed">
          {failure === 'unsupported' || failure === 'broken' ? t('review.peek.cannotPlay') : t(`reader.failed.${failure}`)}
        </p>
      ) : (
        <>
          <div className="peek-row">
            <button type="button" className="peek-play" onClick={toggle} disabled={!loaded} aria-label={state.playing ? t('player.pause') : t('player.play')} data-testid="peek-play">
              {state.playing ? <Pause size={18} aria-hidden /> : <Play size={18} aria-hidden />}
            </button>
            <input
              type="range"
              className="peek-range"
              min={0}
              max={Math.max(1, Math.floor(total))}
              step={1}
              value={Math.floor(at)}
              disabled={!loaded}
              aria-label={t('review.peek.position')}
              onChange={(event) => jump(Number(event.target.value))}
              data-testid="peek-range"
            />
            <span className="peek-time" data-testid="peek-time">
              {clock(at)} / {clock(total)}
            </span>
          </div>
          <div className="peek-jumps" role="group" aria-label={t('review.peek.jumps')}>
            {jumpPoints(total).map((point) => (
              <button key={point.key} type="button" className="peek-btn" onClick={() => jump(point.at)} disabled={!loaded} data-jump={point.key}>
                {t(`review.peek.jump.${point.key}`)}
              </button>
            ))}
            {loaded && loaded.spans.length > 1 && (
              <button type="button" className="peek-btn" aria-expanded={chapters} onClick={() => setChapters((now) => !now)} data-testid="peek-chapters-toggle">
                {t('review.peek.chapters', { count: loaded.spans.length })}
              </button>
            )}
          </div>
          {state.waiting && <p className="text-xs text-muted">{t('player.waiting')}</p>}
          {chapters && loaded && (
            <ol className="peek-chapters" data-testid="peek-chapters">
              {loaded.spans.map((span, index) => (
                <li key={span.number}>
                  <button type="button" className={index === current ? 'on' : ''} onClick={() => jump(span.start)}>
                    <span className="peek-chapter-title">{span.title}</span>
                    <span className="peek-chapter-at">{clock(span.start)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
          <p className="text-xs text-muted">{t('review.peek.listenSaved')}</p>
        </>
      )}
      <audio ref={first} hidden preload="none" data-testid="peek-audio" />
      <audio ref={second} hidden preload="none" data-testid="peek-audio" />
    </div>
  )
}
