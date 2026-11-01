/**
 * One shelf opened: its books on boards, with the filters of the design (section,
 * language, format, age, sort, "hide what I have", and the topics of the shelf), a page at a time. The filters are in the
 * address, so that a shelf can be linked to and the way back lands where it was left.
 *
 * Nothing here asks a source while the person waits; where the server is still filling the shelf the page asks again.
 */
import { ArrowLeft } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, authApi, browseApi, type Profile, type ShelfCard, type ShelfPageOut, type ShelfQuery, type ShelfSection, type ShelfSort, type ShelfYears } from '../api/client'
import { HandProvider } from '../components/browse/Hand'
import { MarketPick } from '../components/MarketPick'
import { PageHead } from '../components/PageHead'
import { ShelfBook } from '../components/browse/ShelfBook'
import { cardMeta, SECTIONS } from '../lib/browse'
import { languageName } from '../lib/books'
import { sourceNames } from '../lib/discover'
import { errorText } from '../lib/errors'
import { useOpenCard } from '../lib/openBook'
import { useAuth } from '../state/auth'

const LISTS = ['browse', 'best', 'new', 'soon', 'trending', 'foryou'] as const
const FORMATS = ['all', 'ebook', 'audio'] as const
const YEARS: ShelfYears[] = ['', 'new', 'mid', 'old']
const SORTS: ShelfSort[] = ['pop', 'new', 'short']
const PAGE = 60
const POLL_MS = 4000
const POLL_TIMES = 45

