/**
 * "Unterwegs": the books somebody asked for that are not on a shelf yet, at the
 * top of the library. In the shelf they stand as dashed spines of their own ("GESUCHT", or "LÄUFT" with the spine filling
 * from the foot as the download goes); in the grid of covers as covers with a mark. A press opens the page of the book.
 */
import { ArrowRight, Download } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { patternOfToken } from '../../lib/books'
import { useOpenAt } from '../../lib/openBook'
import { coverOfBook } from '../../lib/procure'
import { genreClass, leans, spineColors, spineDims, titleSize } from '../../lib/spine'
import { useIncomingBooks, type IncomingBook } from '../../state/procure'
import { Cover } from '../BookCard'

function seed(token: string): number {
  let hash = 0
  for (let i = 0; i < token.length; i++) hash = (hash * 31 + token.charCodeAt(i)) >>> 0
  return hash
}

function IncomingSpine({ item }: { item: IncomingBook }) {
  const { t } = useTranslation()
  const open = useOpenAt()
  const token = item.book.book ?? ''
  const id = seed(token)
  const like = { id, color: item.book.color || '#8e3b46', pages: 300, tall: (id % 100) / 100, genre: item.book.genre, title: item.book.title, audio_only: false }
  const { t: width, h: height } = spineDims(like)
  const { ink, gold, band } = spineColors(like)
  const style = {
    '--c': like.color,
    '--ink': ink,
    '--gold': gold,
    '--band': band,
    '--t': `${width}px`,
    '--h': `${height}px`,
    '--fs': `${titleSize(like, height).toFixed(1)}px`,
    '--prog': `${Math.round(item.progress)}%`,
  } as CSSProperties
  const classes = ['spine', 'ghost', 'incoming-ghost', item.running ? 'running' : '', genreClass(item.book.genre), leans(like) ? 'tilt' : ''].filter(Boolean).join(' ')
  return (
    <button type="button" className={classes} style={style} data-foreign={token} data-pattern={patternOfToken(token)} aria-label={item.book.title} onClick={() => open(`/discover/book/${token}`, {})}>
      <span className="top-deco" />
      <span className="t" dir="auto">
        {item.book.title}
      </span>
      <span className="a">{item.running ? t('acquire.shelf.running') : t('acquire.shelf.searching')}</span>
    </button>
  )
}

function IncomingCard({ item }: { item: IncomingBook }) {
  const { t } = useTranslation()
  const open = useOpenAt()
  const token = item.book.book ?? ''
  return (
    <button type="button" className={`book inc ${item.running ? 'run' : ''}`} data-foreign={token} onClick={() => open(`/discover/book/${token}`, {})} aria-label={item.book.title}>
      <Cover work={coverOfBook(item.book)} />
      <span className="inmark">
        <Download aria-hidden />
        {item.running ? t('acquire.shelf.markRunning') : t('acquire.shelf.markSearching')}
      </span>
      <b dir="auto">{item.book.title}</b>
      <small dir="auto">{item.book.authors.join(', ')}</small>
    </button>
  )
}

export function IncomingSection({ look, genre }: { look: 'spines' | 'covers'; genre: string | null }) {
  const { t } = useTranslation()
  const items = useIncomingBooks(genre)
  if (items.length === 0) return null
  return (
    <section className="section incoming-sec acq" data-testid="incoming">
      <div className="sec-h">
        <h2>
          {t('acquire.shelf.title')} <small>{items.length}</small>
        </h2>
        <Link className="sec-link" to="/acquire">
          {t('acquire.shelf.link')} <ArrowRight size={14} aria-hidden />
        </Link>
      </div>
      {look === 'spines' ? (
        <div className="shelf">
          {items.map((item) => (
            <IncomingSpine key={item.book.book} item={item} />
          ))}
        </div>
      ) : (
        <div className="shelf-grid">
          {items.map((item) => (
            <IncomingCard key={item.book.book} item={item} />
          ))}
        </div>
      )}
    </section>
  )
}
