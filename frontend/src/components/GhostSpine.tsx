/**
 * The spine of a volume the person does not have: drawn like a spine of the shelf, but
 * dashed and see-through, with "Band N · title" on it and BALD (a volume yet to come, in the accent colour) or FEHLT at its
 * foot. It stands in the small shelf of a series on the page of a book and in the card of the gaps.
 */
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import type { DiscoverCard } from '../api/client'
import { patternOfToken } from '../lib/books'
import { genreClass, leans, spineColors, spineDims, titleSize } from '../lib/spine'

/** A number from a token that is the same every time: the spine of a volume that is not on a shelf has no id of its own. */
function seed(token: string): number {
  let hash = 0
  for (let i = 0; i < token.length; i++) hash = (hash * 31 + token.charCodeAt(i)) >>> 0
  return hash
}

export function GhostSpine({ book, className = '', onOpen }: { book: DiscoverCard; className?: string; onOpen?: (card: DiscoverCard) => void }) {
  const { t } = useTranslation()
  const id = seed(book.token)
  const like = { id, color: book.color, pages: 300, tall: (id % 100) / 100, genre: book.genre, title: book.title, audio_only: false }
  const { t: width, h: height } = spineDims(like)
  const { ink, gold, band } = spineColors(like)
  const style = {
    '--c': book.color,
    '--ink': ink,
    '--gold': gold,
    '--band': band,
    '--t': `${width}px`,
    '--h': `${height}px`,
    '--fs': `${titleSize(like, height).toFixed(1)}px`,
  } as CSSProperties
  const label = t('discover.ghost.label', { n: book.series_pos ?? '', title: book.title })
  const classes = ['spine', 'ghost', book.upcoming ? 'soon' : '', genreClass(book.genre), leans(like) ? 'tilt' : '', className].filter(Boolean).join(' ')
  return (
    <button type="button" className={classes} style={style} data-foreign={book.token} aria-label={label} data-pattern={patternOfToken(book.token)} onClick={() => onOpen?.(book)}>
      <span className="top-deco" />
      <span className="t" dir="auto">
        {label}
      </span>
      <span className="a">{t(book.upcoming ? 'discover.ghost.soon' : 'discover.ghost.missing')}</span>
    </button>
  )
}