export function ShelfPage() {
  const { t, i18n } = useTranslation()
  const { me, setMe } = useAuth()
  const { key = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const open = useOpenCard()
  const operator = me?.role === 'operator'
  const section = (SECTIONS as string[]).includes(params.get('section') ?? '') ? (params.get('section') as ShelfSection) : ''
  const language = /^[a-z]{2}$/.test(params.get('language') ?? '') ? (params.get('language') as string) : ''
  const format = (FORMATS as readonly string[]).includes(params.get('format') ?? '') ? (params.get('format') as (typeof FORMATS)[number]) : 'all'
  const years = (YEARS as string[]).includes(params.get('years') ?? '') ? ((params.get('years') ?? '') as ShelfYears) : ''
  const topic = /^[a-z0-9]{1,24}$/.test(params.get('topic') ?? '') ? (params.get('topic') as string) : ''
  const sort = (SORTS as string[]).includes(params.get('sort') ?? '') ? (params.get('sort') as ShelfSort) : 'pop'
  const [hide, setHide] = useState(me?.profile?.discover_hide_owned !== false)
  const [market, setMarket] = useState(me?.profile?.market ?? '')
  const [data, setData] = useState<ShelfPageOut | null>(null)
  const [items, setItems] = useState<ShelfCard[]>([])
  const [failed, setFailed] = useState<string | null>(null)
  const [unknown, setUnknown] = useState(false)
  const [more, setMore] = useState(false)
  const [again, setAgain] = useState(0)
  const tries = useRef(0)

  const query: ShelfQuery = {
    market,
    section,
    language,
    format,
    years,
    topic,
    sort,
    hide_owned: hide,
    limit: PAGE,
  }

  useEffect(() => {
    tries.current = 0
  }, [key, market, section, language, format, years, topic, sort, hide])

  useEffect(() => {
    const controller = new AbortController()
    browseApi
      .shelf(
        key,
        {
          market,
          section,
          language,
          format,
          years,
          topic,
          sort,
          hide_owned: hide,
          limit: PAGE,
        },
        controller.signal,
      )
      .then(
        (answer) => {
          setData(answer)
          setItems(answer.items)
          setFailed(null)
          setUnknown(false)
        },
        (error) => {
          if (error instanceof ApiError && error.code === 'cancelled') return
          if (error instanceof ApiError && error.status === 404) setUnknown(true)
          else setFailed(errorText(error instanceof ApiError ? error.code : 'internal_error'))
        },
      )
    return () => controller.abort()
  }, [key, market, section, language, format, years, topic, sort, hide, again])

  const pending = data?.pending ?? false
  useEffect(() => {
    if (!pending || tries.current >= POLL_TIMES) return
    const timer = window.setTimeout(() => {
      tries.current += 1
      setAgain((n) => n + 1)
    }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [pending, data])

  const change = (next: Record<string, string | null>) => {
    const copy = new URLSearchParams(params)
    for (const [name, value] of Object.entries(next)) {
      if (value === null || value === '' || value === 'all' || (name === 'sort' && value === 'pop')) copy.delete(name)
      else copy.set(name, value)
    }
    setParams(copy, { replace: true })
  }

  const keep = useCallback(
    (change: Partial<Pick<Profile, 'discover_hide_owned' | 'market'>>) => {
      authApi.preferences(change).then(
        (profile) => me && setMe?.({ ...me, profile }),
        (error) => setFailed(errorText(error instanceof ApiError ? error.code : 'internal_error')),
      )
    },
    [me, setMe],
  )

  const showMore = async () => {
    setMore(true)
    try {
      const answer = await browseApi.shelf(key, {
        ...query,
        offset: items.length,
      })
      setItems((now) => [...now, ...answer.items.filter((card) => !now.some((have) => have.token === card.token))])
      setData(answer)
    } catch (error) {
      setFailed(errorText(error instanceof ApiError ? error.code : 'internal_error'))
    } finally {
      setMore(false)
    }
  }

  const settings = <Link to="/settings?tab=sources" />
  const bounds = data?.year_bounds
  const yearText = (value: ShelfYears) => {
    if (value === '' || !bounds) return t('browse.shelfPage.years.all')
    if (value === 'new')
      return t('browse.shelfPage.years.new', {
        from: bounds.new_from,
        to: bounds.new_from + 1,
      })
    if (value === 'mid')
      return t('browse.shelfPage.years.mid', {
        from: bounds.mid_from,
        to: bounds.new_from - 1,
      })
    return t('browse.shelfPage.years.old', { year: bounds.mid_from })
  }
  const seg = (name: string, current: string, options: [string, string][], attribute: string) => (
    <div className="seg small" role="group" aria-label={name}>
      {options.map(([value, label]) => (
        <button
          key={value}
          type="button"
          className={current === value ? 'on' : ''}
          aria-pressed={current === value}
          data-filter={attribute}
          data-value={value}
          onClick={() => change({ [attribute]: value })}
        >
          {label}
        </button>
      ))}
    </div>
  )
  const names = data ? sourceNames(data.sources, t, i18n.language) : ''
  const marketName = t(`discover.markets.${data?.market ?? market}`, {
    defaultValue: data?.market ?? market,
  })
  const info = !data
    ? ''
    : hide && data.owned > 0
      ? `${t('discover.info.mine', { count: data.total })} · ${t('discover.info.hidden', { count: data.owned })}`
      : `${t('discover.info.all', { count: data.total })}${data.owned ? ` · ${t('discover.info.onShelf', { count: data.owned })}` : ''}`
  const dept = data?.dept
  const kind = section === 'new' || sort === 'new' ? 'new' : section === 'award' ? 'award' : sort === 'short' ? 'pages' : ''

  if (unknown) {
    return (
      <div className="view shp" data-testid="shelf-page">
        <PageHead title={t('discover.title')} text={t('discover.text')} />
        <nav className="dlists" aria-label={t('discover.lists')}>
          {LISTS.map((list) => (
            <Link
              key={list}
              to={list === 'browse' ? '/discover' : `/discover?list=${list}`}
              className={list === 'browse' ? 'on' : ''}
              aria-current={list === 'browse' ? 'page' : undefined}
            >
              {t(`discover.list.${list}`)}
            </Link>
          ))}
        </nav>
        <Link className="back-link" to="/discover">
          <ArrowLeft aria-hidden />
          {t('browse.back')}
        </Link>
        <p className="empty" data-testid="unknown-shelf">
          {t('browse.shelfPage.unknown')}
        </p>
      </div>
    )
  }
  return (
    <div className="view shp" data-testid="shelf-page" data-shelf={key}>
      <PageHead title={t('discover.title')} text={t('discover.text')}>
        <MarketPick
          current={market || data?.market || ''}
          markets={data?.markets ?? []}
          onPick={(value) => {
            setMarket(value)
            keep({ market: value })
          }}
        />
      </PageHead>
      <nav className="dlists" aria-label={t('discover.lists')}>
        {LISTS.map((list) => (
          <Link
            key={list}
            to={list === 'browse' ? '/discover' : `/discover?list=${list}`}
            className={list === 'browse' ? 'on' : ''}
            aria-current={list === 'browse' ? 'page' : undefined}
          >
            {t(`discover.list.${list}`)}
          </Link>
        ))}
      </nav>
      <Link className="back-link" to="/discover">
        <ArrowLeft aria-hidden />
        {t('browse.back')}
      </Link>
      <div className="dsec-h">
        <div>
          {dept && <p className="eyebrow">{t(`browse.dept.${dept}.name`)}</p>}
          <h2 className="shp-t">{t(`browse.shelf.${key}.name`, { defaultValue: key })}</h2>
          <p>
            {t(`browse.shelf.${key}.text`, { defaultValue: '' })} {info}
            {info ? '.' : ''}
          </p>
        </div>
        {names && (
          <span className="src" data-testid="source">
            <i />
            {t('discover.sourceMarket', { names, market: marketName })}
          </span>
        )}
      </div>
      {failed && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {failed}
        </p>
      )}
      <div className="dfilters shp-f" data-testid="filters">
        {seg(
          t('browse.shelfPage.section'),
          section,
          [['', t('browse.section.all')], ...SECTIONS.filter((s) => data?.sections.some((entry) => entry.key === s)).map((s): [string, string] => [s, t(`browse.section.${s}`)])],
          'section',
        )}
        {seg(
          t('discover.language.label'),
          language,
          [['', t('discover.language.all')], ...(data?.languages ?? []).map((entry): [string, string] => [entry.key, languageName(entry.key, i18n.language)])],
          'language',
        )}
        {seg(
          t('discover.format.label'),
          format,
          FORMATS.map((value): [string, string] => [value, t(`discover.format.${value}`)]),
          'format',
        )}
        {seg(
          t('browse.shelfPage.years.label'),
          years,
          YEARS.map((value): [string, string] => [value, yearText(value)]),
          'years',
        )}
        <label className="toggle">
          <input
            type="checkbox"
            checked={hide}
            onChange={(event) => {
              setHide(event.target.checked)
              keep({ discover_hide_owned: event.target.checked })
            }}
            data-testid="hide-owned"
          />
          <span />
          {t('discover.hideOwned')}
        </label>
        <select className="sel mini" aria-label={t('browse.shelfPage.sort.label')} value={sort} onChange={(event) => change({ sort: event.target.value })} data-testid="sort">
          {SORTS.map((value) => (
            <option key={value} value={value}>
              {t(`browse.shelfPage.sort.${value}`)}
            </option>
          ))}
        </select>
        {(data?.topics.length ?? 0) > 0 && (
          <div className="genres dgenres" role="group" aria-label={t('browse.shelfPage.topics.label')}>
            <button type="button" className={`tchip ${topic === '' ? 'on' : ''}`} aria-pressed={topic === ''} data-topic="" onClick={() => change({ topic: '' })}>
              {t('browse.shelfPage.topics.all')}
            </button>
            {data?.topics.map((entry) => (
              <button
                key={entry.key}
                type="button"
                className={`tchip ${topic === entry.key ? 'on' : ''}`}
                aria-pressed={topic === entry.key}
                data-topic={entry.key}
                onClick={() => change({ topic: entry.key })}
              >
                {t(`browse.topic.${key}.${entry.key}`, {
                  defaultValue: entry.key,
                })}
              </button>
            ))}
          </div>
        )}
      </div>
      {data &&
        (items.length > 0 ? (
          <HandProvider onOpen={open}>
            <div className="bgrid" data-testid="grid">
              {items.map((card) => (
                <ShelfBook key={card.token} card={card} meta={cardMeta(card, kind, t, i18n.language)} />
              ))}
            </div>
          </HandProvider>
        ) : (
          <Nothing data={data} operator={operator} settings={settings} hide={hide} />
        ))}
      {data && items.length < data.total && (
        <div className="shp-more acq">
          <button type="button" className="bbtn" onClick={() => void showMore()} disabled={more} data-testid="more">
            {t('browse.shelfPage.more')}
          </button>
        </div>
      )}
    </div>
  )
}

function Nothing({ data, operator, settings, hide }: { data: ShelfPageOut; operator: boolean; settings: ReactElement; hide: boolean }) {
  const { t } = useTranslation()
  if (data.state === 'main_off')
    return (
      <p className="empty" data-testid="why-main-off">
        {operator ? <Trans i18nKey="discover.state.mainOff" components={{ settings }} /> : t('discover.state.mainOffMember')}
      </p>
    )
  if (data.state === 'no_source')
    return (
      <p className="empty" data-testid="why-no-source">
        {operator ? <Trans i18nKey="browse.state.noSource" components={{ settings }} /> : t('browse.state.noSourceMember')}
      </p>
    )
  if (data.state === 'fetching')
    return (
      <p className="empty" data-testid="why-fetching">
        {t('browse.state.fetching')}
      </p>
    )
  return (
    <p className="empty" data-testid="nothing">
      {t('browse.shelfPage.none')} {hide && data.owned ? t('browse.shelfPage.noneOwned') : ''}
    </p>
  )
}
