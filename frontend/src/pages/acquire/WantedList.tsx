/** "Gesucht" as a table: one row for each wish, small, so that hundreds of them can be looked over. A small cover, the book with
 * its author, the kind and language, where the wish stands (with a link to the reasons a search found nothing), when it was
 * searched last and when it is tried next, who wished it, and the three things a person can do as small buttons. The columns
 * can be put in order, and only the rows near the screen are drawn (`useRowWindow`), so 500 wishes weigh no more than 20. On a
 * phone only the book, where it stands and the buttons remain. */
import { Languages, ListChecks, Search, Trash2 } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { WishOut } from '../../api/client'
import { ago, whenText } from '../../lib/ago'
import { useRowWindow } from '../../lib/useRowWindow'
import { firstDirection, sortWishes, type WishSort } from '../../lib/wishList'
import { BookLink, KindChip, MiniCover } from './bits'

/** The height of a row: desktop and phone. */
const ROW_PX = 60
const ROW_PHONE_PX = 68

export type WishLook = { tone: string; icon: ReactNode; label: string; sub: string }

type Props = {
  wishes: WishOut[]
  stateOf: (wish: WishOut) => WishLook
  /** The wish is under way, so that a new search makes no sense. */
  busyOf: (wish: WishOut) => boolean
  working: boolean
  notOut: (wish: WishOut) => string | undefined
  onSearch: (wish: WishOut) => void
  onReleases: (wish: WishOut) => void
  onRelanguage: (wish: WishOut) => void
  onRemove: (wish: WishOut) => void
  onReasons: (wish: WishOut) => void
}

