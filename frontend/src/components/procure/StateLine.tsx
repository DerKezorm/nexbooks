/** Where a kind of a book stands in the fetching: one line that leads to the page
 * "Beschaffen", "Wird beschafft · lädt 40 %", "Gesucht · nächster Versuch in 3 Std", "Upgrade gesucht · hat MP3 128,
 * sucht bis FLAC". */
import { ArrowUp, Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { whenText } from '../../lib/ago'
import { dayText } from '../../lib/discover'
import { hoursUntil } from '../../lib/procure'
import type { KindState } from '../../state/procure'

export function StateLine({ state }: { state: KindState }) {
  const { t, i18n } = useTranslation()
  const { wish, download } = state
  const format = (code: string | undefined) => (code ? (i18n.exists(`procure.formats.name.${code}`) ? t(`procure.formats.name.${code}`) : code.toUpperCase()) : '')
  let text: string
  let tone = 'quiet'
  let bar: number | null = null
  let to = '/acquire'
  let Icon = Download
  if (download) {
    const percent = download.phase === 'downloading' && download.progress != null ? ` ${Math.round(download.progress)} %` : ''
    text = t('acquire.line.loading', { phase: `${t(`acquire.running.phase.${download.phase}`)}${percent}` })
    tone = 'info'
    bar = download.phase === 'downloading' ? (download.progress ?? 0) : null
    to = '/acquire?reiter=running'
  } else if (wish?.state === 'upgrade') {
    text = t('acquire.line.upgrade', { have: format(wish.detail?.have), goal: format(wish.detail?.goal) })
    tone = 'upg'
    Icon = ArrowUp
  } else if (wish?.state === 'searching') {
    text = t('acquire.line.searching')
    tone = 'info'
  } else if (wish?.state === 'not_found') {
    text = t('acquire.line.notFound', { count: hoursUntil(wish.next_try_at) })
  } else if (wish?.state === 'waiting') {
    text = t('acquire.line.waiting', { date: dayText(wish.release_date, i18n.language, true) })
    tone = 'soft'
  } else if (wish?.state === 'capped') {
    const until = wish.detail?.until ?? wish.next_try_at
    text = until ? t('acquire.line.cappedUntil', { time: whenText(until, i18n.language) }) : t('acquire.line.capped')
    tone = 'warn'
  } else {
    text = t('acquire.line.plain')
  }
  return (
    <Link className={`bes-line t-${tone}`} to={to} data-testid="state-line" data-kind={state.kind}>
      <Icon aria-hidden />
      <span>{text}</span>
      {bar !== null && (
        <span className="pg" aria-hidden>
          <i style={{ width: `${bar}%` }} />
        </span>
      )}
    </Link>
  )
}
