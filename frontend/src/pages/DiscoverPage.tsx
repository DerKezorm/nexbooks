/**
 * Discover: the lists of the sources (best sellers, new, coming soon, trending) for a
 * market, with the filters of the design (format, language, genre, "hide what I have"), and "for you": the gaps in the series
 * the person reads and more of their authors. The wish list stands at the foot. Covers only, never spines.
 *
 * Nothing here asks a source while the person waits: the page reads what the server kept, and where the server is still
 * fetching or looking something up it says so and asks again after a few seconds. The market, the language and the switch that
 * hides what the person has are kept in their profile; the list, the format and the genre are in the address.
 *
 * What stands on the person's own shelf is decided by the server from their libraries (`owned_work_id`); a book that does
 * opens as the book of the shelf, any other as the page of a book that is not (`ForeignPage`).
 */
import { Download } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, authApi, discoverApi, type BrowseWall, type DiscoverCard, type DiscoverList, type DiscoverListName, type ForYou, type Profile, type SeriesGap } from '../api/client'
import { BrowseView } from '../components/browse/BrowseView'
import { HandProvider } from '../components/browse/Hand'
import { ForeignCard } from '../components/ForeignCard'
import { MarketPick } from '../components/MarketPick'
import { PageHead } from '../components/PageHead'
import { languageName } from '../lib/books'
import { cardOfWork, dayText, sourceNames } from '../lib/discover'
import { errorText } from '../lib/errors'
import { useOpenCard } from '../lib/openBook'
import { acquireBookOfCard } from '../lib/procure'
import { GENRES } from '../lib/shelf'
import { useAuth } from '../state/auth'
import { useProcure } from '../state/procure'
import { useWish } from '../state/wish'
import { DiscoverSearchField } from './DiscoverSearch'

type ListKey = DiscoverListName | 'foryou' | 'browse'
/** Browse comes first and is the one shown when the address names no list. */
const LISTS: ListKey[] = ['browse', 'best', 'new', 'soon', 'trending', 'foryou']
const FORMATS = ['all', 'ebook', 'audio'] as const
type Format = (typeof FORMATS)[number]
/** The markets shown before the server has said which there are (the design's four). */
const FIRST_MARKETS = ['de', 'us', 'uk', 'fr']
/** While the server is fetching a list or looking something up, the page asks again this often (milliseconds), this many times. */
const POLL_MS = 4000
const POLL_TIMES = 30

function listOf(value: string | null): ListKey {
  return LISTS.includes(value as ListKey) ? (value as ListKey) : 'browse'
}

