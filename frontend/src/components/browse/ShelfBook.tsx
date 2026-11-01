/**
 * A book on a shelf: the cover standing on its board, the title, the author, a small line
 * (the day it came out, the prize, the pages), a plaque in the corner when the book is on the person's shelf, asked for or
 * on their wish list, and a ribbon when it won a prize. The whole card is one button: a click opens the page of the book; the
 * mouse resting on it takes it in the hand (`Hand.tsx`).
 */
import { Award, Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { ShelfCard } from '../../api/client'
import { coverOf } from '../../lib/discover'
import { useProcure } from '../../state/procure'
import { useWish } from '../../state/wish'
import { Cover } from '../BookCard'
import { CheckIcon, MarkIcon } from '../icons'
import { useHand } from './Hand'

export function ShelfBook({ card, meta = '' }: { card: ShelfCard; meta?: string }) {
  const { t } = useTranslation()
  const hand = useHand()
  const wish = useWish()
  const procure = useProcure()
  const owned = card.owned_work_id !== null
  const asked = procure.allowed ? procure.kindsOf({ token: card.token }) : []
  const loading = asked.some((item) => item.download)
  const author = card.authors.join(', ')
  return (
    <button
      type="button"
      className={`bcard ${owned ? 'is-owned' : ''} ${hand.heldToken === card.token ? 'held' : ''}`}
      aria-label={author ? t('browse.card.label', { title: card.title, author }) : card.title}
      data-testid="bcard"
      data-token={card.token}
      {...hand.bind(card)}
    >
      <span className="bcv">
        {owned ? (
          <span className="bbadge owned" data-testid="badge-owned">
            <CheckIcon />
            {t('discover.badge.owned')}
          </span>
        ) : asked.length > 0 ? (
          <span className="bbadge want" data-testid="badge-asked">
            <Download aria-hidden />
            {loading ? t('browse.card.loading') : t('browse.card.wanted')}
          </span>
        ) : wish.wished(card) ? (
          <span className="bbadge" data-testid="badge-wished">
            <MarkIcon />
            {t('discover.badge.wished')}
          </span>
        ) : null}
        {card.award && (
          <span className="tribbon" title={card.award}>
            <Award aria-hidden />
          </span>
        )}
        <Cover work={coverOf(card)} />
      </span>
      <b dir="auto">{card.title}</b>
      <small dir="auto">{author}</small>
      {meta && <small className="bmeta">{meta}</small>}
    </button>
  )
}
