/**
 * The spine of a book on the shelf, built the way the design builds it: the title
 * stands upright in a block of its own, the last name of the author at the foot, a pair of headphones for a book that
 * also exists as a recording, the word HÖRBUCH on the case of a recording alone, and the lines and bands of the genre.
 */
import { memo, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import type { WorkBrief } from '../api/client'
import { authorLine } from '../lib/books'
import { footName, genreClass, leans, spineColors, spineDims, titleSize } from '../lib/spine'
import { AudioIcon } from './BookCard'

type Props = {
  work: WorkBrief
  /** Extra classes: `current` (the book whose page is open), `found` (the search just led here). */
  className?: string
  onOpen?: (work: WorkBrief, element: HTMLElement) => void
  /** Pressing a spine of the page of a book leads to that book; there is no animation of its own. */
  tabIndex?: number
  /** A kind of this book is asked for and not there yet: a band at the foot of the spine. */
  incoming?: 'find' | 'run'
}

export const Spine = memo(function Spine({ work, className = '', onOpen, tabIndex, incoming }: Props) {
  const { t } = useTranslation()
  const { t: width, h: height } = spineDims(work)
  const { ink, gold, band } = spineColors(work)
  const hasAudio = work.editions.some((edition) => edition.kind === 'audio')
  const style = {
    '--c': work.color,
    '--ink': ink,
    '--gold': gold,
    '--band': band,
    '--t': `${width}px`,
    '--h': `${height}px`,
    '--fs': `${titleSize(work, height).toFixed(1)}px`,
  } as CSSProperties
  const author = authorLine(work)
  const classes = ['spine', genreClass(work.genre), work.audio_only ? 'is-audio' : '', leans(work) ? 'tilt' : '', incoming ? 'incoming' : '', className].filter(Boolean).join(' ')
  return (
    <button
      type="button"
      className={classes}
      style={style}
      data-work={work.id}
      tabIndex={tabIndex}
      aria-label={author ? `${work.title}, ${author}` : work.title}
      data-incoming={incoming}
      onClick={(event) => onOpen?.(work, event.currentTarget)}
    >
      <span className="top-deco">{work.audio_only ? t('library.audioCase') : ''}</span>
      {hasAudio && !work.audio_only && (
        <span className="ic">
          <AudioIcon />
        </span>
      )}
      <span className="t" dir="auto">
        {work.title}
      </span>
      <span className="a" dir="auto">
        {footName(work)}
      </span>
    </button>
  )
})