export function DiscoverPage() {
  const { t } = useTranslation()
  const { me, setMe } = useAuth()
  const [params, setParams] = useSearchParams()
  const list = listOf(params.get('list'))
  const format: Format = FORMATS.includes(params.get('format') as Format) ? (params.get('format') as Format) : 'all'
  const genre = (GENRES as readonly string[]).includes(params.get('genre') ?? '') && params.get('genre') !== 'none' ? (params.get('genre') ?? '') : ''
  const [market, setMarket] = useState(me?.profile?.market ?? '')
  const [language, setLanguage] = useState(me?.profile?.language ?? '')
  const [hide, setHide] = useState(me?.profile?.discover_hide_owned !== false)
  const [markets, setMarkets] = useState<string[]>(FIRST_MARKETS)
  // The market the server shows when the person chose none (the one of the language of the page).
  const [resolved, setResolved] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const wish = useWish()
  const open = useOpenCard()

  const keep = useCallback(
    (change: Partial<Pick<Profile, 'market' | 'language' | 'discover_hide_owned'>>) => {
      authApi.preferences(change).then(
        (profile) => me && setMe?.({ ...me, profile }),
        (error) => setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error')),
      )
    },
    [me, setMe],
  )

  const change = (next: Record<string, string | null>) => {
    const copy = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === '' || value === 'all') copy.delete(key)
      else copy.set(key, value)
    }
    setParams(copy, { replace: true })
  }

  const showMarket = (value: string) => {
    setMarket(value)
    keep({ market: value })
  }

  const current = market || resolved
  return (
    <div className="view" data-testid="discover">
      <PageHead title={t('discover.title')} text={t('discover.text')}>
        <MarketPick current={current} markets={markets} onPick={showMarket} />
      </PageHead>
      <DiscoverSearchField />
      <nav className="dlists" id="dlists" aria-label={t('discover.lists')}>
        {LISTS.map((key) => (
          <button key={key} type="button" data-l={key} className={list === key ? 'on' : ''} aria-current={list === key ? 'page' : undefined} onClick={() => change({ list: key === 'browse' ? null : key })}>
            {t(`discover.list.${key}`)}
          </button>
        ))}
      </nav>
      {problem && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {problem}
        </p>
      )}
      {wish.problem && (
        <p role="alert" className="mb-4 text-sm text-bad" data-testid="wish-problem">
          {wish.problem}
        </p>
      )}
      {list === 'browse' ? (
        <HandProvider onOpen={open}>
          <BrowseView
            market={market}
            hide={hide}
            onHide={(value) => {
              setHide(value)
              keep({ discover_hide_owned: value })
            }}
            onLoaded={(answer: BrowseWall) => {
              if (Array.isArray(answer.markets)) setMarkets(answer.markets)
              setResolved(answer.market ?? '')
            }}
          />
        </HandProvider>
      ) : list === 'foryou' ? (
        <ForYouView onOpen={open} />
      ) : (
        <ListView
          list={list}
          market={market}
          format={format}
          language={language}
          genre={genre}
          hide={hide}
          onFormat={(value) => change({ format: value })}
          onLanguage={(value) => {
            setLanguage(value)
            keep({ language: value })
          }}
          onHide={(value) => {
            setHide(value)
            keep({ discover_hide_owned: value })
          }}
          onGenre={(value) => change({ genre: value })}
          onLoaded={(answer) => {
            if (Array.isArray(answer.markets)) setMarkets(answer.markets)
            setResolved(answer.market ?? '')
          }}
          onOpen={open}
        />
      )}
      <WishSection onOpen={open} />
    </div>
  )
}

// ---- A list of the sources ---------------------------------------------------------------------------------------------

