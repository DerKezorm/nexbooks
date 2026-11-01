/**
 * The page of an author: for every person,
 * whether a book of theirs stands on a shelf or not.
 *
 * * **The head**: the round photo with who made it and under which licence (only a photo of a checked licence, else a circle
 *   with the initials), the genres, the name, the years of the life, the other names the person writes under (whoever may
 *   splits one off after a question, as before), a short text in the person's language with its source, the languages the
 *   works came out in, the numbers (works, on the shelf, announced, the years) and a fan of covers.
 * * **"Kommt bald"**: what is announced, with the day in each language.
 * * **"Das Werk"**: every work the sources know, set against what the person has (`components/AuthorWorks.tsx`).
 *
 * An author of the person's libraries is reached by the number of the person (`/person/:id`), any other by the token of the
 * catalogue (`/author/:token`, from the search or the page of a book); a token of an author the person has books of leads to
 * the person's page. A catalogue that is being built is asked for again until it is there (`pollDelay`). The switch
 * "beobachten" stands below the head (`components/WatchCard.tsx`).
 */
import { Eye, Merge } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, catalogApi, worksApi, type AuthorCatalog, type AuthorWatch, type CatalogWork, type PenName, type PersonOut, type PhotoCredit } from '../api/client'
import { AuthorWorks } from '../components/AuthorWorks'
import { Cover } from '../components/BookCard'
import { MergeAuthorDialog } from '../components/MergeAuthorDialog'
import { BackIcon } from '../components/icons'
import { useConfirm } from '../components/useConfirm'
import { WatchCard } from '../components/WatchCard'
import { figure, inkFor, languageName } from '../lib/books'
import { ALL, coverOfWork, defaultLanguage, editionIn, pollDelay, soon as announced, yearsOf, type AuthorFilter } from '../lib/catalog'
import { dayText } from '../lib/discover'
import { errorText } from '../lib/errors'
import { circleColor, initials } from '../lib/listing'
import { useOpenAt, useOpenBook } from '../lib/openBook'
import { genreKey } from '../lib/shelf'
import { useProcure } from '../state/procure'

function Credit({ credit }: { credit: PhotoCredit | null | undefined }) {
  const { t } = useTranslation()
  if (!credit) return null
  // Who made the photo (else where it comes from), then the licence as a link to its text.
  const text = credit.author ? t('person.photoBy', { author: credit.author }) : t(`person.photoFrom_${credit.source}`, { defaultValue: '' })
  if (!text && !credit.license) return null
  return (
    <p className="au-credit" data-testid="photo-credit">
      {credit.page_url ? (
        <a href={credit.page_url} target="_blank" rel="noopener noreferrer" title={t('person.photoPage')}>
          {text || t('person.photoPage')}
        </a>
      ) : (
        text
      )}
      {credit.license && (
        <>
          {text && ' · '}
          {credit.license_url ? (
            <a href={credit.license_url} target="_blank" rel="noopener noreferrer">
              {credit.license}
            </a>
          ) : (
            credit.license
          )}
        </>
      )}
    </p>
  )
}

function Photo({ name, url, genre }: { name: string; url: string | null; genre: string }) {
  const { t } = useTranslation()
  if (url) {
    return (
      <span className="av xl">
        <img src={url} alt={t('authors.photoOf', { name })} decoding="async" draggable={false} />
      </span>
    )
  }
  const color = circleColor(genreKey(genre))
  return (
    <span className="av xl ini" style={{ '--ac': color, color: inkFor(color) } as CSSProperties} role="img" aria-label={t('authors.initialsOf', { name })} data-testid="author-initials">
      <span aria-hidden>{initials(name)}</span>
    </span>
  )
}

/** A work that is announced: its day in each language (the original marked), and what the person can do. */
/**
 * What the person's watch of the author does with a book to come, in the place of "Vormerken" (for example
 * "wird gewünscht, sobald es auf Deutsch angekündigt ist"); null when the watch has nothing to say about it.
 */