export function WantedList({ wishes, stateOf, busyOf, working, notOut, onSearch, onReleases, onRelanguage, onRemove, onReasons }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [order, setOrder] = useState<{ sort: WishSort | null; dir: 'asc' | 'desc' }>({ sort: null, dir: 'asc' })
  const sorted = useMemo(() => sortWishes(wishes, order.sort, order.dir, language), [wishes, order, language])
  const { table, phone, row, from, to, before, after } = useRowWindow(sorted.length, ROW_PX, ROW_PHONE_PX)
  const shown = sorted.slice(from, to)
  const choose = (sort: WishSort) => setOrder((now) => (now.sort === sort ? { sort, dir: now.dir === 'asc' ? 'desc' : 'asc' } : { sort, dir: firstDirection(sort) }))

  const header = (sort: WishSort, className = '') => {
    const active = order.sort === sort
    const name = t(`acquire.wanted.list.col.${sort}`)
    return (
      <th key={sort} scope="col" className={`${className} wl-${sort}`} aria-sort={active ? (order.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button type="button" onClick={() => choose(sort)} aria-label={`${t('library.list.sortBy', { column: name })}${active ? `, ${t(order.dir === 'asc' ? 'library.list.asc' : 'library.list.desc')}` : ''}`} data-sort={sort}>
          {name}
          {active && <span aria-hidden>{order.dir === 'asc' ? '↑' : '↓'}</span>}
        </button>
      </th>
    )
  }

  return (
    <div className="list-wrap wl-wrap" data-testid="wanted-list">
      <table className="list-table wl-table" ref={table} aria-label={t('acquire.wanted.list.label')} aria-rowcount={sorted.length + 1}>
        <thead>
          <tr>
            <th className="col-cover" aria-hidden />
            {header('title')}
            {header('kind', 'wopt2')}
            {header('state')}
            {header('last', 'wopt')}
            {header('next', 'wopt')}
            {header('by', 'wopt3')}
            <th scope="col" className="wl-act">
              <span className="wl-hidden">{t('acquire.wanted.list.col.act')}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {before > 0 && (
            <tr className="pad" aria-hidden style={{ height: before }}>
              <td />
            </tr>
          )}
          {shown.map((wish, offset) => {
            const state = stateOf(wish)
            const busy = busyOf(wish)
            const languageOff = wish.state === 'idle' && wish.detail?.reason === 'language_off'
            const reasons = (wish.state === 'not_found' || wish.state === 'capped') && wish.detail?.reasons?.length ? wish.detail.reasons : []
            const series = wish.book.series ? `${wish.book.series}${wish.book.series_pos != null ? ` ${wish.book.series_pos}` : ''}` : ''
            const who = wish.via
              ? t('acquire.wanted.via', { name: wish.via, ago: ago(wish.created_at, language, t('acquire.justNow')) })
              : t('acquire.wanted.by', { name: wish.by, ago: ago(wish.created_at, language, t('acquire.justNow')) })
            const named = (key: string) => t(`acquire.wanted.list.${key}`, { title: wish.book.title })
            return (
              <tr key={wish.id} data-wish={wish.id} data-state={wish.state} aria-rowindex={from + offset + 2} style={{ height: row }}>
                <td className="col-cover">
                  <MiniCover book={wish.book} />
                </td>
                <th scope="row" className="col-title">
                  <span className="lt-title" dir="auto">
                    <BookLink book={wish.book} />
                  </span>
                  <small dir="auto">{[wish.book.authors.join(', '), series].filter(Boolean).join(' · ')}</small>
                  {phone && <small className="wl-phone-state">{state.label}</small>}
                </th>
                <td className="wopt2 wl-kind">
                  <span className="chips">
                    <KindChip kind={wish.kind} language={wish.language} />
                  </span>
                </td>
                <td className="wl-state-cell">
                  <div className={`wl-state t-${state.tone}`} title={[state.label, state.sub].filter(Boolean).join('. ')}>
                    <span className="si">{state.icon}</span>
                    <div>
                      <div className="wl-line">
                        <b>{state.label}</b>
                        {reasons.length > 0 && (
                          <button type="button" className="link-btn" onClick={() => onReasons(wish)} data-act="reasons">
                            {t('acquire.wanted.list.reasons')}
                          </button>
                        )}
                      </div>
                      <span className="wl-sub">{state.sub}</span>
                    </div>
                  </div>
                </td>
                <td className="wopt wl-last">{wish.searched_at ? ago(wish.searched_at, language, t('acquire.justNow')) : ''}</td>
                <td className="wopt wl-next">{wish.next_try_at ? whenText(wish.next_try_at, language) : ''}</td>
                <td className="wopt3 wl-by">
                  <span className="clamp" dir="auto" title={who}>
                    {who}
                  </span>
                </td>
                <td className="wl-act">
                  <div className="wl-buttons">
                    {languageOff ? (
                      <button type="button" className="bbtn small icon primary" disabled={working} onClick={() => onRelanguage(wish)} title={t('acquire.wanted.relanguage')} aria-label={`${t('acquire.wanted.relanguage')}: ${wish.book.title}`} data-act="relanguage">
                        <Languages aria-hidden className="ic" />
                      </button>
                    ) : (
                      <>
                        <button type="button" className="bbtn small icon primary" disabled={busy || working} onClick={() => onSearch(wish)} title={notOut(wish) ?? t('acquire.wanted.searchNow')} aria-label={named('searchFor')} data-act="search">
                          <Search aria-hidden className="ic" />
                        </button>
                        <button type="button" className="bbtn small icon" onClick={() => onReleases(wish)} title={t('procure.releases.open')} aria-label={named('releasesOf')} data-act="releases">
                          <ListChecks aria-hidden className="ic" />
                        </button>
                      </>
                    )}
                    <button type="button" className="bbtn small icon" onClick={() => onRemove(wish)} title={t('acquire.wanted.remove.title')} aria-label={named('removeFor')} data-act="remove">
                      <Trash2 aria-hidden className="ic" />
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
          {after > 0 && (
            <tr className="pad" aria-hidden style={{ height: after }}>
              <td />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
