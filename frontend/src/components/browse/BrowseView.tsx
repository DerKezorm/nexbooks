/**
 * Browse, the first tab of Discover: like a bookshop. A wall of thirteen shelves in
 * three departments (a tile with three covers for each), the shelves made for the person, and below, department by department,
 * a strip of covers for each shelf. Nothing here asks a source while the person waits: the page reads what the server kept,
 * and where the server is still filling the shelves it says so and asks again after a few seconds.
 *
 * What stands on the person's own shelf is decided by the server from their libraries (`owned_work_id`).
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, browseApi, type BrowseWall, type DiscoverCard } from '../../api/client'
import { DEPARTMENTS, touchOnly } from '../../lib/browse'
import { coverOf, dayText, sourceNames } from '../../lib/discover'
import { errorText } from '../../lib/errors'
import { useAuth } from '../../state/auth'
import { Cover } from '../BookCard'
import { WatchStrip } from '../WatchStrip'
import { PersonalRows } from './PersonalRows'
import { ShelfStripView } from './ShelfStrip'

/** While the server is filling the shelves the page asks again this often (milliseconds), this many times. */
const POLL_MS = 4000
const POLL_TIMES = 45

export function BrowseView({ market, hide, onHide, onLoaded }: { market: string; hide: boolean; onHide: (value: boolean) => void; onLoaded: (answer: BrowseWall) => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const operator = me?.role === 'operator'
  const [data, setData] = useState<BrowseWall | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [again, setAgain] = useState(0)
  const tries = useRef(0)
  const loaded = useRef(onLoaded)
  loaded.current = onLoaded

  useEffect(() => {
    tries.current = 0
  }, [market, hide])

  useEffect(() => {
    const controller = new AbortController()
    browseApi.wall({ market, hide_owned: hide }, controller.signal).then(
      (answer) => {
        setData(answer)
        setFailed(null)
        loaded.current(answer)
      },
      (error) => {
        if (!(error instanceof ApiError && error.code === 'cancelled')) setFailed(errorText(error instanceof ApiError ? error.code : 'internal_error'))
      },
    )
    return () => controller.abort()
  }, [market, hide, again])

  // Shelves that are being filled are read again until they are there.
  const pending = data?.pending ?? false
  useEffect(() => {
    if (!pending || tries.current >= POLL_TIMES) return
    const timer = window.setTimeout(() => {
      tries.current += 1
      setAgain((n) => n + 1)
    }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [pending, data])

  const settings = <Link to="/settings?tab=sources" />
  const names = data ? sourceNames(data.sources, t, i18n.language) : ''
  const marketName = t(`discover.markets.${data?.market ?? market}`, {
    defaultValue: data?.market ?? market,
  })
  const stripsOf = (dept: string) => (data ? data.strips.filter((strip) => data.departments.find((d) => d.key === dept)?.shelves.some((shelf) => shelf.key === strip.shelf)) : [])
  const empty = data !== null && data.strips.length === 0

  return (
    <div id="discover" data-testid="browse">
      <div className="browse-top">
        <p className="browse-intro">{touchOnly() ? t('browse.introTouch') : t('browse.intro')}</p>
        <label className="toggle">
          <input type="checkbox" checked={hide} onChange={(event) => onHide(event.target.checked)} data-testid="hide-owned" />
          <span />
          {t('discover.hideOwned')}
        </label>
      </div>
      <WatchStrip />
      {failed && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {failed}
        </p>
      )}
      {data && data.state === 'ready' && !data.asking && data.fetched_at && !empty && (
        <p className="dnote" data-testid="off-note">
          {t('discover.state.offNote', {
            date: dayText(data.fetched_at.slice(0, 10), i18n.language, true),
          })}
        </p>
      )}
      {data && pending && data.state === 'ready' && (
        <p className="dnote" data-testid="partial">
          {t('browse.state.partial')}
        </p>
      )}
      {data && empty && <Why data={data} operator={operator} settings={settings} />}
      {data && !empty && (
        <>
          <section className="dsec" data-testid="wall">
            <div className="dsec-h">
              <div>
                <h2>{t('browse.wall.title')}</h2>
                <p>{t('browse.wall.text')}</p>
              </div>
              {names && (
                <span className="src" data-testid="source">
                  <i />
                  {t('discover.sourceMarket', { names, market: marketName })}
                </span>
              )}
            </div>
            <div className="tw">
              {DEPARTMENTS.map((dept) => (
                <div className="tw-dept" key={dept}>
                  <h3>{t(`browse.dept.${dept}.name`)}</h3>
                  <div className="tw-grid">
                    {(data.departments.find((entry) => entry.key === dept)?.shelves ?? []).map((tile) => (
                      <Link className="shtile" key={tile.key} to={`/discover/shelf/${tile.key}`} data-testid="tile" data-shelf={tile.key}>
                        <span className="sh-fan">
                          {tile.fan.map((card: DiscoverCard, index) => (
                            <span className={`sh-c c${index}`} key={card.token}>
                              <Cover work={coverOf(card)} />
                            </span>
                          ))}
                        </span>
                        <b>{t(`browse.shelf.${tile.key}.name`)}</b>
                        <small>{[t('browse.tile.books', { count: tile.books }), tile.new > 0 ? t('browse.tile.new', { count: tile.new }) : ''].filter(Boolean).join(' · ')}</small>
                      </Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
          <PersonalRows market={market} hide={hide} />
          {DEPARTMENTS.map((dept) => {
            const strips = stripsOf(dept)
            if (strips.length === 0) return null
            return (
              <section className="dept" key={dept} data-testid="dept">
                <div className="dept-h">
                  <span className="eyebrow">{t('browse.dept.eyebrow')}</span>
                  <h2>{t(`browse.dept.${dept}.name`)}</h2>
                  <p>{t(`browse.dept.${dept}.text`)}</p>
                </div>
                {strips.map((strip) => (
                  <ShelfStripView key={strip.shelf} strip={strip} market={market} hide={hide} />
                ))}
              </section>
            )
          })}
        </>
      )}
    </div>
  )
}

/** Why the wall has no shelf: the reason, in a sentence, with the way to the settings for the operator. */
function Why({ data, operator, settings }: { data: BrowseWall; operator: boolean; settings: ReactElement }) {
  const { t } = useTranslation()
  if (data.state === 'ready') {
    return (
      <p className="empty" data-testid="nothing">
        {t('browse.shelfPage.none')} {data.hide_owned ? t('browse.shelfPage.noneOwned') : ''}
      </p>
    )
  }
  if (data.state === 'main_off') {
    return (
      <p className="empty" data-testid="why-main-off">
        {operator ? <Trans i18nKey="discover.state.mainOff" components={{ settings }} /> : t('discover.state.mainOffMember')}
      </p>
    )
  }
  if (data.state === 'no_source') {
    return (
      <p className="empty" data-testid="why-no-source">
        {operator ? <Trans i18nKey="browse.state.noSource" components={{ settings }} /> : t('browse.state.noSourceMember')}
      </p>
    )
  }
  return (
    <p className="empty" data-testid={`why-${data.state}`}>
      {t(`browse.state.${data.state}`)}
    </p>
  )
}