function watchSays(t: (key: string, values?: Record<string, unknown>) => string, watch: AuthorWatch | null | undefined, key: string, language: string): string | null {
  if (!watch) return null
  const can = watch.can_acquire
  const waiting = watch.not_yet.find((item) => item.key === key)
  if (waiting) return t('author.soon.watchNotYet', { language: languageName(waiting.language, language) })
  const line = watch.announced.find((item) => item.key === key)
  if (!line) return null
  if (line.state === 'year') return t(can ? 'author.soon.watchYear' : 'author.soon.watchYearList')
  if (line.state === 'listed') return t('author.soon.watchListed')
  if (line.state === 'covered') return t('author.soon.watchCovered')
  if (line.state === 'open') return t(can ? 'author.soon.watchOpen' : 'author.soon.watchOpenList')
  return null
}

function SoonCard({ work, catalog, mine, onOpen }: { work: CatalogWork; catalog: AuthorCatalog; mine: string; onOpen: (work: CatalogWork) => void }) {
  const { t, i18n } = useTranslation()
  const procure = useProcure()
  const says = watchSays(t, catalog.watch, work.key, i18n.language)
  // While the person watches the author, "not announced yet" is said of the language the watch wishes in.
  const wanted = catalog.watch?.target || mine
  const shown = editionIn(work, mine) ?? editionIn(work, ALL)
  const original = work.editions.find((edition) => edition.language === work.orig_language)
  const asked = procure.kindsOf({ workId: work.work?.id ?? null, token: work.token })
  const lines = [...work.editions].sort((a, b) => (a.language === work.orig_language ? -1 : b.language === work.orig_language ? 1 : 0))
  const label = (code: string) => (code === work.orig_language ? t('author.soon.originalTag', { language: languageName(code, i18n.language) }) : languageName(code, i18n.language))
  return (
    <article className="soon-c" data-testid="soon-work">
      <button type="button" className="at-cv" onClick={() => onOpen(work)} aria-label={t('author.open', { title: shown?.title ?? '' })}>
        <Cover work={coverOfWork(work, shown, catalog.name)} />
      </button>
      <div>
        <b dir="auto">{shown?.title}</b>
        {original && shown && original.title !== shown.title && <small dir="auto">{t('author.originalTitle', { title: original.title })}</small>}
        <ul>
          {lines.map((edition) => {
            const day = edition.released.length === 10 ? dayText(edition.released, i18n.language, true) : ''
            if (edition.upcoming) {
              return (
                <li key={edition.language} className="up">
                  {day ? (
                    <>
                      {label(edition.language)}: {t('author.soon.on')} <b>{day}</b>
                    </>
                  ) : (
                    t('author.soon.upcomingYear', { language: label(edition.language), year: edition.year })
                  )}
                </li>
              )
            }
            return <li key={edition.language}>{day && edition.year && Number(edition.released.slice(0, 4)) === edition.year ? t('author.soon.out', { language: label(edition.language), day }) : t('author.soon.outYear', { language: label(edition.language), year: edition.year ?? '' })}</li>
          })}
          {wanted !== work.orig_language && !work.editions.some((edition) => edition.language === wanted) && <li className="no">{t('author.soon.notYet', { language: languageName(wanted, i18n.language) })}</li>}
        </ul>
        {asked.length > 0 ? (
          <span className="rchip ok">{t('author.mark.want', { kind: asked.map((item) => t(`author.kindName.${item.kind}`)).join(', ') })}</span>
        ) : says ? (
          <span className="rchip" data-testid="soon-watch">
            <Eye aria-hidden size={13} />
            {says}
          </span>
        ) : (
          procure.allowed &&
          work.token &&
          !work.work && (
            <button type="button" className="mini-act" onClick={() => procure.open([{ token: work.token!, title: shown?.title ?? '', authors: [catalog.name], series: work.series, series_pos: work.series_pos, color: work.color || '#8e3b46', cover_url: work.cover_url }])}>
              {t('author.reserve')}
            </button>
          )
        )}
      </div>
    </article>
  )
}

