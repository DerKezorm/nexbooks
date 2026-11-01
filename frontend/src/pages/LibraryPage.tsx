/**
 * The library: the line of numbers, the choice between the shelf of spines and the grid of covers (kept in the profile),
 * the genres as chips with their numbers, and the books. The books come in pages of 200 as long as there are more, in the
 * order of the shelf (genre, author, series, volume), and show as they arrive; a section far down is not drawn until it
 * comes near. A press on a spine or a cover opens the page of the book over the shelf (`WorkPage`, the shelf stays where it
 * is); a hit of the search leads here with the number of the book (`state.goto`): the shelf scrolls to the spine, which
 * glows, and then the book opens.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ApiError, authApi, laterApi, librariesApi, worksApi, type BookStats, type Library, type LibraryView, type ListSort, type WorkBrief, type WorkRow } from '../api/client'
import { AuthorsGrid, type AuthorSort } from '../components/AuthorsGrid'
import { BookCard } from '../components/BookCard'
import { ListView, ROW_PHONE_PX, ROW_PX } from '../components/ListView'
import { PageHead } from '../components/PageHead'
import { ShelfSections, SpineTip } from '../components/Shelf'
import { IncomingSection } from '../components/procure/Incoming'
import { UploadDialog } from '../components/procure/UploadDialog'
import { UploadIcon } from '../components/icons'
import { GridIcon, ListIcon, ShelfIcon } from '../components/icons'
import { figure } from '../lib/books'
import { timeOfMoment } from '../lib/dates'
import { errorText } from '../lib/errors'
import { firstDirection, sortWorks } from '../lib/listing'
import { reducedMotion } from '../lib/motion'
import { useOpenBook } from '../lib/openBook'
import { GENRES, genreKey, useSpineTip, type GenreKey } from '../lib/shelf'
import { listOf } from '../lib/say'
import { useAuth } from '../state/auth'
import { useIncoming, useIncomingBooks, useProcure } from '../state/procure'

/** How many books one request brings. */
export const PAGE = 200
/** While a scan runs, the page looks again this often (milliseconds). */
const POLL_MS = 3000
/** While a scan runs and books come in, the list of books is fetched anew at most this often (a big library has many pages). */
const RELOAD_MS = 8000
/** How long the spine glows before the book opens, and how long the glow lasts. */
const FOUND_OPEN_MS = 1100
const FOUND_GLOW_MS = 2400

/** What the shelves show: the books that are here, the ones on their way, or both. */
type Stock = 'all' | 'available' | 'incoming'
const STOCKS: Stock[] = ['all', 'available', 'incoming']

type Section = 'books' | 'authors' | 'later'

/** The person's own list for later, the newest first. Read again when a book that was open closes: it may have left. */
function LaterShelf({ at, onOpen }: { at: string; onOpen: (work: WorkBrief) => void }) {
  const { t } = useTranslation()
  const [works, setWorks] = useState<WorkBrief[] | null>(null)
  useEffect(() => {
    let live = true
    laterApi.list().then(
      (found) => live && setWorks(found.items),
      () => live && setWorks([]),
    )
    return () => {
      live = false
    }
  }, [at])
  if (!works) return null
  if (works.length === 0) {
    return (
      <p className="empty" data-testid="later-nothing">
        {t('library.laterNothing')}
      </p>
    )
  }
  return (
    <div className="shelf-grid" data-testid="later-grid">
      {works.map((work) => (
        <BookCard key={work.id} work={work} onOpen={onOpen} />
      ))}
    </div>
  )
}

