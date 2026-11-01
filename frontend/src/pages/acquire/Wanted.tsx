/** "Gesucht": what is wished and not on its way yet, with where it stands, why a
 * search found nothing, when the next try is, and the three things a person can do: search now, see the releases, take the
 * wish away (asks first). As a table (the first look; hundreds of wishes stay readable) or as cards. */
import { ArrowUp, Calendar, Clock, Pause, Search, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { procureApi, type ProcureCounts, type WishOut } from '../../api/client'
import { ReleasesDialog } from '../../components/procure/ReleasesDialog'
import { Dialog } from '../../components/Dialog'
import { GridIcon, ListIcon } from '../../components/icons'
import { useConfirm } from '../../components/useConfirm'
import { ago, whenText } from '../../lib/ago'
import { dayText } from '../../lib/discover'
import { languageName } from '../../lib/books'
import { errorText } from '../../lib/errors'
import { hoursUntil } from '../../lib/procure'
import { iso } from '../../lib/say'
import { useDo } from './hooks'
import { BookLink, KindChip, MiniCover } from './bits'
import { WantedList } from './WantedList'

/** The look the person chose, kept in this browser only. */
const LOOK_KEY = 'nexbooks.acquire.wanted'
type Look = 'cards' | 'list'

function readLook(): Look {
  try {
    return window.localStorage.getItem(LOOK_KEY) === 'cards' ? 'cards' : 'list'
  } catch {
    return 'list'
  }
}
function keepLook(look: Look): void {
  try {
    window.localStorage.setItem(LOOK_KEY, look)
  } catch {
    // A browser that keeps nothing starts with the list next time.
  }
}

type Tone = 'info' | 'quiet' | 'soft' | 'warn' | 'upg'

export function Wanted({ wishes, counts, onChanged }: { wishes: WishOut[]; counts: ProcureCounts; onChanged: () => Promise<void> }) {
  const { t, i18n } = useTranslation()
  const [mine, setMine] = useState(false)
  const [releases, setReleases] = useState<WishOut | null>(null)
  const [reasonsOf, setReasonsOf] = useState<WishOut | null>(null)
  const [look, setLook] = useState<Look>(readLook)
  const choose = (next: Look) => {
    setLook(next)
    keepLook(next)
  }
  const confirm = useConfirm()
  const doing = useDo(onChanged)
  const shown = wishes.filter((wish) => wish.state !== 'loading' && (!mine || wish.mine))
  const format = (code: string | undefined) => (code ? (i18n.exists(`procure.formats.name.${code}`) ? t(`procure.formats.name.${code}`) : code.toUpperCase()) : '')

  // The wishes that wait for the end of a limit of the day, and when the first of them goes on.
  const capped = shown.filter((wish) => wish.state === 'capped')
  const cappedTimes = capped.map((wish) => wish.detail?.until ?? wish.next_try_at).filter((value): value is string => !!value).sort()
  const cappedTime = cappedTimes.length > 0 ? whenText(cappedTimes[0], i18n.language) : ''

  /** "Zuletzt gesucht vor 2 Stunden." for a wish that was searched. */
  const searchedText = (wish: WishOut) => (wish.searched_at ? t('acquire.wanted.state.last', { ago: ago(wish.searched_at, i18n.language, t('acquire.justNow')) }) : '')

  const stateOf = (wish: WishOut): { tone: Tone; icon: ReactNode; label: string; sub: string } => {
    const detail = wish.detail ?? {}
    switch (wish.state) {
      case 'searching':
        return { tone: 'info', icon: <i className="spin" aria-hidden />, label: t('acquire.wanted.state.searching.label'), sub: wish.waiting_for_a_place ? t('acquire.wanted.state.searching.place') : t('acquire.wanted.state.searching.sub', { count: counts.indexers }) }
      case 'not_found': {
        const reason = wish.handing_over_again ? t('acquire.wanted.state.not_found.handover') : detail.load_error ? t('acquire.wanted.state.not_found.loadError', { why: errorText(detail.load_error) }) : detail.error ? t(`acquire.wanted.state.not_found.error.${detail.error}`, { defaultValue: t('acquire.wanted.state.not_found.error.other') }) : ''
        const next = wish.handing_over_again ? '' : t('acquire.wanted.state.next', { count: hoursUntil(wish.next_try_at) })
        // Releases only doubts stand against: a person can look at them here, nothing asks them to.
        const look = detail.look ? t('acquire.wanted.state.not_found.look', { count: detail.look }) : ''
        return { tone: 'quiet', icon: <Clock aria-hidden className="ic" />, label: t('acquire.wanted.state.not_found.label'), sub: [reason, look, next, searchedText(wish)].filter(Boolean).join(' ') }
      }
      case 'waiting':
        return { tone: 'soft', icon: <Calendar aria-hidden className="ic" />, label: t('acquire.wanted.state.waiting.label'), sub: t('acquire.wanted.state.waiting.sub', { date: dayText(wish.release_date, i18n.language, true) }) }
      case 'capped': {
        const time = whenText(detail.until ?? wish.next_try_at ?? '', i18n.language)
        const sub = !detail.indexer
          ? t('acquire.wanted.state.capped.unknown')
          : detail.code === 'indexer_share_used'
            ? t('acquire.wanted.state.capped.share', { name: detail.indexer })
            : detail.code === 'indexer_daily_limit'
              ? t(detail.limit ? 'acquire.wanted.state.capped.daily' : 'acquire.wanted.state.capped.dailyFree', { name: detail.indexer, limit: (detail.limit ?? 0).toLocaleString(i18n.language) })
              : t('acquire.wanted.state.capped.sub', { name: detail.indexer })
        return { tone: 'warn', icon: <Pause aria-hidden className="ic" />, label: time ? t('acquire.wanted.state.capped.label', { time }) : t('acquire.wanted.state.capped.labelOpen'), sub }
      }
      case 'upgrade':
        if (detail.goal_language && detail.have_language) {
          // A wish for "the languages allowed" has its book in a lower one: it looks for the first.
          const said = { have: languageName(detail.have_language, i18n.language) || detail.have_language, goal: languageName(detail.goal_language, i18n.language) || detail.goal_language, count: hoursUntil(wish.next_try_at) }
          return { tone: 'upg', icon: <ArrowUp aria-hidden className="ic" />, label: t('acquire.wanted.state.upgrade.label'), sub: [t('acquire.wanted.state.upgrade.language', said), searchedText(wish)].filter(Boolean).join(' ') }
        }
        return { tone: 'upg', icon: <ArrowUp aria-hidden className="ic" />, label: t('acquire.wanted.state.upgrade.label'), sub: [t('acquire.wanted.state.upgrade.sub', { have: format(detail.have), goal: format(detail.goal), count: hoursUntil(wish.next_try_at) }), searchedText(wish)].filter(Boolean).join(' ') }
      default:
        return { tone: 'quiet', icon: <Pause aria-hidden className="ic" />, label: t('acquire.wanted.state.idle.label'), sub: detail.passing ? t('acquire.wanted.state.idle.passing', { count: detail.passing }) : t(`acquire.wanted.state.idle.${detail.reason ?? 'plain'}`, { defaultValue: t('acquire.wanted.state.idle.plain'), language: languageName(detail.language ?? wish.language, i18n.language) || (detail.language ?? wish.language) }) }
    }
  }

  const remove = (wish: WishOut) =>
    confirm.ask({
      title: t('acquire.wanted.remove.title'),
      text: t('acquire.wanted.remove.text', { title: wish.book.title, kind: t(`procure.releases.kind.${wish.kind}`) }),
      confirm: t('acquire.wanted.remove.confirm'),
      run: async () => {
        await procureApi.removeWish(wish.id)
        await onChanged()
      },
    })

  return (
    <>
      <div className="bes-top">
        {shown.length > 0 ? <p className="bes-count">{t('acquire.wanted.count', { count: shown.length })}</p> : <span />}
        <div className="bes-tools">
          <div className="seg small bes-seg" role="group" aria-label={t('acquire.scope.aria')}>
            <button type="button" className={mine ? '' : 'on'} onClick={() => setMine(false)} data-mine="0">
              {t('acquire.scope.all')}
            </button>
            <button type="button" className={mine ? 'on' : ''} onClick={() => setMine(true)} data-mine="1">
              {t('acquire.scope.mine')}
            </button>
          </div>
          <div className="seg small bes-seg" role="group" aria-label={t('acquire.wanted.view.aria')}>
            <button type="button" className={look === 'cards' ? 'on' : ''} aria-pressed={look === 'cards'} onClick={() => choose('cards')} data-look="cards">
              <GridIcon />
              {t('acquire.wanted.view.cards')}
            </button>
            <button type="button" className={look === 'list' ? 'on' : ''} aria-pressed={look === 'list'} onClick={() => choose('list')} data-look="list">
              <ListIcon />
              {t('acquire.wanted.view.list')}
            </button>
          </div>
        </div>
      </div>
      {capped.length > 0 && (
        <div className="note warn" data-testid="wanted-capped">
          <Pause aria-hidden className="ic" />
          <p>{t(cappedTime ? 'acquire.wanted.cappedMany' : 'acquire.wanted.cappedManyOpen', { count: capped.length, time: cappedTime })}</p>
        </div>
      )}
      {doing.problem && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      {shown.length === 0 ? (
        <p className="empty" data-testid="wanted-empty">
          {t('acquire.wanted.empty')}
        </p>
      ) : look === 'list' ? (
        <WantedList
          wishes={shown}
          stateOf={stateOf}
          busyOf={(wish) => wish.state === 'waiting' || (wish.state === 'searching' && !wish.waiting_for_a_place)}
          working={doing.busy}
          notOut={(wish) => (wish.state === 'waiting' ? t('acquire.wanted.notOut', { date: dayText(wish.release_date, i18n.language, true) }) : undefined)}
          onSearch={(wish) => void doing.run(() => procureApi.searchWish(wish.id))}
          onReleases={setReleases}
          onRelanguage={(wish) => void doing.run(() => procureApi.wishLanguage(wish.id, null))}
          onRemove={remove}
          onReasons={setReasonsOf}
        />
      ) : (
        <div className="rqs" data-testid="wanted">
          {shown.map((wish) => {
            const state = stateOf(wish)
            const busy = wish.state === 'waiting' || (wish.state === 'searching' && !wish.waiting_for_a_place)
            // Its language is no longer one the operator allows: it rests until it is turned to the languages allowed.
            const languageOff = wish.state === 'idle' && wish.detail?.reason === 'language_off'
            const series = wish.book.series ? ` · ${wish.book.series}${wish.book.series_pos != null ? ` ${wish.book.series_pos}` : ''}` : ''
            const reasons = (wish.state === 'not_found' || wish.state === 'capped') && wish.detail?.reasons?.length ? wish.detail.reasons : []
            return (
              <article className="rq" key={wish.id} data-wish={wish.id} data-state={wish.state}>
                <MiniCover book={wish.book} />
                <div className="rq-main">
                  <h3 dir="auto">
                    <BookLink book={wish.book} />
                  </h3>
                  <p className="rq-by" dir="auto">
                    {wish.book.authors.join(', ')}
                    {series}
                  </p>
                  <div className="chips">
                    <KindChip kind={wish.kind} language={wish.language} />
                    {wish.state === 'upgrade' && (
                      <span className="chip upg">
                        <ArrowUp aria-hidden className="ic" />
                        {t('acquire.wanted.upgrade')}
                      </span>
                    )}
                    {wish.library && <span className="chip">{iso(wish.library.name)}</span>}
                  </div>
                  <p className="rq-who">
                    {wish.via
                      ? t('acquire.wanted.via', { name: wish.via, ago: ago(wish.created_at, i18n.language, t('acquire.justNow')) })
                      : t('acquire.wanted.by', { name: wish.by, ago: ago(wish.created_at, i18n.language, t('acquire.justNow')) })}
                  </p>
                </div>
                <div className={`rq-state t-${state.tone}`}>
                  <span className="si">{state.icon}</span>
                  <div>
                    <b>{state.label}</b>
                    <span>{state.sub}</span>
                    {reasons.length > 0 && (
                      <details className="why">
                        <summary>{t('acquire.wanted.why', { count: wish.detail?.total ?? 0 })}</summary>
                        <ul>
                          {reasons.map((reason) => (
                            <li key={reason.code}>
                              {reason.count} × {t(`procure.releases.reasons.${reason.code}.chip`, { defaultValue: reason.code })}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                </div>
                <div className="rq-act">
                  {languageOff ? (
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.wishLanguage(wish.id, null))} data-act="relanguage">
                      {t('acquire.wanted.relanguage')}
                    </button>
                  ) : (
                    <>
                  <button type="button" className="bbtn small primary" disabled={busy || doing.busy} onClick={() => void doing.run(() => procureApi.searchWish(wish.id))} title={wish.state === 'waiting' ? t('acquire.wanted.notOut', { date: dayText(wish.release_date, i18n.language, true) }) : undefined} data-act="search">
                    <Search aria-hidden className="ic" />
                    {t('acquire.wanted.searchNow')}
                  </button>
                  <button type="button" className="bbtn small" onClick={() => setReleases(wish)} data-act="releases">
                    {t('procure.releases.open')}
                  </button>
                    </>
                  )}
                  <button type="button" className="bbtn small icon" onClick={() => remove(wish)} title={t('acquire.wanted.remove.title')} aria-label={t('acquire.wanted.remove.title')} data-act="remove">
                    <Trash2 aria-hidden className="ic" />
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}
      {releases && (
        <ReleasesDialog
          workId={releases.book.work_id ?? undefined}
          book={releases.book.book ?? undefined}
          language={releases.language}
          title={releases.book.title}
          author={releases.book.authors[0] ?? ''}
          kind={releases.kind}
          onClose={() => setReleases(null)}
          onLoaded={() => {
            setReleases(null)
            void onChanged()
          }}
        />
      )}
      {reasonsOf && (
        <Dialog title={t('acquire.wanted.list.whyTitle', { count: reasonsOf.detail?.total ?? 0 })} sub={reasonsOf.book.title} onClose={() => setReasonsOf(null)} className="acq">
          <ul className="wl-reasons" data-testid="wanted-reasons">
            {(reasonsOf.detail?.reasons ?? []).map((reason) => (
              <li key={reason.code}>
                {reason.count} × {t(`procure.releases.reasons.${reason.code}.chip`, { defaultValue: reason.code })}
              </li>
            ))}
          </ul>
        </Dialog>
      )}
      {confirm.dialog}
    </>
  )
}