function ListView({
  list,
  market,
  format,
  language,
  genre,
  hide,
  onFormat,
  onLanguage,
  onHide,
  onGenre,
  onLoaded,
  onOpen,
}: {
  list: DiscoverListName
  market: string
  format: Format
  language: string
  genre: string
  hide: boolean
  onFormat: (value: Format) => void
  onLanguage: (value: string) => void
  onHide: (value: boolean) => void
  onGenre: (value: string) => void
  onLoaded: (answer: DiscoverList) => void
  onOpen: (card: DiscoverCard) => void
}) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const operator = me?.role === 'operator'
  const [data, setData] = useState<DiscoverList | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [again, setAgain] = useState(0)
  const tries = useRef(0)
  const loaded = useRef(onLoaded)
  loaded.current = onLoaded

  useEffect(() => {
    tries.current = 0
  }, [list, market, format, language, genre, hide])

  useEffect(() => {
    const controller = new AbortController()
    discoverApi
      .list(list, { market, format, language, genre, hide_owned: hide }, controller.signal)
      .then(
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
  }, [list, market, format, language, genre, hide, again])

  // A list that is being fetched is read again until it is there.
  const state = data?.state
  useEffect(() => {
    if (state !== 'fetching' || tries.current >= POLL_TIMES) return
    const timer = window.setTimeout(() => {
      tries.current += 1
      setAgain((n) => n + 1)
    }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [state, data])

  const current = data && data.list === list ? data : null
  const languages = [...new Set([...(current?.languages.map((entry) => entry.code) ?? []), ...(language ? [language] : [])])]
  const items = current?.items ?? []
  const when = list === 'new' || list === 'soon' ? list : undefined
  const marketName = t(`discover.markets.${current?.market ?? market}`, { defaultValue: current?.market ?? market })
  const names = current ? sourceNames(current.sources, t, i18n.language) : ''
  const sourceText = list === 'trending' ? t('discover.source', { names }) : t('discover.sourceMarket', { names, market: marketName })
  const info = !current
    ? ''
    : current.hide_owned && current.owned > 0
      ? `${t('discover.info.mine', { count: items.length })} · ${t('discover.info.hidden', { count: current.owned })}`
      : `${t('discover.info.all', { count: items.length })}${current.owned ? ` · ${t('discover.info.onShelf', { count: current.owned })}` : ''}`

  return (
    <>
      <div className="dfilters" id="dfilters">
        <div className="seg small" id="dfmt" role="group" aria-label={t('discover.format.label')}>
          {FORMATS.map((value) => (
            <button key={value} type="button" data-f={value} className={format === value ? 'on' : ''} aria-pressed={format === value} onClick={() => onFormat(value)}>
              {t(`discover.format.${value}`)}
            </button>
          ))}
        </div>
        <div className="seg small" id="dlang" role="group" aria-label={t('discover.language.label')}>
          <button type="button" data-lang="all" className={language === '' ? 'on' : ''} aria-pressed={language === ''} onClick={() => onLanguage('')}>
            {t('discover.language.all')}
          </button>
          {languages.map((code) => (
            <button key={code} type="button" data-lang={code} className={language === code ? 'on' : ''} aria-pressed={language === code} onClick={() => onLanguage(code)}>
              {languageName(code, i18n.language)}
            </button>
          ))}
        </div>
        <label className="toggle">
          <input type="checkbox" id="hideOwned" checked={hide} onChange={(event) => onHide(event.target.checked)} />
          <span />
          {t('discover.hideOwned')}
        </label>
        <div className="genres dgenres" id="dgenres" role="group" aria-label={t('discover.genres')}>
          <button type="button" className={`g ${genre === '' ? 'on' : ''}`} data-dg="" aria-pressed={genre === ''} onClick={() => onGenre('')}>
            {t('discover.allGenres')}
          </button>
          {GENRES.filter((key) => key !== 'none').map((key) => (
            <button key={key} type="button" className={`g ${genre === key ? 'on' : ''}`} data-dg={key} aria-pressed={genre === key} onClick={() => onGenre(key)}>
              {t(`library.genre.${key}`)}
            </button>
          ))}
        </div>
      </div>
      <div id="discover">
        {failed && (
          <p role="alert" className="mb-4 text-sm text-bad">
            {failed}
          </p>
        )}
        {current && (
          <section className="dsec" data-testid="list">
            <div className="dsec-h">
              <div>
                <h2>{t(`discover.list.${list}`)}</h2>
                <p>
                  {t(`discover.sub.${list}`)} {info}.
                </p>
              </div>
              <span className="src" data-testid="source">
                <i />
                {sourceText}
              </span>
            </div>
            {current.state === 'ready' && !current.asking && current.fetched_at && (
              <p className="dnote" data-testid="off-note">
                {t('discover.state.offNote', { date: dayText(current.fetched_at.slice(0, 10), i18n.language, true) })}
              </p>
            )}
            {items.length > 0 ? (
              <div className="dgrid" data-testid="grid">
                {items.map((card) => (
                  <ForeignCard key={card.token} card={card} when={when} onOpen={onOpen} />
                ))}
              </div>
            ) : (
              <Why data={current} operator={operator} />
            )}
          </section>
        )}
      </div>
    </>
  )
}

/** Why a list shows nothing: the reason, in a sentence, with the way to the settings for the operator. */
function Why({ data, operator }: { data: DiscoverList; operator: boolean }) {
  const { t } = useTranslation()
  const settings = <Link to="/settings?tab=sources" />
  if (data.state === 'ready') {
    return (
      <p className="empty" data-testid="nothing">
        {t('discover.nothing')} {data.hide_owned && data.owned ? t('discover.nothingOwned') : ''}
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
    const unconnected = data.availability.some((entry) => entry.state === 'not_connected')
    // The lists of new and coming audiobooks come from Audible alone, which is off until the operator turns it on.
    const audibleOff = data.availability.some((entry) => entry.source === 'audible' && entry.state === 'off')
    const key = unconnected ? 'notConnected' : audibleOff ? 'audibleOff' : 'noSource'
    return (
      <p className="empty" data-testid="why-no-source">
        {operator ? <Trans i18nKey={`discover.state.${key}`} components={{ settings }} /> : t(`discover.state.${key}Member`)}
      </p>
    )
  }
  return (
    <p className="empty" data-testid={`why-${data.state}`}>
      {t(`discover.state.${data.state}`)}
    </p>
  )
}

// ---- For you -----------------------------------------------------------------------------------------------------------

function ForYouView({ onOpen }: { onOpen: (card: DiscoverCard) => void }) {
  const { t } = useTranslation()
  const { me } = useAuth()
  const operator = me?.role === 'operator'
  const [data, setData] = useState<ForYou | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [again, setAgain] = useState(0)
  const tries = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    discoverApi.forYou(controller.signal).then(
      (answer) => {
        setData(answer)
        setFailed(null)
      },
      (error) => {
        if (!(error instanceof ApiError && error.code === 'cancelled')) setFailed(errorText(error instanceof ApiError ? error.code : 'internal_error'))
      },
    )
    return () => controller.abort()
  }, [again])

  const pending = data?.pending ?? false
  useEffect(() => {
    if (!pending || tries.current >= POLL_TIMES) return
    const timer = window.setTimeout(() => {
      tries.current += 1
      setAgain((n) => n + 1)
    }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [pending, data])

  if (failed) {
    return (
      <p role="alert" className="mb-4 text-sm text-bad">
        {failed}
      </p>
    )
  }
  if (!data) return <div id="discover" />
  const settings = <Link to="/settings?tab=sources" />
  const nothing = data.gaps.length === 0 && data.authors.length === 0
  return (
    <div id="discover" data-testid="foryou">
      {data.state !== 'ready' && nothing ? (
        <p className="empty" data-testid={`foryou-${data.state}`}>
          {data.state === 'main_off'
            ? operator
              ? <Trans i18nKey="discover.state.mainOff" components={{ settings }} />
              : t('discover.state.mainOffMember')
            : operator
              ? <Trans i18nKey="discover.state.noSource" components={{ settings }} />
              : t('discover.state.noSourceMember')}
        </p>
      ) : (
        <>
          {data.pending && (
            <p className="dnote" data-testid="foryou-pending">
              {t('discover.foryou.pending')}
            </p>
          )}
          <section className="dsec">
            <div className="dsec-h">
              <div>
                <h2>{t('discover.foryou.gaps.title')}</h2>
                <p>{t('discover.foryou.gaps.text')}</p>
              </div>
              {data.gaps.length > 0 && (
                <span className="src">
                  <i />
                  {t('discover.source', { names: data.sources.map((id) => t(`discover.sourceName.${id}`)).join(', ') })}
                </span>
              )}
            </div>
            {data.gaps.length > 0 ? (
              <div className="gaps" data-testid="gaps">
                {data.gaps.map((gap) => (
                  <div className="gap" key={gap.series_id} data-testid="gap">
                    <h3 dir="auto">{gap.name}</h3>
                    <p className="gmeta">
                      <Trans i18nKey="discover.gap.meta" values={{ author: gap.author, have: gap.have, out: gap.out }} components={{ b: <b /> }} />
                      {' · '}
                      {[gap.missing > 0 ? t('discover.gap.missing', { count: gap.missing }) : '', gap.soon > 0 ? t('discover.gap.soon', { count: gap.soon }) : ''].filter(Boolean).join(', ')}
                    </p>
                    <GapAction gap={gap} />
                    <div className="row vols">
                      {gap.volumes.map((volume) => (
                        <ForeignCard key={volume.pos} card={volume.work ? cardOfWork(volume.work) : (volume.book as DiscoverCard)} mode="vol" onOpen={onOpen} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty" data-testid="no-gaps">
                {data.pending ? t('discover.foryou.looking') : t('discover.foryou.gaps.none')}
              </p>
            )}
          </section>
          <section className="dsec">
            <div className="dsec-h">
              <div>
                <h2>{t('discover.foryou.authors.title')}</h2>
                <p>{t('discover.foryou.authors.text')}</p>
              </div>
              {data.authors.some((author) => author.books.length > 0) && (
                <span className="src">
                  <i />
                  {t('discover.source', { names: data.sources.map((id) => t(`discover.sourceName.${id}`)).join(', ') })}
                </span>
              )}
            </div>
            {data.authors.length > 0 ? (
              <div className="authors" data-testid="authors">
                {data.authors.map((author) => (
                  <div className="arow" key={author.person_id} data-testid="author">
                    <h3 dir="auto">
                      {author.name} <small>{t('discover.foryou.onShelf', { count: author.on_shelf })}</small>
                    </h3>
                    {author.pending ? (
                      <p className="empty">{t('discover.foryou.looking')}</p>
                    ) : (
                      <div className="row">
                        {author.books.map((card) => (
                          <ForeignCard key={card.token} card={card} mode="row" onOpen={onOpen} />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty" data-testid="no-authors">
                {t('discover.foryou.authors.none')}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  )
}

// ---- The wish list -----------------------------------------------------------------------------------------------------

function WishSection({ onOpen }: { onOpen: (card: DiscoverCard) => void }) {
  const { t } = useTranslation()
  const wish = useWish()
  const procure = useProcure()
  if (wish.count === 0) return null
  // Books of the list that nobody has asked for yet: "Alle beschaffen" asks for them in one go.
  const free = procure.allowed ? wish.items.filter((card) => card.owned_work_id === null && procure.kindsOf({ token: card.token }).length === 0) : []
  return (
    <section className="dsec wishsec acq" data-testid="wishlist">
      <div className="dsec-h">
        <div>
          <h2>
            {t('discover.wish.title')} <small>{wish.count}</small>
          </h2>
          <p>{procure.allowed ? t('acquire.entry.wishNote') : t('discover.wish.text')}</p>
          {free.length > 0 && (
            <button type="button" className="bbtn small primary wish-all" onClick={() => procure.open(free.map(acquireBookOfCard), { kinds: ['ebook'] })} data-testid="acquire-all">
              <Download size={15} aria-hidden />
              {t('acquire.entry.allButton')} <small>{free.length}</small>
            </button>
          )}
        </div>
      </div>
      <div className="row">
        {wish.items.map((card) => (
          <ForeignCard key={card.token} card={{ ...card, wished: true }} mode="row" onOpen={onOpen} />
        ))}
      </div>
    </section>
  )
}

/** "Fehlende beschaffen": asks for every volume of a series the person lacks and nobody has asked for yet. */
function GapAction({ gap }: { gap: SeriesGap }) {
  const { t } = useTranslation()
  const procure = useProcure()
  if (!procure.allowed) return null
  const missing = gap.volumes.flatMap((volume) => (volume.book && !volume.work && procure.kindsOf({ token: volume.book.token }).length === 0 ? [volume.book as DiscoverCard] : []))
  if (missing.length === 0) return null
  return (
    <span className="acq">
      <button type="button" className="bbtn small gap-act" onClick={() => procure.open(missing.map(acquireBookOfCard), { kinds: ['ebook'] })} data-testid="acquire-gap">
        <Download size={15} aria-hidden />
        {t('acquire.entry.gapButton')}
      </button>
    </span>
  )
}
