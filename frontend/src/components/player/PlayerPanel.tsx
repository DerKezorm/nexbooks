/**
 * The panels of the big player: the speed, the sleep timer, the chapters, the bookmarks with a note, and the choices
 * of the jumps. From the side on a large screen, from below on a phone, in the language of the reader's panels (`.seg`, the
 * switch, the rows).
 */
import { Minus, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { BookmarkOut } from '../../api/client'
import { SLEEP_MAX, SLEEP_MIN, SLEEP_MINUTES } from '../../lib/sleepTimer'
import { clampSpeed, clock, SPEED_MAX, SPEED_MIN, SPEED_PRESETS, SPEED_STEP } from '../../lib/timeline'
import { usePlayer, usePlayerLive } from '../../state/player'
import { useConfirm } from '../useConfirm'

export type PanelKind = 'speed' | 'sleep' | 'chapters' | 'marks' | 'settings'

/** "1,25×": a speed as the language writes numbers, at least one place after the point. */
// eslint-disable-next-line react-refresh/only-export-components
export function speedText(speed: number, language: string): string {
  return `${new Intl.NumberFormat(language, { minimumFractionDigits: 1, maximumFractionDigits: 2 }).format(speed)}×`
}

const BACK_CHOICES = [5, 10, 15, 20, 30, 45, 60]
const AHEAD_CHOICES = [10, 15, 20, 30, 45, 60, 90, 120]

export function PlayerPanel({ kind, onClose }: { kind: PanelKind; onClose: () => void }) {
  const { t } = useTranslation()
  const titles: Record<PanelKind, string> = {
    speed: t('player.speed.title'),
    sleep: t('player.sleep.title'),
    chapters: t('player.chapters.title'),
    marks: t('player.marks.title'),
    settings: t('player.settings.title'),
  }
  return (
    <section className={`pl-panel pl-panel-${kind}`} aria-label={titles[kind]} data-testid={`panel-${kind}`}>
      <div className="pl-panel-head">
        <h3>{titles[kind]}</h3>
        <button type="button" className="pl-icon" onClick={onClose} aria-label={t('common.close')}>
          <X size={18} aria-hidden />
        </button>
      </div>
      {kind === 'speed' && <Speed />}
      {kind === 'sleep' && <Sleep onDone={onClose} />}
      {kind === 'chapters' && <Chapters onDone={onClose} />}
      {kind === 'marks' && <Marks />}
      {kind === 'settings' && <Settings />}
    </section>
  )
}

function Speed() {
  const { t, i18n } = useTranslation()
  const player = usePlayer()
  const { speed } = usePlayerLive()
  return (
    <div className="pl-panel-body">
      <div className="seg pl-presets" role="group" aria-label={t('player.speed.presets')}>
        {SPEED_PRESETS.map((value) => (
          <button key={value} type="button" className={Math.abs(speed - value) < 0.001 ? 'on' : ''} onClick={() => player.setSpeed(value)} aria-pressed={Math.abs(speed - value) < 0.001} data-testid={`speed-${value}`}>
            {speedText(value, i18n.language)}
          </button>
        ))}
      </div>
      <div className="pl-row">
        <button type="button" className="pl-icon" onClick={() => player.setSpeed(clampSpeed(speed - SPEED_STEP))} aria-label={t('player.speed.slower')} disabled={speed <= SPEED_MIN}>
          <Minus size={18} aria-hidden />
        </button>
        <input
          type="range"
          min={SPEED_MIN}
          max={SPEED_MAX}
          step={SPEED_STEP}
          value={speed}
          onChange={(event) => player.setSpeed(Number(event.target.value))}
          aria-label={t('player.speed.title')}
          aria-valuetext={speedText(speed, i18n.language)}
          style={{ '--done': `${((speed - SPEED_MIN) / (SPEED_MAX - SPEED_MIN)) * 100}%` } as React.CSSProperties}
          data-testid="speed-slider"
        />
        <button type="button" className="pl-icon" onClick={() => player.setSpeed(clampSpeed(speed + SPEED_STEP))} aria-label={t('player.speed.faster')} disabled={speed >= SPEED_MAX}>
          <Plus size={18} aria-hidden />
        </button>
        <output className="pl-value" data-testid="speed-value">
          {speedText(speed, i18n.language)}
        </output>
      </div>
      <p className="pl-note">{t('player.speed.note')}</p>
    </div>
  )
}

function Sleep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const player = usePlayer()
  const { sleep } = usePlayerLive()
  const [own, setOwn] = useState(20)
  return (
    <div className="pl-panel-body">
      {sleep.kind !== 'off' && (
        <div className="pl-sleep-now" data-testid="sleep-now">
          <p>{sleep.kind === 'minutes' ? t('player.sleep.left', { time: clock(sleep.left) }) : t('player.sleep.atChapterEnd', { time: clock(sleep.left) })}</p>
          <button type="button" className="pl-quiet" onClick={player.sleepOff} data-testid="sleep-off">
            {t('player.sleep.off')}
          </button>
        </div>
      )}
      <div className="pl-chips">
        {SLEEP_MINUTES.map((minutes) => (
          <button
            key={minutes}
            type="button"
            className={`g ${sleep.kind === 'minutes' && sleep.minutes === minutes ? 'on' : ''}`}
            onClick={() => {
              player.sleepFor(minutes)
              onDone()
            }}
            data-testid={`sleep-${minutes}`}
          >
            {t('player.sleep.minutes', { count: minutes })}
          </button>
        ))}
        <button
          type="button"
          className={`g ${sleep.kind === 'chapter' ? 'on' : ''}`}
          onClick={() => {
            player.sleepAtChapterEnd()
            onDone()
          }}
          data-testid="sleep-chapter"
        >
          {t('player.sleep.chapterEnd')}
        </button>
      </div>
      <div className="pl-row">
        <label className="pl-label" htmlFor="pl-sleep-own">
          {t('player.sleep.own')}
        </label>
        <input id="pl-sleep-own" type="range" min={SLEEP_MIN} max={SLEEP_MAX} step={5} value={own} onChange={(event) => setOwn(Number(event.target.value))} style={{ '--done': `${((own - SLEEP_MIN) / (SLEEP_MAX - SLEEP_MIN)) * 100}%` } as React.CSSProperties} />
        <output className="pl-value">{t('player.sleep.minutes', { count: own })}</output>
        <button
          type="button"
          className="go"
          onClick={() => {
            player.sleepFor(own)
            onDone()
          }}
          data-testid="sleep-own"
        >
          {t('player.sleep.start')}
        </button>
      </div>
      <p className="pl-note">{t('player.sleep.note')}</p>
    </div>
  )
}