export function PersonPage() {
  const { id, token } = useParams()
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const openBook = useOpenBook()
  const openAt = useOpenAt()
  const mine = i18n.language.slice(0, 2)
  const [person, setPerson] = useState<PersonOut | null>(null)
  const [catalog, setCatalog] = useState<AuthorCatalog | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [merging, setMerging] = useState(false)
  const [filter, setFilter] = useState<AuthorFilter | null>(null)
  const { ask, dialog } = useConfirm()
  const polls = useRef(0)

  const readCatalog = useCallback(async () => {
    if (id) return catalogApi.person(Number(id))
    return catalogApi.author(token ?? '')
  }, [id, token])

  useEffect(() => {
    let live = true
    setPerson(null)
    setCatalog(null)
    setFilter(null)
    setFailure(null)
    polls.current = 0
    const gone = (error: unknown) => live && setFailure(error instanceof ApiError ? error.code : 'internal_error')
    if (id) worksApi.person(Number(id), i18n.language).then((found) => live && setPerson(found), gone)
    readCatalog().then((found) => {
      if (!live) return
      if (!id && found.person_id !== null) {
        navigate(`/person/${found.person_id}`, { replace: true })
        return
      }
      setCatalog(Array.isArray(found.works) ? found : null)
    }, gone)
    return () => {
      live = false
    }
  }, [id, token, i18n.language, readCatalog, navigate])

  // A catalogue that is being built (or built again) is asked for again until it is there, while the page is shown (a
  // hidden tab asks again when it is shown).
  const [shownAgain, setShownAgain] = useState(0)
  useEffect(() => {
    const seen = () => {
      if (document.visibilityState === 'visible') setShownAgain((value) => value + 1)
    }
    document.addEventListener('visibilitychange', seen)
    return () => document.removeEventListener('visibilitychange', seen)
  }, [])
  useEffect(() => {
    // (Also while a watch waits for its first look: the beat of the server makes it a few seconds after the build.)
    if (!catalog || !(catalog.state === 'building' || catalog.refreshing || catalog.watch?.state === 'waiting')) return
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'hidden') return
      polls.current += 1
      readCatalog().then((found) => setCatalog(Array.isArray(found.works) ? found : null), () => undefined)
    }, pollDelay(polls.current))
    return () => window.clearTimeout(timer)
  }, [catalog, readCatalog, shownAgain])

  useEffect(() => {
    if (catalog && !filter) setFilter({ language: defaultLanguage(catalog, mine), order: 'kind', look: 'covers', hideOwned: false })
  }, [catalog, filter, mine])

  const split = (pen: PenName) => {
    if (!person) return
    ask({
      title: t('person.splitTitle', { name: pen.name }),
      text: t('person.splitText', { name: pen.name, person: person.name }),
      confirm: t('person.splitConfirm'),
      run: async () => {
        try {
          const next = await worksApi.splitPerson(person.id, pen.id)
          setPerson(next)
          setSaid(t('person.splitDone', { name: pen.name }))
          readCatalog().then((found) => setCatalog(Array.isArray(found.works) ? found : null), () => undefined)
        } catch (error) {
          setFailure(error instanceof ApiError ? error.code : 'internal_error')
        }
      },
    })
  }

  // Two authors that are one: the author that stays is shown (it may be the other one), with a sentence about it.
  const merged = (kept: PersonOut, other: string) => {
    setMerging(false)
    setSaid(t('person.mergeDone', { name: kept.name, other }))
    if (kept.id !== person?.id) {
      navigate(`/person/${kept.id}`, { replace: true })
      return
    }
    setPerson(kept)
    readCatalog().then((found) => setCatalog(Array.isArray(found.works) ? found : null), () => undefined)
  }

  const openWork = (work: CatalogWork) => {
    if (work.work) openBook(work.work.id, work.work)
    else if (work.token) openAt(`/discover/book/${encodeURIComponent(work.token)}`, {})
  }

  const works = useMemo(() => catalog?.works ?? [], [catalog])
  const soonWorks = useMemo(() => announced(works), [works])
  // The fan of covers (as the design): the works with a picture first, then the drawn ones, the newest of each.
  const fan = useMemo(() => {
    const sure = works.filter((work) => work.kind !== 'unsure' && work.editions.some((edition) => edition.ebook))
    const pictured = sure.filter((work) => work.cover_url || work.work?.cover_url)
    return [...pictured, ...sure.filter((work) => !pictured.includes(work))].slice(0, 5)
  }, [works])
  const name = person?.name ?? catalog?.name ?? ''
  const genre = catalog?.genre || person?.wrote[0]?.genre || ''
  // An author nobody of the libraries is: the photo and the text the catalogue found (two of its works prove the item).
  const bio = person ? person.bio : (catalog?.bio ?? null)
  const photoUrl = person ? person.photo_url : (catalog?.photo_url ?? null)
  const credit = person ? person.credit : (catalog?.credit ?? null)
  const born = bio?.born ?? (catalog?.born ? catalog.born : null)
  const died = bio?.died ?? (catalog?.died ? catalog.died : null)
  const life = born && died ? t('person.life', { born, died }) : born ? t('person.born', { year: born }) : died ? t('person.died', { year: died }) : ''
  // The year of death for the note beside the switch "watch" (the sources write it as a year or as a date).
  const diedYear = typeof died === 'number' ? died : (/\b(\d{4})\b/.exec(String(died ?? ''))?.[1] ?? null)
  const pens = person?.pen_names ?? []
  const splitting = Boolean(person?.can_split) && pens.length > 0
  const aliases = [...new Set([...(person?.aliases ?? []), ...(catalog?.aliases ?? [])])]
  const years = catalog ? yearsOf(catalog, new Date().getFullYear()) : null
  const languages = useMemo(() => {
    const counts = new Map<string, number>()
    for (const work of works) for (const edition of work.editions) if (edition.language) counts.set(edition.language, (counts.get(edition.language) ?? 0) + 1)
    return [...counts.keys()].sort((a, b) => (a === catalog?.orig_language ? -1 : b === catalog?.orig_language ? 1 : (counts.get(b) ?? 0) - (counts.get(a) ?? 0)))
  }, [works, catalog])
  const tint = fan.find((work) => !work.work)?.color || fan[0]?.color || circleColor(genreKey(genre))

  return (
    <div className="view acq au-page" id="view-autor">
      <button type="button" className="d-back" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/'))}>
        <BackIcon />
        {t('author.back')}
      </button>
      {said && (
        <p className="empty" role="status">
          {said}
        </p>
      )}
      {failure && (
        <p className="empty" role="alert">
          {failure === 'not_found' ? (id ? t('person.notFound') : t('author.notFound')) : errorText(failure)}
        </p>
      )}
      {!failure && name && (
        <>
          <header className="au-hero" style={{ '--bc': tint } as CSSProperties} data-testid="author-hero">
            <div className="au-glow" />
            <div className="au-photo">
              <Photo name={name} url={photoUrl} genre={genre} />
              <Credit credit={credit} />
            </div>
            <div className="au-main">
              <p className="d-eyebrow">{genre ? t(`library.genre.${genreKey(genre)}`) : ''}</p>
              <h1 className="au-name" dir="auto">
                {name}
              </h1>
              <p className="au-life" data-testid={life ? 'author-life' : undefined}>
                {life}
              </p>
              {splitting ? (
                <ul className="pen-names au-alias" data-testid="pen-names">
                  {pens.map((pen) => (
                    <li key={pen.id}>
                      <span dir="auto">{t('person.alsoAs', { name: pen.name })}</span>{' '}
                      <button type="button" className="d-ghost" aria-label={t('person.splitLabel', { name: pen.name, person: name })} onClick={() => split(pen)}>
                        {t('person.split')}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                aliases.length > 0 && (
                  <p className="au-alias" data-testid="author-alias" dir="auto">
                    {t('person.alias', { names: aliases.join(', ') })}
                  </p>
                )
              )}
              {person?.can_merge && (
                <p className="au-alias">
                  <button type="button" className="d-ghost" onClick={() => setMerging(true)} data-testid="merge-open">
                    <Merge aria-hidden size={13} /> {t('person.merge')}
                  </button>
                </p>
              )}
              {bio?.texts.map((text, index) => (
                <div key={index} data-testid="author-bio">
                  <p className="au-bio" dir="auto">
                    {text.text}
                  </p>
                  {text.url ? (
                    <p className="au-src">
                      <a href={text.url} target="_blank" rel="noopener noreferrer">
                        {t(`person.bioFrom_${text.source}`, { defaultValue: text.source })}
                      </a>
                    </p>
                  ) : null}
                </div>
              ))}
              {languages.length > 0 && (
                <div className="au-langs">
                  <span className="lab">{t('author.languages')}</span>
                  {languages.map((code) => (
                    <span key={code} className="chip">
                      {languageName(code, i18n.language)}
                      {code === catalog?.orig_language ? ` · ${t('author.original')}` : ''}
                    </span>
                  ))}
                </div>
              )}
              <div className="d-facts" data-testid="author-facts">
                <span>
                  <b>{figure(catalog?.works_total ?? person?.wrote.length ?? 0, i18n.language)}</b>
                  {t('author.facts.works')}
                </span>
                <span>
                  <b>{figure(catalog?.on_shelf ?? person?.wrote.length ?? 0, i18n.language)}</b>
                  {t('author.facts.onShelf')}
                </span>
                {catalog && catalog.soon > 0 && (
                  <span>
                    <b>{catalog.soon}</b>
                    {t('author.facts.soon')}
                  </span>
                )}
                {years && (
                  <span>
                    <b>{years[0] === years[1] ? years[0] : t('author.facts.years', { from: years[0], to: years[1] })}</b>
                    {t('author.facts.yearsLabel')}
                  </span>
                )}
              </div>
            </div>
            <div className="au-fan" aria-hidden>
              {fan.map((work, index) => (
                <span key={work.key} className={`fc f${index}`}>
                  <Cover work={coverOfWork(work, editionIn(work, ALL), name)} />
                </span>
              ))}
            </div>
          </header>
          {catalog && <WatchCard catalog={catalog} onChange={setCatalog} died={diedYear} />}
          {catalog && catalog.state !== 'ready' && (
            <p className="au-state" data-testid="catalog-state">
              {t(`author.state.${catalog.state}`, { name })}
            </p>
          )}
          {catalog?.common_name && (
            <p className="au-state" data-testid="common-name">
              {t('author.commonName', { name })}
            </p>
          )}
          {soonWorks.length > 0 && catalog && (
            <section className="au-sec" data-testid="author-soon">
              <div className="au-sec-h">
                <div>
                  <h2>
                    {t('author.soon.title')} <small>{soonWorks.length}</small>
                  </h2>
                  <p>{t('author.soon.text')}</p>
                </div>
              </div>
              <div className="soons">
                {soonWorks.map((work) => (
                  <SoonCard key={work.key} work={work} catalog={catalog} mine={filter?.language && filter.language !== ALL ? filter.language : mine} onOpen={openWork} />
                ))}
              </div>
            </section>
          )}
          {catalog && filter && <AuthorWorks catalog={catalog} onChange={setCatalog} filter={filter} onFilter={setFilter} />}
          {person && person.narrated.length > 0 && (
            <section className="au-sec" data-testid="person-narrated">
              <div className="au-sec-h">
                <div>
                  <h2>
                    {t('person.narrated')} <small>{person.narrated.length}</small>
                  </h2>
                </div>
              </div>
              <div className="agrid">
                {person.narrated.map((work) => (
                  <div className="atile" key={work.id}>
                    <button type="button" className="at-cv" onClick={() => openBook(work.id, work)} aria-label={t('author.open', { title: work.title })}>
                      <Cover work={work} />
                    </button>
                    <button type="button" className="at-t" onClick={() => openBook(work.id, work)} dir="auto">
                      {work.title}
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
      {!failure && !name && (
        <p className="empty">
          <Link to="/">{t('person.back')}</Link>
        </p>
      )}
      {merging && person && <MergeAuthorDialog person={person} onClose={() => setMerging(false)} onMerged={merged} />}
      {dialog}
    </div>
  )
}
