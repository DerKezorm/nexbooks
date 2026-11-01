/**
 * The search of the catalogues in Discover (`/discover/search?q=`): what a library gives a person who asks for a book. One hit
 * for each book, named the way it is called in the languages the operator fetches in, with its author, series and volume, the
 * year it first came out and its cover; the book asked for first, then the other volumes of its series, then the rest; after
 * them boxes, study guides, plays, comics and parts, and last the books that exist in none of the languages fetched in. A book
 * is acquired with one press ("E-Book", "Hörbuch", "Beides"); its single editions stand behind "Ausgaben zeigen". The quick
 * search of the head asks the same and shows the first of these hits in the same order (`lib/catalogSearch`).
 */
import { ArrowRight } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { catalogApi, type AuthorHit, type CatalogGroup, type CatalogHit, type CatalogOut } from '../api/client'
import { AuthorAvatar } from '../components/AuthorAvatar'
import { AcquireKinds, EditionLine, HitChips, HitCover } from '../components/CatalogHitParts'
import { SearchIcon } from '../components/icons'
import { PageHead } from '../components/PageHead'
import { authorHref } from '../lib/catalog'
import { askCatalog, searchHref, tokenOfHit, tooOften, useHitLine, useRestingLine } from '../lib/catalogSearch'
import { useOpenAt } from '../lib/openBook'

const GROUPS: CatalogGroup[] = ['match', 'series', 'more', 'related', 'elsewhere']

/** The field of the search in Discover: it leads to the page of the search. */
export function DiscoverSearchField({ initial = '' }: { initial?: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [text, setText] = useState(initial)
  useEffect(() => setText(initial), [initial])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (text.trim()) navigate(searchHref(text))
  }
  return (
    <form className="dsearch" role="search" onSubmit={submit} data-testid="discover-search">
      <SearchIcon />
      <input type="search" value={text} onChange={(event) => setText(event.target.value)} aria-label={t('search.page.field')} placeholder={t('search.page.placeholder')} maxLength={200} />
      <button type="submit" className="bbtn small primary">
        {t('search.page.go')}
      </button>
    </form>
  )
}

function HitRow({ hit, ticket }: { hit: CatalogHit; ticket: string | null | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const openAt = useOpenAt()
  const line = useHitLine()
  const [editions, setEditions] = useState(false)
  const [gone, setGone] = useState(false)
  const open = () => {
    if (hit.owned_work_id !== null) {
      navigate('/', { state: { goto: hit.owned_work_id, at: Date.now() } })
      return
    }
    // (The page of the hit is made now that the person opens it.)
    tokenOfHit(hit, ticket).then(
      (token) => openAt(`/discover/book/${encodeURIComponent(token)}`, {}),
      () => setGone(true),
    )
  }
  return (
    <li className={`hit hit-${hit.group}`} data-testid="search-hit" data-group={hit.group} data-label={hit.label}>
      <button type="button" className="hit-open" onClick={open} aria-label={t('search.page.open', { title: hit.title })}>
        <HitCover hit={hit} big />
      </button>
      <div className="hit-body">
        <button type="button" className="hit-title" onClick={open}>
          <b dir="auto">{hit.title}</b>
        </button>
        <small dir="auto">{line(hit)}</small>
        <span className="chips">
          <HitChips hit={hit} />
        </span>
        {gone && (
          <p role="alert" className="text-sm text-bad">
            {t('search.hit.gone')}
          </p>
        )}
        <span className="hit-do">
          <AcquireKinds hit={hit} ticket={ticket} />
          {hit.editions.length > 0 && (
            <button type="button" className="bbtn small" aria-expanded={editions} data-testid="hit-editions" onClick={() => setEditions((now) => !now)}>
              {editions ? t('search.hit.editionsHide') : t('search.hit.editionsShow', { count: hit.editions.length })}
            </button>
          )}
        </span>
        {editions && (
          <ul className="hit-editions">
            {hit.editions.map((edition, index) => (
              <EditionLine key={`${edition.source}-${index}`} edition={edition} />
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function People({ people }: { people: AuthorHit[] }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  if (!people.length) return null
  const go = (hit: AuthorHit) => {
    const href = authorHref(hit)
    if (href) navigate(href)
    else
      catalogApi.open(hit.name).then(
        (found) => {
          const target = authorHref(found)
          if (target) navigate(target)
        },
        () => undefined,
      )
  }
  return (
    <section className="dsec">
      <h2 className="hit-head">{t('search.people')}</h2>
      <div className="hit-people">
        {people.map((hit) => (
          <button key={`${hit.person_id ?? ''}-${hit.token ?? ''}-${hit.name}`} type="button" className="res res-au" data-testid="search-person" onClick={() => go(hit)}>
            <AuthorAvatar hit={hit} />
            <span>
              <b dir="auto">{hit.name}</b>
              <small>{hit.works !== null ? t('search.works', { count: hit.works }) : ''}</small>
            </span>
            <span className="res-go">
              {t('search.allBooks')}
              <ArrowRight aria-hidden />
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}

export function DiscoverSearchPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const q = (params.get('q') ?? '').trim()
  const [answer, setAnswer] = useState<CatalogOut | null>(null)
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<'' | 'busy' | 'failed'>('')

  useEffect(() => {
    setAnswer(null)
    setProblem('')
    if (!q) return
    const controller = new AbortController()
    setAsking(true)
    askCatalog(q, controller.signal, setAnswer).then(
      () => !controller.signal.aborted && setAsking(false),
      (error: unknown) => {
        if (controller.signal.aborted) return
        setAsking(false)
        setProblem(tooOften(error) ? 'busy' : 'failed')
      },
    )
    return () => controller.abort()
  }, [q])

  const restingLine = useRestingLine()
  const items = answer?.items ?? []
  const resting = restingLine(answer)
  const series = items.find((item) => item.group === 'series')?.series ?? ''
  return (
    <div className="view" data-testid="discover-search-page">
      <PageHead title={t('search.page.title')} text={t('search.page.text')} />
      <DiscoverSearchField initial={q} />
      {!q && <p className="empty slim">{t('search.page.empty')}</p>}
      {q && asking && !answer && <p className="none">{t('search.searching')}</p>}
      {problem && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {problem === 'busy' ? t('search.catalogBusy') : t('search.catalogFailed')}
        </p>
      )}
      {answer && !answer.available && <p className="none">{t('search.page.off')}</p>}
      {resting && (
        <p className="none" role="status" data-testid="search-resting">
          {resting}
        </p>
      )}
      {answer?.failed && !resting && <p className="none">{t('search.catalogFailed')}</p>}
      <People people={Array.isArray(answer?.people) ? answer.people : []} />
      {GROUPS.map((group) => {
        const shown = items.filter((item) => item.group === group)
        if (!shown.length) return null
        return (
          <section key={group} className="dsec" data-testid={`search-group-${group}`}>
            {group !== 'match' && <h2 className="hit-head">{group === 'series' && series ? t('search.page.group.seriesOf', { series }) : t(`search.page.group.${group}`)}</h2>}
            <ol className="hits">
              {shown.map((hit) => (
                <HitRow key={hit.key} hit={hit} ticket={answer?.ticket} />
              ))}
            </ol>
          </section>
        )
      })}
      {answer?.pending && <p className="none" data-testid="search-pending">{t('search.hit.moreComing')}</p>}
      {answer && answer.available && !answer.pending && !answer.failed && !resting && items.length === 0 && <p className="none">{t('search.none')}</p>}
    </div>
  )
}
