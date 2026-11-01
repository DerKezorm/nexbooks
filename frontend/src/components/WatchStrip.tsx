/**
 * "Du beobachtest": the authors the person watches, each as a chip with the day of
 * the next book announced (or its year, when no day is known), under the head of Browse. Nothing is shown for a person who
 * watches nobody.
 */
import { Eye } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { watchApi, type AuthorWatch } from '../api/client'
import { languageName } from '../lib/books'
import { AuthorAvatar } from './AuthorAvatar'

/** "12. Feb. 2027": the day with a short month and the year, as the design shows the next book. */
function shortDay(day: string, language: string): string {
  const parsed = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!parsed) return ''
  try {
    return new Intl.DateTimeFormat(language, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(Number(parsed[1]), Number(parsed[2]) - 1, Number(parsed[3]), 12)))
  } catch {
    return day
  }
}

function Next({ watch }: { watch: AuthorWatch }) {
  const { t, i18n } = useTranslation()
  const next = watch.next
  if (!next) return <>{watch.state === 'waiting' ? t('discover.watching.waiting') : t('discover.watching.nothing')}</>
  // The language is named when the book comes in another language than the one the watch wishes in.
  const other = Boolean(watch.target) && next.language !== watch.target
  const language = languageName(next.language, i18n.language)
  // A book announced for a year without a day: "nächstes Buch 2027", never a day nobody knows.
  if (/^\d{4}$/.test(next.date)) return <>{other ? t('discover.watching.yearIn', { year: next.date, language }) : t('discover.watching.year', { year: next.date })}</>
  const day = shortDay(next.date, i18n.language)
  if (!day) return <>{t('discover.watching.nothing')}</>
  return <>{other ? t('discover.watching.nextIn', { day, language }) : t('discover.watching.next', { day })}</>
}

export function WatchStrip() {
  const { t } = useTranslation()
  const [items, setItems] = useState<AuthorWatch[]>([])
  useEffect(() => {
    const controller = new AbortController()
    watchApi.list(controller.signal).then(
      (found) => setItems(Array.isArray(found.items) ? found.items : []),
      () => undefined,
    )
    return () => controller.abort()
  }, [])
  if (items.length === 0) return null
  return (
    <div className="watchstrip" data-testid="watch-strip">
      <span className="ws-l">
        <Eye aria-hidden size={15} />
        {t('discover.watching.label')}
      </span>
      {items.map((watch) => (
        <Link key={watch.token} className="ws-chip" to={watch.person_id !== null ? `/person/${watch.person_id}` : `/author/${watch.token}`} data-testid="watch-chip">
          <AuthorAvatar hit={watch} size="xs" />
          <span>
            <b dir="auto">{watch.name}</b>
            <small>
              <Next watch={watch} />
            </small>
          </span>
        </Link>
      ))}
    </div>
  )
}
