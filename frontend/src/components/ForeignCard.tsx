/**
 * A book of a source in Discover: the cover with the plaque in its corner ("Im Regal"
 * when the book is on the person's shelf, else "Merken" or "Gemerkt"), the title, the author, and in a list the chips of the
 * forms it is known in and the day it came or comes out. In a row (the wish list, more of an author) the card is only cover,
 * title and author; in the volumes of a series it says "Band N" and, for a volume the person lacks, is drawn dashed.
 *
 * The cover and the title open the book; the plaque is a button of its own beside them, not inside them.
 */
import { Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { DiscoverCard, FormBrief } from '../api/client'
import { languageName } from '../lib/books'
import { coverOf, dayText } from '../lib/discover'
import { acquireBookOfCard } from '../lib/procure'
import { useProcure } from '../state/procure'
import { useWish } from '../state/wish'
import { AudioIcon, BookIcon, Cover } from './BookCard'
import { CheckIcon, MarkIcon } from './icons'

type Mode = 'grid' | 'row' | 'vol'

export function Chips({ forms }: { forms: FormBrief[] }) {
  const { t } = useTranslation()
  const seen = new Set<string>()
  return (
    <>
      {forms
        .filter((form) => {
          const key = `${form.kind}|${form.language}|${form.variant}`
          if (seen.has(key)) return false
          seen.add(key)
          return true
        })
        .map((form) =>
          form.kind === 'audio' ? (
            <span key={`a${form.variant}${form.language}`} className="chip" data-kind="audio">
              <AudioIcon />
              {form.variant && form.variant !== 'unabridged' ? t(`library.variant.${form.variant}`) : ''}
            </span>
          ) : (
            <span key={`e${form.language}`} className="chip" data-kind="ebook">
              <BookIcon />
              {form.language ? form.language.slice(0, 2).toUpperCase() : ''}
            </span>
          ),
        )}
    </>
  )
}

export function ForeignCard({
  card,
  mode = 'grid',
  when,
  onOpen,
}: {
  card: DiscoverCard
  mode?: Mode
  /** In a list of new or coming books: "erschienen am 9. Okt." */
  when?: 'new' | 'soon'
  onOpen: (card: DiscoverCard) => void
}) {
  const { t, i18n } = useTranslation()
  const wish = useWish()
  const procure = useProcure()
  const wished = wish.wished(card)
  const viaText = (name: string, why: string) => {
    const [code, language] = why.split(':')
    const reason = code ? t(`discover.wish.why.${code}`, { language: language ? languageName(language, i18n.language) : '', defaultValue: '' }) : ''
    return reason ? t('discover.wish.viaWhy', { name, why: reason }) : t('discover.wish.via', { name })
  }
  // What was asked for: a book that is wanted shows that in the place of "Merken".
  const asked = procure.allowed ? procure.kindsOf({ token: card.token }) : []
  const loading = asked.some((item) => item.download)
  const owned = card.owned_work_id !== null
  const vol = mode === 'vol'
  const missing = vol && !owned
  const classes = ['dcard', mode === 'grid' ? 'grid-item' : '', mode === 'grid' && owned ? 'is-owned' : '', vol && !owned && card.upcoming ? 'missing soon' : vol && !owned ? 'missing' : ''].filter(Boolean).join(' ')
  const handle = { 'data-work': owned ? card.owned_work_id : undefined, 'data-foreign': card.token } as const
  const author = card.authors.join(', ')
  const dateText = when ? t(`discover.when.${when}`, { date: dayText(card.released, i18n.language) }) : ''
  return (
    <div className={classes} data-testid="dcard" data-token={card.token}>
      {mode === 'grid' && card.rank !== null && (
        <span className="rk" data-testid="rank">
          {card.rank}
        </span>
      )}
      <span className="cv">
        <button type="button" className="dopen" aria-label={author ? `${card.title}, ${author}` : card.title} onClick={() => onOpen(card)} {...handle}>
          <Cover work={coverOf(card)} />
        </button>
        {owned ? (
          <span className="badge owned" data-testid="badge-owned">
            <CheckIcon />
            {t('discover.badge.owned')}
          </span>
        ) : asked.length > 0 ? (
          <Link className={`badge bes ${loading ? 'run' : ''}`} to={loading ? '/acquire?reiter=running' : '/acquire'} data-testid="badge-acquire" title={t('acquire.title')}>
            <Download aria-hidden />
            {loading ? t('acquire.entry.loading') : t('acquire.entry.wished')}
          </Link>
        ) : (
          <button
            type="button"
            className={`badge ${wished ? 'on' : ''}`}
            data-wish={card.token}
            aria-pressed={wished}
            title={t('discover.badge.wish')}
            onClick={() => void wish.toggle(card)}
          >
            <MarkIcon />
            {wished ? t('discover.badge.wished') : t('discover.badge.wish')}
          </button>
        )}
      </span>
      {vol && (
        <span className="vol">
          {t('discover.gap.vol', { n: card.series_pos ?? '' })}
          {missing ? ` · ${t(card.upcoming ? 'discover.gap.volSoon' : 'discover.gap.volMissing')}` : ''}
        </span>
      )}
      {/* The title opens the book, too; the cover is the control that has a name for the keys and for a screen reader. */}
      <div className="dtext" role="presentation" onClick={() => onOpen(card)}>
        <b dir="auto">{card.title}</b>
        {!vol && <small dir="auto">{author}</small>}
        {vol && card.upcoming && <small className="soon-date">{t('discover.gap.releases', { date: dayText(card.released, i18n.language, true) })}</small>}
        {/* A book the watching of an author put on the list says so, and why it is not wished ("angekündigt auf Englisch"). */}
        {mode === 'row' && wish.via[card.token] && (
          <small className="via" dir="auto" data-testid="via-watch">
            {viaText(wish.via[card.token], wish.viaWhy[card.token] ?? '')}
          </small>
        )}
      </div>
      {(mode === 'row' || mode === 'vol') && procure.allowed && !owned && (
        asked.length > 0 ? (
          <span className="mini-act done" data-testid="acquire-done">
            <CheckIcon />
            {loading ? t('acquire.entry.loading') : t('acquire.entry.wished')}
          </span>
        ) : (
          <button type="button" className="mini-act" onClick={() => procure.open([acquireBookOfCard(card)])} data-procure={card.token}>
            <Download aria-hidden />
            {t('acquire.entry.get')}
          </button>
        )
      )}
      {mode === 'grid' && (
        <span className="chips">
          <Chips forms={card.forms} />
          {dateText && <span className="when">{dateText}</span>}
        </span>
      )}
    </div>
  )
}
