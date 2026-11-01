/**
 * The shelf of the library: a section for each genre with a heading and its number, the spines standing on boards; the
 * small shelf of a series or an author on the page of a book; and the tip that follows the mouse over a spine.
 */
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import type { WorkBrief } from '../api/client'
import { authorLine } from '../lib/books'
import { GENRES, genreKey, sectionHeight, type Tip } from '../lib/shelf'
import { useIncoming } from '../state/procure'
import { Spine } from './Spine'

type Open = (work: WorkBrief, element: HTMLElement) => void

/** The sections of the library: `counts` are the numbers the server gave for the whole library (a section's heading shows
 * its number before all its books have come). A section far down is not drawn until it comes near. */
export function ShelfSections({ works, counts, found, onOpen }: { works: WorkBrief[]; counts: Record<string, number>; found: number | null; onOpen: Open }) {
  const { t, i18n } = useTranslation()
  const incoming = useIncoming()
  const sections = GENRES.map((genre) => ({ genre, items: works.filter((work) => genreKey(work.genre) === genre) })).filter((section) => section.items.length > 0)
  return (
    <>
      {sections.map(({ genre, items }, index) => (
        <section key={genre} className={`section ${index >= 2 ? 'is-far' : ''}`} data-genre={genre} style={index >= 2 ? ({ '--est': `${sectionHeight(counts[genre] ?? items.length, window.innerWidth)}px` } as CSSProperties) : undefined}>
          <h2>
            {t(`library.genre.${genre}`)} <small>{(counts[genre] ?? items.length).toLocaleString(i18n.language)}</small>
          </h2>
          <div className="shelf">
            {items.map((work) => (
              <Spine key={work.id} work={work} className={found === work.id ? 'found' : ''} onOpen={onOpen} incoming={incoming(work)?.state} />
            ))}
          </div>
        </section>
      ))}
    </>
  )
}

/** The small shelf of the page of a book: the series (the current volume marked) or more of an author. */
export function MiniShelf({ works, current, onOpen, label }: { works: WorkBrief[]; current?: number; onOpen: Open; label: string }) {
  const incoming = useIncoming()
  return (
    <div className="mini-shelf" role="group" aria-label={label} data-testid="mini-shelf">
      {works.map((work) => (
        <Spine key={work.id} work={work} className={work.id === current ? 'current' : ''} onOpen={onOpen} incoming={incoming(work)?.state} />
      ))}
    </div>
  )
}

export function SpineTip({ tip }: { tip: Tip | null }) {
  if (!tip) return null
  const { work, x, y } = tip
  return (
    <div className="tip" role="tooltip" style={{ transform: `translate(${x}px, ${y}px)` }} data-testid="spine-tip">
      <b dir="auto">{work.title}</b>
      <span dir="auto">
        {authorLine(work)}
        {work.series ? ` · ${work.series.name}${work.series.pos !== null ? ` ${work.series.pos}` : ''}` : ''}
      </span>
    </div>
  )
}
