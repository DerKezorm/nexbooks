/**
 * A book in the grid of covers: the cover (the real picture, or the drawn one in the colour of the
 * book when it has none), title, author, and a chip for each edition (a book symbol and the language for an e-book, a
 * pair of headphones and the variant for a recording; the formats of an e-book beside it).
 */
import { useState, type CSSProperties } from 'react'
import { Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { EditionBrief, WorkBrief } from '../api/client'
import { authorLine, formatsLabel, inkFor, languageChip, patternFor } from '../lib/books'

export function BookIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
      <path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5" />
    </svg>
  )
}

export function AudioIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 15v-3a8 8 0 0 1 16 0v3" />
      <rect x="3" y="14" width="4" height="7" rx="1.5" />
      <rect x="17" y="14" width="4" height="7" rx="1.5" />
    </svg>
  )
}

/** What a cover is drawn from: a book of a library, or a book of a source that is not on a shelf (Discover). */
export type CoverWork = Pick<WorkBrief, 'color' | 'pattern' | 'cover_url' | 'audio_only' | 'title' | 'authors' | 'series'>

/** The cover: the picture the scan made, or one drawn from the metadata (author, title, series) in the book's colour.
 * `mini`: the small one of the search results, which is only the colour (or the picture). */
/** The shape of the box of a cover, and how far a picture may be from it and still fill the box. */
const BOOK_SHAPE = 2 / 3
const SHAPE_SLACK = 0.05

export function Cover({ work, mini = false }: { work: CoverWork; mini?: boolean }) {
  // A picture the server cannot give (the source has none or is busy) falls back to the drawn cover in the same box and
  // colour: no message, no jump. The server fetches the cover of a source when the picture is asked for.
  const [failed, setFailed] = useState<string | null>(null)
  // A picture of another shape than a book (a recording's is square) stands whole in the box, on a blurred copy of itself.
  const [odd, setOdd] = useState<string | null>(null)
  const style = { '--c': work.color, '--ink': inkFor(work.color) } as CSSProperties
  const src = work.cover_url
  const classes = `cover ${work.audio_only ? 'is-audio' : ''} ${mini ? 'mini' : ''} ${src && odd === src ? 'is-fit' : ''}`
  if (src && failed !== src) {
    return (
      <div className={classes} style={style} data-testid="cover-picture">
        {odd === src && <img key="back" className="c-back" src={src} alt="" decoding="async" draggable={false} />}
        <img
          key="front"
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setFailed(src)}
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget
            if (Math.abs(naturalWidth / naturalHeight - BOOK_SHAPE) > SHAPE_SLACK) setOdd(src)
          }}
        />
      </div>
    )
  }
  return (
    <div className={classes} style={{ ...style, ...patternFor(work.color, work.pattern) }} data-testid="cover-drawn">
      <span className="c-author" dir="auto">
        {authorLine(work)}
      </span>
      <span className="c-title" dir="auto">
        {work.title}
      </span>
      {work.series && (
        <span className="c-series" dir="auto">
          {work.series.name}
          {work.series.pos !== null ? ` · ${work.series.pos}` : ''}
        </span>
      )}
      {work.audio_only && (
        <span className="c-audio">
          <AudioIcon />
        </span>
      )}
    </div>
  )
}

function EditionChips({ edition }: { edition: EditionBrief }) {
  const { t } = useTranslation()
  if (edition.kind === 'audio') {
    const variant = edition.variant === 'unabridged' || !edition.variant ? '' : t(`library.variant.${edition.variant}`)
    return (
      <span className="chip" data-kind="audio">
        <AudioIcon />
        {variant}
      </span>
    )
  }
  const language = languageChip(edition.language)
  return (
    <>
      <span className="chip" data-kind="ebook">
        <BookIcon />
        {language}
      </span>
      {edition.formats.length > 0 && (
        <span className="chip" data-kind="formats">
          {formatsLabel(edition)}
        </span>
      )}
    </>
  )
}

/** A book in the grid of covers: pressing it opens the book (the cover turns up into the page). */
export function BookCard({ work, onOpen, incoming }: { work: WorkBrief; onOpen?: (work: WorkBrief, element: HTMLElement) => void; incoming?: { state: 'find' | 'run'; kind: 'ebook' | 'audio' } | null }) {
  const { t } = useTranslation()
  const author = authorLine(work)
  return (
    <button
      type="button"
      className={`book ${incoming ? 'inc' : ''} ${incoming?.state === 'run' ? 'run' : ''}`}
      aria-label={author ? `${work.title}, ${author}` : work.title}
      data-testid="book"
      data-work={work.id}
      onClick={(event) => onOpen?.(work, event.currentTarget)}
    >
      <Cover work={work} />
      {incoming && (
        <span className="inmark" title={t('acquire.shelf.incoming', { kind: t(`procure.releases.kind.${incoming.kind}`) })} data-testid="inmark">
          <Download aria-hidden />
          {t(`procure.releases.kind.${incoming.kind}`)}
        </span>
      )}
      <b dir="auto">{work.title}</b>
      {work.series && (
        <i className="b-series" data-testid="book-series" dir="auto">
          {work.series.name}
          {work.series.pos !== null ? ` · ${work.series.pos}` : ''}
        </i>
      )}
      <small dir="auto">{author}</small>
      <span className="chips">
        {work.editions.map((edition) => (
          <EditionChips key={edition.id} edition={edition} />
        ))}
      </span>
    </button>
  )
}

/** The chips of a work: one for each edition (the language of an e-book with its formats, the variant of a recording). */
export function EditionChipList({ work }: { work: Pick<WorkBrief, 'editions'> }) {
  return (
    <>
      {work.editions.map((edition) => (
        <EditionChips key={edition.id} edition={edition} />
      ))}
    </>
  )
}