function Chapters({ onDone }: { onDone: () => void }) {
  const player = usePlayer()
  const { book, chapter } = usePlayerLive()
  const list = useRef<HTMLOListElement>(null)
  // The chapter that plays is in sight when the list opens.
  useEffect(() => {
    list.current?.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView({ block: 'center' })
  }, [])
  if (!book) return null
  return (
    <ol className="pl-list" ref={list}>
      {book.spans.map((span, index) => (
        <li key={span.number}>
          <button
            type="button"
            className={`pl-chap ${index === chapter ? 'on' : ''}`}
            aria-current={index === chapter ? 'true' : undefined}
            onClick={() => {
              player.seek(span.start)
              if (window.matchMedia?.('(max-width: 720px)').matches) onDone()
            }}
            data-testid="chapter-item"
          >
            <span className="n">{index + 1}</span>
            <span className="tt" dir="auto">
              {span.title}
            </span>
            <span className="d">{clock(span.end - span.start)}</span>
          </button>
        </li>
      ))}
    </ol>
  )
}

function Marks() {
  const { t } = useTranslation()
  const player = usePlayer()
  const { bookmarks } = usePlayerLive()
  const { ask, dialog } = useConfirm()
  const [editing, setEditing] = useState<number | null>(null)
  const add = async () => {
    const made = await player.addBookmark()
    if (made) setEditing(made.id)
  }
  const remove = (mark: BookmarkOut) =>
    ask({
      title: t('player.marks.deleteTitle'),
      text: t('player.marks.deleteText', { label: `${clock(mark.position ?? 0)}${mark.chapter_title ? ` · ${mark.chapter_title}` : ''}` }),
      confirm: t('player.marks.deleteConfirm'),
      run: async () => {
        await player.removeBookmark(mark.id)
        if (editing === mark.id) setEditing(null)
      },
    })
  return (
    <div className="pl-panel-body pl-marks">
      <button type="button" className="go" onClick={() => void add()} data-testid="add-mark">
        {t('player.marks.add')}
      </button>
      {bookmarks.length === 0 ? (
        <p className="pl-empty">{t('player.marks.empty')}</p>
      ) : (
        <ul className="pl-list">
          {bookmarks.map((mark) => (
            <li key={mark.id} className="pl-mark" data-testid="mark-item">
              <div className="pl-mark-row">
                <button type="button" className="pl-mark-go" onClick={() => player.seek(mark.position ?? 0)} data-testid="mark-go">
                  <b>{clock(mark.position ?? 0)}</b>
                  <span dir="auto">{mark.chapter_title}</span>
                </button>
                <button type="button" className="pl-icon" onClick={() => remove(mark)} aria-label={t('player.marks.delete')} title={t('player.marks.delete')} data-testid="mark-delete">
                  <Trash2 size={16} aria-hidden />
                </button>
              </div>
              {editing === mark.id ? (
                <NoteEditor mark={mark} onDone={() => setEditing(null)} />
              ) : (
                <button type="button" className={`pl-note-text ${mark.note ? '' : 'is-empty'}`} onClick={() => setEditing(mark.id)} data-testid="mark-note">
                  {mark.note || t('player.marks.addNote')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {dialog}
    </div>
  )
}

function NoteEditor({ mark, onDone }: { mark: BookmarkOut; onDone: () => void }) {
  const { t } = useTranslation()
  const player = usePlayer()
  const [text, setText] = useState(mark.note)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (busy) return
    setBusy(true)
    const ok = await player.saveNote(mark.id, text.trim())
    setBusy(false)
    if (ok) onDone()
  }
  return (
    <form
      className="pl-note-edit"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <textarea value={text} maxLength={500} rows={2} onChange={(event) => setText(event.target.value)} placeholder={t('player.marks.notePlaceholder')} aria-label={t('player.marks.note')} autoFocus data-testid="mark-note-input" />
      <div>
        <button type="submit" className="go" disabled={busy} data-testid="mark-note-save">
          {t('player.marks.save')}
        </button>
        <button type="button" className="pl-quiet" onClick={onDone}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}

function Settings() {
  const { t } = useTranslation()
  const player = usePlayer()
  const { choices } = usePlayerLive()
  return (
    <div className="pl-panel-body">
      <div className="pl-row pl-row-col">
        <span className="pl-label">{t('player.settings.back')}</span>
        <div className="seg small" role="group" aria-label={t('player.settings.back')}>
          {BACK_CHOICES.map((value) => (
            <button key={value} type="button" className={choices.back === value ? 'on' : ''} aria-pressed={choices.back === value} onClick={() => player.setChoices({ back: value })} data-testid={`back-${value}`}>
              {t('player.settings.seconds', { n: value })}
            </button>
          ))}
        </div>
      </div>
      <div className="pl-row pl-row-col">
        <span className="pl-label">{t('player.settings.ahead')}</span>
        <div className="seg small" role="group" aria-label={t('player.settings.ahead')}>
          {AHEAD_CHOICES.map((value) => (
            <button key={value} type="button" className={choices.ahead === value ? 'on' : ''} aria-pressed={choices.ahead === value} onClick={() => player.setChoices({ ahead: value })} data-testid={`ahead-${value}`}>
              {t('player.settings.seconds', { n: value })}
            </button>
          ))}
        </div>
      </div>
      <div className="pl-row">
        <label className="toggle">
          <input type="checkbox" checked={choices.rewind} onChange={(event) => player.setChoices({ rewind: event.target.checked })} data-testid="rewind-toggle" />
          <span />
          {t('player.settings.rewind')}
        </label>
      </div>
      <p className="pl-note">{t('player.settings.rewindNote')}</p>
    </div>
  )
}