export function LibraryPage() {
  const { t, i18n } = useTranslation()
  const { me, setMe } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const operator = me?.role === 'operator'
  const [libraries, setLibraries] = useState<Library[] | null>(null)
  const [works, setWorks] = useState<WorkRow[]>([])
  const [loaded, setLoaded] = useState(false)
  const [stats, setStats] = useState<BookStats | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [genre, setGenre] = useState<GenreKey | null>(null)
  const [stock, setStock] = useState<Stock>('available')
  const [view, setView] = useState<LibraryView>(me?.profile?.view === 'grid' || me?.profile?.view === 'list' ? me.profile.view : 'shelf')
  const [section, setSection] = useState<Section>(me?.profile?.library_section === 'authors' || me?.profile?.library_section === 'later' ? me.profile.library_section : 'books')
  const [sort, setSort] = useState<ListSort>(me?.profile?.list_sort ?? 'shelf')
  const [dir, setDir] = useState<'asc' | 'desc'>(me?.profile?.list_dir === 'desc' ? 'desc' : 'asc')
  const [authorSort, setAuthorSort] = useState<AuthorSort>(me?.profile?.authors_sort ?? 'name')
  const [found, setFound] = useState<number | null>(null)
  // Changes when the library changed under the page (a scan added or removed books): the books are fetched anew.
  const [version, setVersion] = useState(0)
  const signature = useRef('')
  const reloadedAt = useRef(0)
  const shown = useRef(0)
  shown.current = works.length
  // The list view asks for more of each book (the person's own place, the matching), the others do not.
  const extras = view === 'list' ? 'list' : undefined
  // What the books were last fetched as (the version of the library and the extras): a list that was fetched with the
  // extras serves the shelf too, so that going back to the shelf asks for nothing.
  const fetched = useRef('')

  const load = useCallback(async () => {
    try {
      const mine = listOf<Library>(await librariesApi.mine())
      setLibraries(mine)
      if (mine.length === 0) {
        setWorks([])
        setStats(null)
        setProblem(null)
        setLoaded(true)
        return
      }
      const numbers = await worksApi.stats()
      setStats(numbers)
      setProblem(null)
      const now = `${numbers.works}:${numbers.editions}:${numbers.works_with_audio}`
      // Books that come in while a scan runs show as they come, but the list is not fetched anew at every look.
      const reading = mine.some((library) => library.state !== 'idle')
      if (now !== signature.current && (!reading || signature.current === '' || Date.now() - reloadedAt.current >= RELOAD_MS)) {
        signature.current = now
        reloadedAt.current = Date.now()
        setVersion((v) => v + 1)
      }
    } catch (error) {
      setProblem(error instanceof ApiError ? error.code : 'internal_error')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // The books, a page at a time. They show as they come when nothing is shown yet; a second reading (the library changed)
  // replaces the list when it is whole, so that the shelf does not jump.
  const has = libraries !== null && libraries.length > 0
  useEffect(() => {
    if (!has || version === 0) return
    const key = `${version}|${extras ?? ''}`
    if (fetched.current === key || (!extras && fetched.current === `${version}|list`)) return
    const controller = new AbortController()
    let cancelled = false
    const progressive = shown.current === 0
    void (async () => {
      const collected: WorkRow[] = []
      const known = new Set<number>()
      let total = Infinity
      try {
        for (let page = 1; collected.length < total && !cancelled; page++) {
          const answer = await worksApi.list(page, PAGE, undefined, controller.signal, extras)
          const items = listOf<WorkRow>(answer.items)
          total = Number(answer.total) || 0
          for (const item of items) {
            if (!known.has(item.id)) {
              known.add(item.id)
              collected.push(item)
            }
          }
          if (cancelled) return
          if (progressive) setWorks([...collected])
          if (items.length === 0) break
        }
        if (!cancelled) {
          if (!progressive) setWorks(collected)
          fetched.current = key
          setLoaded(true)
        }
      } catch (error) {
        if (!cancelled && !(error instanceof ApiError && error.code === 'cancelled')) setProblem(error instanceof ApiError ? error.code : 'internal_error')
      }
    })()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [has, version, extras])

  // The books the matching is not sure about, in the libraries this person looks after (the server counts them for no one else).
  const toLook = libraries?.filter((library) => library.my_right === 'manage' && library.counts.review > 0) ?? []
  const review = { count: toLook.reduce((sum, library) => sum + library.counts.review, 0) }
  // The matching with the sources, in the libraries this person looks after: how many books are looked at of all that have a
  // place in it, and the sources it waits for because they rest. A line while books wait or the matching runs.
  // (A library whose books wait while no source is on is not being matched: nothing is said of it.)
  const managed = libraries?.filter((library) => library.my_right === 'manage' && library.matching !== 'off') ?? []
  const matchEntries = managed.reduce((sum, library) => sum + (library.counts.entries ?? 0), 0)
  const matchPending = managed.reduce((sum, library) => sum + (library.counts.pending ?? 0), 0)
  const matchRunning = managed.some((library) => (library.matching ?? 'idle') !== 'idle')
  const showMatching = matchEntries > 0 && (matchPending > 0 || matchRunning)
  const resting = new Map<string, string>()
  for (const library of managed) for (const rest of library.resting ?? []) if (!resting.has(rest.source)) resting.set(rest.source, rest.until)
  const busy = libraries?.some((library) => library.state !== 'idle') ?? false
  const watching = busy || showMatching
  // The libraries being read now, with how far they are: a line for each, under the numbers.
  const running = libraries?.filter((library) => library.state === 'running') ?? []
  useEffect(() => {
    if (!watching) return
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [watching, load])

  // The page under the book is this page as it stands, without the number a search may have brought (else the page, drawn
  // under the book, would take it as a new hit and lead away again).
  const openBook = useOpenBook()
  const open = useCallback((work: WorkBrief) => openBook(work.id, work), [openBook])

  const incoming = useIncoming()
  const list = useMemo(() => {
    const ofGenre = genre ? works.filter((work) => genreKey(work.genre) === genre) : works
    return stock === 'incoming' ? ofGenre.filter((work) => incoming(work) !== null) : ofGenre
  }, [genre, works, stock, incoming])
  const sortedList = useMemo(() => (view === 'list' ? sortWorks(list, sort, dir, i18n.language) : list), [view, list, sort, dir, i18n.language])

  // A hit of the search: to the spine (the genre filter off if it hides the book), a glow, then the book.
  const arrived = (location.state as { goto?: number; at?: number } | null)?.goto
  const [wanted, setWanted] = useState<number | null>(null)
  useEffect(() => {
    if (typeof arrived !== 'number') return
    setWanted(arrived)
    navigate(location.pathname, { replace: true, state: null })
  }, [arrived, location.pathname, navigate])
  const tip = useSpineTip(works)
  // "Hochladen": for who may bring books into a library (the right ``acquire`` somewhere).
  const mayUpload = useProcure().allowed
  const onTheWay = useIncomingBooks(genre).length
  const [uploading, setUploading] = useState(false)
  useEffect(() => {
    if (wanted === null) return
    const work = works.find((item) => item.id === wanted)
    if (!work) {
      // Not here (yet): wait for the rest of the books; if they are all here and it is not among them, open it as it is.
      if (loaded) {
        setWanted(null)
        openBook(wanted)
      }
      return
    }
    if (genre && genreKey(work.genre) !== genre) {
      setGenre(null)
      return
    }
    if (stock === 'incoming' && incoming(work) === null) {
      setStock('available')
      return
    }
    const calm = reducedMotion()
    const element = document.querySelector<HTMLElement>(`.view [data-work="${work.id}"]`)
    if (!element) {
      if (section === 'authors') {
        // The books are not drawn while the authors are shown: the book opens as it is.
        setWanted(null)
        open(work)
        return
      }
      if (view !== 'list') return
      // The list draws only the rows near the screen: the row of a book far down is not there to be found. Its place is
      // known (the rows are of one height), so the page goes there, and the book opens.
      const index = sortedList.findIndex((item) => item.id === work.id)
      const box = document.querySelector<HTMLElement>('.view .list-table')
      if (index < 0 || !box) return
      setWanted(null)
      const height = window.innerWidth <= 820 ? ROW_PHONE_PX : ROW_PX
      const head = box.querySelector('thead')?.getBoundingClientRect().height ?? 0
      const top = box.getBoundingClientRect().top + window.scrollY + head + index * height - (window.innerHeight - height) / 2
      window.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
      window.setTimeout(() => open(work), calm ? 0 : FOUND_OPEN_MS)
      return
    }
    setWanted(null)
    element.scrollIntoView({ block: 'center', behavior: calm ? 'auto' : 'smooth' })
    if (!calm && element.classList.contains('spine')) {
      setFound(work.id)
      window.setTimeout(() => setFound((now) => (now === work.id ? null : now)), FOUND_GLOW_MS)
    }
    // The timer is not cleared with this effect: it ends with `setWanted(null)` above, which would otherwise cancel it.
    window.setTimeout(() => open(work), calm ? 0 : FOUND_OPEN_MS)
  }, [wanted, works, loaded, genre, stock, incoming, view, section, sortedList, open, openBook])

  // What the person chose is kept in their profile; if the server refuses, the choice goes back.
  const keep = <T,>(change: Parameters<typeof authApi.preferences>[0], set: (value: T) => void, before: T) =>
    authApi.preferences(change).then(
      (profile) => me && setMe?.({ ...me, profile }),
      () => set(before),
    )
  const choose = (next: LibraryView) => {
    if (next === view) return
    const before = view
    setView(next)
    void keep({ view: next }, setView, before)
  }
  const chooseSection = (next: Section) => {
    if (next === section) return
    const before = section
    setSection(next)
    void keep({ library_section: next }, setSection, before)
  }
  const chooseAuthorSort = (next: AuthorSort) => {
    const before = authorSort
    setAuthorSort(next)
    void keep({ authors_sort: next }, setAuthorSort, before)
  }
  // A column is chosen: the same column again turns the order round; another starts the way it is natural for it.
  const chooseSort = (column: ListSort) => {
    const [beforeSort, beforeDir] = [sort, dir]
    const nextDir = column === sort ? (dir === 'asc' ? 'desc' : 'asc') : firstDirection(column)
    setSort(column)
    setDir(nextDir)
    void keep({ list_sort: column, list_dir: nextDir }, () => (setSort(beforeSort), setDir(beforeDir)), null)
  }

  const language = i18n.language
  const line =
    stats && stats.works > 0 ? (
      <span data-testid="library-stats">
        <b>{figure(stats.works, language)}</b> {t('library.works', { count: stats.works })} · <b>{figure(stats.editions, language)}</b> {t('library.editions', { count: stats.editions })} ·{' '}
        <b>{figure(stats.works_with_audio, language)}</b> {t('library.withAudio')}
      </span>
    ) : (
      t('library.text')
    )
  const counts = stats?.genres ?? {}

  return (
    <div className="view">
      <PageHead title={t('library.title')} text={line}>
        {(works.length > 0 || section !== 'books' || mayUpload) && (
          <div className="head-tools">
            {mayUpload && (
              <div className="seg">
                <button type="button" onClick={() => setUploading(true)} data-act="upload" data-testid="library-upload">
                  <UploadIcon />
                  {t('upload.button')}
                </button>
              </div>
            )}
            {(works.length > 0 || section !== 'books') && (
            <div className="seg" id="section" role="group" aria-label={t('library.section.label')}>
              <button type="button" className={section === 'books' ? 'on' : ''} aria-pressed={section === 'books'} onClick={() => chooseSection('books')} data-section="books">
                {t('library.section.books')}
              </button>
              <button type="button" className={section === 'authors' ? 'on' : ''} aria-pressed={section === 'authors'} onClick={() => chooseSection('authors')} data-section="authors">
                {t('library.section.authors')}
              </button>
              <button type="button" className={section === 'later' ? 'on' : ''} aria-pressed={section === 'later'} onClick={() => chooseSection('later')} data-testid="section-later">
                {t('library.section.later')}
              </button>
            </div>
            )}
            {section === 'books' && works.length > 0 && (
              <div className="seg" id="look" role="group" aria-label={t('library.look.label')}>
                <button type="button" className={view === 'shelf' ? 'on' : ''} aria-pressed={view === 'shelf'} onClick={() => choose('shelf')} data-look="shelf">
                  <ShelfIcon />
                  {t('library.look.shelf')}
                </button>
                <button type="button" className={view === 'grid' ? 'on' : ''} aria-pressed={view === 'grid'} onClick={() => choose('grid')} data-look="grid">
                  <GridIcon />
                  {t('library.look.grid')}
                </button>
                <button type="button" className={view === 'list' ? 'on' : ''} aria-pressed={view === 'list'} onClick={() => choose('list')} data-look="list">
                  <ListIcon />
                  {t('library.look.list')}
                </button>
              </div>
            )}
          </div>
        )}
      </PageHead>
      {uploading && <UploadDialog onClose={() => setUploading(false)} />}
      {problem && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {errorText(problem)}
        </p>
      )}
      {libraries && libraries.length === 0 && (
        <p className="empty" data-testid="library-empty">
          {operator ? <Trans i18nKey="library.emptyOperator" components={{ settings: <Link to="/settings?tab=libraries" /> }} /> : t('library.emptyMember')}
        </p>
      )}
      {review.count > 0 && (
        <Link to="/review" className="review-hint" data-testid="review-hint">
          <span>
            {t('library.reviewHint', { count: review.count })}
            {matchPending > 0 && (
              <small className="review-quiet" data-testid="review-pending">
                {' '}
                · {t('library.reviewPending', { count: matchPending })}
              </small>
            )}
          </span>
          <b>{t('library.reviewOpen')}</b>
        </Link>
      )}
      {running.length > 0 && (
        <div className="scanning" data-testid="scanning">
          {running.map((library) => {
            const done = library.progress?.done ?? 0
            const total = library.progress?.total ?? 0
            return (
              <div key={library.id} className="scan-row" data-testid="scan-row">
                <span className="scan-text">
                  {total > 0 ? t('library.scanning', { name: library.name, done: figure(done, language), total: figure(total, language) }) : t('library.scanningNone', { name: library.name })}
                </span>
                {operator && (
                  <Link to="/settings?tab=libraries" className="scan-link">
                    {t('library.scanningSettings')}
                  </Link>
                )}
                {total > 0 && (
                  <div className="scan-bar" role="progressbar" aria-label={t('library.scanningBar', { name: library.name })} aria-valuetext={t('library.barValue', { done: figure(Math.min(done, total), language), total: figure(total, language) })} aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(done, total)}>
                    <i style={{ width: `${Math.min(100, Math.round((done / total) * 100))}%` }} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      {showMatching && (
        <div className="scanning" data-testid="matching">
          <div className="scan-row" data-testid="matching-row">
            <span className="scan-text">
              {t('library.matching', { done: figure(Math.max(0, matchEntries - matchPending), language), total: figure(matchEntries, language) })}
              {[...resting].map(([source, until]) => (
                <span key={source} className="scan-rest" data-testid="matching-rest">
                  {' '}
                  · {t('library.restingUntil', { source: t(`sources.short.${source}`), time: timeOfMoment(until, language, me?.profile?.timezone) })}
                </span>
              ))}
            </span>
            <div className="scan-bar" role="progressbar" aria-label={t('library.matchingBar')} aria-valuetext={t('library.barValue', { done: figure(Math.max(0, matchEntries - matchPending), language), total: figure(matchEntries, language) })} aria-valuemin={0} aria-valuemax={matchEntries} aria-valuenow={Math.max(0, matchEntries - matchPending)}>
              <i style={{ width: `${Math.min(100, Math.round(((matchEntries - matchPending) / matchEntries) * 100))}%` }} />
            </div>
          </div>
        </div>
      )}
      {libraries && libraries.length > 0 && works.length === 0 && !problem && (
        <p className="empty" data-testid="library-nothing">
          {busy ? t('library.reading') : loaded && stats?.works === 0 ? t('library.noBooks') : t('library.loading')}
        </p>
      )}
      {section === 'authors' && libraries && libraries.length > 0 && <AuthorsGrid sort={authorSort} onSort={chooseAuthorSort} />}
      {section === 'later' && <LaterShelf at={location.pathname} onOpen={open} />}
      {section === 'books' && works.length > 0 && (
        <>
          <div className="lib-filters">
            <div className="genres" role="group" aria-label={t('library.genres')} id="genres">
              <button type="button" className={`g ${genre === null ? 'on' : ''}`} aria-pressed={genre === null} onClick={() => setGenre(null)} data-g="">
                {t('library.allGenres')} <small>{stats?.works ?? works.length}</small>
              </button>
              {GENRES.map((key) => {
                const count = counts[key] ?? 0
                return count > 0 ? (
                  <button key={key} type="button" className={`g ${genre === key ? 'on' : ''}`} aria-pressed={genre === key} onClick={() => setGenre(key)} data-g={key}>
                    {t(`library.genre.${key}`)} <small>{count}</small>
                  </button>
                ) : null
              })}
            </div>
            {mayUpload && (
              <div className="seg small" id="stock" role="group" aria-label={t('library.stock.label')}>
                {STOCKS.map((key) => (
                  <button key={key} type="button" className={stock === key ? 'on' : ''} aria-pressed={stock === key} onClick={() => setStock(key)} data-stock={key}>
                    {t(`library.stock.${key}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
          {stock === 'incoming' && onTheWay === 0 && list.length === 0 && (
            <p className="empty" data-testid="stock-nothing">
              {t('library.stock.nothing')}
            </p>
          )}
          {view === 'shelf' ? (
            <div id="library" data-testid="shelf" {...tip.handlers}>
              {stock !== 'available' && <IncomingSection look="spines" genre={genre} />}
              <ShelfSections works={list} counts={stock === 'incoming' ? {} : counts} found={found} onOpen={open} />
            </div>
          ) : view === 'list' ? (
            <div id="library">
              {stock === 'incoming' && <IncomingSection look="covers" genre={genre} />}
              <ListView works={sortedList} libraries={libraries ?? []} sort={sort} dir={dir} onSort={chooseSort} onOpen={(work) => open(work)} />
            </div>
          ) : (
            <>
              {stock !== 'available' && <IncomingSection look="covers" genre={genre} />}
              <div className="shelf-grid" data-testid="grid" id="library">
                {list.map((work) => (
                  <BookCard key={work.id} work={work} onOpen={open} incoming={incoming(work)} />
                ))}
              </div>
            </>
          )}
          {!loaded && (
            <p className="empty" data-testid="library-loading-more">
              {t('library.loadingMore')}
            </p>
          )}
          <SpineTip tip={tip.tip} />
        </>
      )}
    </div>
  )
}
