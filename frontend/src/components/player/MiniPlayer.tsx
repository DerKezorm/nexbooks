/**
 * The small player at the bottom of every page: the cover, the title and the chapter, the jump back, play and pause,
 * and a thin line of how far the chapter is. A tap on the title opens the big player. It stays while the person moves between
 * the pages of the app; it is not shown over the reader of e-books nor on the pages before the sign-in, and it steps aside
 * while a dialog is open (its buttons would lie over the dialog's on a phone).
 */
import { Pause, Play, X } from 'lucide-react'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'

import { clock } from '../../lib/timeline'
import { usePlayer, usePlayerLive } from '../../state/player'
import { Cover } from '../BookCard'
import { JumpIcon } from './icons'

/** Pages that are not part of the app around the player: the reader of e-books and everything before the sign-in. */
const AWAY = /^\/(read|login|setup|invite|forgot|reset|mail)(\/|$)/

export function MiniPlayer() {
  const { t } = useTranslation()
  const location = useLocation()
  const player = usePlayer()
  const { book, engine, expanded, chapter, choices, loading, hint } = usePlayerLive()
  const shown = book !== null && !expanded && !AWAY.test(location.pathname)

  // The pages leave room at the bottom while it is there.
  useEffect(() => {
    document.body.classList.toggle('has-player', shown)
    return () => document.body.classList.remove('has-player')
  }, [shown])

  if (!shown || !book) return null
  const span = book.spans[chapter]
  const inChapter = span ? Math.min(1, Math.max(0, (engine.position - span.start) / Math.max(0.1, span.end - span.start))) : 0
  const playing = engine.playing
  return (
    <div className="mp" role="region" aria-label={t('player.region')} data-testid="mini-player" data-playing={playing ? 'yes' : 'no'} data-position={engine.position.toFixed(2)}>
      <span className="mp-line" aria-hidden>
        <i style={{ width: `${inChapter * 100}%` }} />
      </span>
      <button type="button" className="mp-open" onClick={player.expand} aria-label={t('player.open', { title: book.work.title })} data-testid="mini-open">
        <span className="mp-cover">
          <Cover work={book.work} mini />
        </span>
        <span className="mp-text">
          <b dir="auto">{book.work.title}</b>
          <small dir="auto">
            {engine.failure ? t(`player.failed.short.${engine.failure}`) : `${span?.title ?? ''} · ${clock(engine.position - (span?.start ?? 0))}`}
          </small>
        </span>
      </button>
      <button type="button" className="mp-btn mp-wide" onClick={() => player.skip(-choices.back)} aria-label={t('player.back', { n: choices.back })} title={t('player.back', { n: choices.back })} disabled={loading}>
        <JumpIcon dir="back" seconds={choices.back} />
      </button>
      <button type="button" className="mp-play" onClick={hint ? player.expand : player.toggle} aria-label={playing ? t('player.pause') : t('player.play')} title={playing ? t('player.pause') : t('player.play')} disabled={loading || engine.failure !== null} data-testid="mini-play">
        {playing ? <Pause size={22} aria-hidden fill="currentColor" /> : <Play size={22} aria-hidden fill="currentColor" />}
      </button>
      <button type="button" className="mp-btn mp-wide" onClick={() => player.skip(choices.ahead)} aria-label={t('player.ahead', { n: choices.ahead })} title={t('player.ahead', { n: choices.ahead })} disabled={loading}>
        <JumpIcon dir="ahead" seconds={choices.ahead} />
      </button>
      <button type="button" className="mp-btn" onClick={player.close} aria-label={t('player.close')} title={t('player.close')} data-testid="mini-close">
        <X size={18} aria-hidden />
      </button>
    </div>
  )
}
