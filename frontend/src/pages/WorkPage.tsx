/**
 * The page of a book, in the colours of the book. It stands
 * over the page it was opened from (the shelf stays where it is, so a way back finds it as it was), has an address of its
 * own (`/work/:id`, also when it is opened by a link or after a reload, then without the turn) and closes with Escape, the
 * button, or the way back of the browser; the focus goes to the page and, afterwards, back to the spine it came from.
 *
 * From a spine the book turns to its cover in two steps (360 ms, then 420 ms), from a cover in the grid the cover grows to
 * its place (520 ms); with `prefers-reduced-motion` nothing moves. The cover leans with the mouse.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams, type Location } from 'react-router-dom'

import { ApiError, discoverApi, editionsApi, historyApi, laterApi, librariesApi, procureApi, readingApi, worksApi, type DiscoverCard, type EditionOut, type HistoryOut, type Library, type ProgressOut, type ReleaseKind, type SeriesGap, type SeriesOut, type PersonOut, type WorkBrief, type WorkOut, type WorkProcure } from '../api/client'
import { Cover } from '../components/BookCard'
import { ManageDialog } from '../components/ManageBook'
import { MoveDialog } from '../components/MoveDialog'
import { MiniShelf } from '../components/Shelf'
import { Spine } from '../components/Spine'
import { Twins } from '../components/Twins'
import { VolumeShelf } from '../components/VolumeShelf'
import { ReleasesDialog } from '../components/procure/ReleasesDialog'
import { UploadDialog } from '../components/procure/UploadDialog'
import { BackIcon, MarkIcon, PencilIcon } from '../components/icons'
import { authorLine } from '../lib/books'
import { leadEditions, useEditionLabel } from '../lib/editionText'
import { errorText } from '../lib/errors'
import { trapTab } from '../lib/focusTrap'
import { reducedMotion } from '../lib/motion'
import { bookLayerStands, leaveBook, type BookRoute } from '../lib/openBook'
import { listOf } from '../lib/say'
import { useDetailTurn } from '../lib/useDetailTurn'
import { useProcure } from '../state/procure'

/** The page fades out this long before it is gone. */
const FADE_OUT_MS = 300
/** How long a sentence like "Gespeichert." stays beside the buttons. */
const STATUS_MS = 4500

type RouteState = BookRoute

function backLabel(background: Location | undefined): string {
  const path = background?.pathname ?? '/'
  if (path === '/') return 'work.back.library'
  if (path.startsWith('/discover')) return 'work.back.discover'
  if (path.startsWith('/stack')) return 'work.back.stack'
  return 'work.back.other'
}

/** A description as paragraphs: a blank line starts one; a single line break inside a paragraph (the file's own) is a space. */
function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((part) => part.replace(/\s*\n\s*/g, ' ').trim())
    .filter(Boolean)
}

/** Puts the book on the person's own list for later, the one the apps show too, and takes it off. */
function LaterButton({ work, listed }: { work: number; listed: boolean }) {
  const { t } = useTranslation()
  const [on, setOn] = useState(listed)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const toggle = async () => {
    setBusy(true)
    setProblem('')
    try {
      await (on ? laterApi.remove(work) : laterApi.add(work))
      setOn(!on)
    } catch (error) {
      setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="want" data-testid="later">
      <button type="button" aria-pressed={on} disabled={busy} onClick={() => void toggle()}>
        <MarkIcon />
        {on ? t('work.laterOn') : t('work.later')}
      </button>
      {problem && (
        <p role="alert" className="d-original" data-testid="later-problem">
          {problem}
        </p>
      )}
    </div>
  )
}

export function WorkPage() {
  const { id } = useParams()
  return <WorkView key={id} id={Number(id)} />
}

function WorkView({ id }: { id: number }) {
  const { t } = useTranslation()
  const { duration } = useEditionLabel()
  const location = useLocation()
  const navigate = useNavigate()
  const state = location.state as RouteState
  const background = state?.background
  const [full, setFull] = useState<WorkOut | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [libraries, setLibraries] = useState<Library[]>([])
  const [series, setSeries] = useState<SeriesOut | null>(null)
  const [person, setPerson] = useState<PersonOut | null>(null)
  // The volumes of the series the person lacks (Discover), and how often the page asked again while they were looked up.
  const [gaps, setGaps] = useState<SeriesGap | null>(null)
  const [lookedUp, setLookedUp] = useState(0)
  const [places, setPlaces] = useState<ProgressOut[]>([])
  const [finished, setFinished] = useState<HistoryOut[]>([])
  const [shown, setShown] = useState(false)
  const [editing, setEditing] = useState(false)
  const [moving, setMoving] = useState<EditionOut | null>(null)
  const [status, setStatus] = useState<string | null>(state?.said ?? null)
  // What the person may look for of this book, and the search that is open.
  const [procure, setProcure] = useState<WorkProcure | null>(null)
  const acquire = useProcure()
  const [releases, setReleases] = useState<ReleaseKind | null>(null)
  const [uploadKind, setUploadKind] = useState<ReleaseKind | null>(null)
  const root = useRef<HTMLElement>(null)
  const cover = useRef<HTMLDivElement>(null)
  const flip = useRef<HTMLDivElement>(null)
  const spineHost = useRef<HTMLDivElement>(null)
  const coverHost = useRef<HTMLDivElement>(null)
  const leaving = useRef(false)

  const brief: WorkBrief | null = full ?? state?.work ?? null

  // A sentence handed over is said once: it is taken out of the history entry, so that a reload does not say it again.
  useEffect(() => {
    if (state?.said) navigate(location.pathname, { replace: true, state: { background: state.background, work: state.work, from: state.from } satisfies RouteState })
    // Once, when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = useCallback(async () => {
    try {
      setFull(await worksApi.get(id))
      setFailure(null)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.code : 'internal_error')
    }
  }, [id])
  // The reading of a recording nothing proves, set by a person (or taken back).
  const setVariant = useCallback(
    async (edition: EditionOut, variant: 'unabridged' | 'abridged' | 'dramatized' | null) => {
      try {
        await editionsApi.correct(edition.id, { variant })
        setStatus(t('work.twin.unprovenSaved'))
        await load()
      } catch (error) {
        setStatus(errorText(error instanceof ApiError ? error.code : 'internal_error'))
      }
    },
    [load, t],
  )
  // A language nexbooks worked out, confirmed by a person.
  const confirmLanguage = useCallback(
    async (edition: EditionOut) => {
      try {
        await editionsApi.correct(edition.id, { language: edition.language })
        setStatus(t('work.twin.unprovenSaved'))
        await load()
      } catch (error) {
        setStatus(errorText(error instanceof ApiError ? error.code : 'internal_error'))
      }
    },
    [load, t],
  )
  useEffect(() => {
    void load()
    librariesApi.mine().then((found) => setLibraries(listOf<Library>(found)), () => undefined)
    // Where the person stands in each edition: theirs alone (the server answers only for the caller).
    readingApi.places(id).then((found) => setPlaces(Array.isArray(found) ? found : []), () => setPlaces([]))
  }, [load, id])
  useEffect(() => {
    // In the languages the operator allows (settings, "Sprachen"): the language of the page plays no part.
    procureApi.work(id).then((found) => setProcure(found && Array.isArray(found.kinds) ? found : null), () => setProcure(null))
  }, [id])
  // The days the person finished the book: theirs alone.
  const loadFinished = useCallback(() => historyApi.ofWork(id).then((found) => setFinished(Array.isArray(found) ? found : []), () => setFinished([])), [id])
  useEffect(() => {
    void loadFinished()
  }, [loadFinished])
  const seriesId = full?.series?.id
  const authorId = full?.authors[0]?.id
  useEffect(() => {
    if (seriesId) worksApi.series(seriesId).then(setSeries, () => setSeries(null))
    else setSeries(null)
  }, [seriesId, full?.editions.length])
  useEffect(() => {
    if (authorId) worksApi.person(authorId).then(setPerson, () => setPerson(null))
    else setPerson(null)
  }, [authorId, full?.editions.length])
  // The gaps of the series: what the sources say, set against the shelf. Nothing here is needed to read the book, so a
  // failure (no source on, none answering) is a series without ghosts.
  useEffect(() => {
    if (seriesId) discoverApi.series(seriesId).then((found) => setGaps(Array.isArray(found?.volumes) ? found : null), () => setGaps(null))
    else setGaps(null)
  }, [seriesId, full?.editions.length, lookedUp])
  const gapsPending = gaps?.pending ?? false
  useEffect(() => {
    if (!gapsPending || lookedUp >= 6) return
    const timer = window.setTimeout(() => setLookedUp((n) => n + 1), 5000)
    return () => window.clearTimeout(timer)
  }, [gapsPending, lookedUp])

  // The page lies over the shelf: the shelf does not scroll under it, and a page that shows books opens no second one.
  useEffect(() => {
    document.body.classList.add('lock')
    const gone = bookLayerStands()
    return () => {
      document.body.classList.remove('lock')
      gone()
    }
  }, [])

  // The Tab key stays on the page while it lies over the shelf (a dialog drawn above it is outside of it and has its own).
  useEffect(() => {
    const layer = root.current
    if (!layer) return
    const key = (event: KeyboardEvent) => void trapTab(event, layer)
    layer.addEventListener('keydown', key)
    return () => layer.removeEventListener('keydown', key)
  }, [])

  // The focus: onto the page, and afterwards back onto the spine (or cover) the book came from.
  useEffect(() => {
    root.current?.focus({ preventScroll: true })
    return () => {
      if (background) document.querySelector<HTMLElement>(`.view [data-work="${id}"]`)?.focus({ preventScroll: true })
    }
  }, [id, background])

  // The turn (`lib/useDetailTurn.ts`): once, when the page is first drawn with the book it was opened from.
  useDetailTurn(brief !== null, background ? `.view [data-work="${id}"]` : null, { cover, flip, spineHost, coverHost }, setShown)

  const close = useCallback(() => {
    if (leaving.current) return
    leaving.current = true
    const done = () => leaveBook(state, navigate)
    if (reducedMotion()) {
      done()
      return
    }
    setShown(false)
    window.setTimeout(done, FADE_OUT_MS)
  }, [state, navigate])

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [close])

  useEffect(() => {
    if (!status) return
    const timer = window.setTimeout(() => setStatus(null), STATUS_MS)
    return () => window.clearTimeout(timer)
  }, [status])

  const tilt = (event: PointerEvent<HTMLDivElement>) => {
    const element = cover.current?.querySelector<HTMLElement>('.cover')
    if (!element) return
    const box = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - box.left) / box.width - 0.5
    const y = (event.clientY - box.top) / box.height - 0.5
    element.style.transform = `rotateY(${x * 14}deg) rotateX(${-y * 10}deg)`
  }
  const untilt = () => {
    const element = cover.current?.querySelector<HTMLElement>('.cover')
    if (element) element.style.transform = ''
  }

  // Another book of the page (a volume of the series, a book of the author): this page is replaced, without a turn.
  const goTo = (work: WorkBrief) => navigate(`/work/${work.id}`, { replace: true, state: { background, work, from: state?.from } satisfies RouteState })
  const goBook = (card: DiscoverCard) =>
    navigate(card.owned_work_id !== null ? `/work/${card.owned_work_id}` : `/discover/book/${encodeURIComponent(card.token)}`, {
      replace: true,
      state: { background, ...(card.owned_work_id !== null ? {} : { card }), from: state?.from } satisfies RouteState,
    })

  const rightOf = (libraryId: number) => libraries.find((library) => library.id === libraryId)?.my_right
  const manageable = (full?.editions ?? []).filter((edition) => rightOf(edition.library_id) === 'manage')

  const eyebrowGenre = brief?.genre ? t(`library.genre.${brief.genre}`) : ''
  const eyebrowSeries = brief?.series ? (brief.series.pos !== null ? t('work.eyebrowSeries', { series: brief.series.name, pos: brief.series.pos }) : brief.series.name) : ''
  const eyebrow = [eyebrowGenre, eyebrowSeries].filter(Boolean).join(' · ')
  const style = { '--bc': brief?.color ?? 'var(--accent)' } as CSSProperties
  // The listening time is that of the recording the card shows (the unabridged one if there is one).
  const firstAudio = full ? leadEditions(full).audio : undefined
  const volumes = series?.works ?? []
  // The small shelf shows ghosts for the volumes the person lacks, once the sources have said which there are.
  const ghosted = (gaps?.volumes ?? []).some((volume) => volume.book !== null)
  // "More by" leaves out the book itself and the volumes of the series that stands above it.
  const inSeries = new Set(volumes.length > 1 ? volumes.map((work) => work.id) : [])
  const more = (person?.wrote ?? []).filter((work) => work.id !== id && !inSeries.has(work.id)).slice(0, 18)

  return (
    <>
    <section ref={root} className={`detail ${shown ? 'show' : ''}`} style={style} role="dialog" aria-modal="true" aria-label={brief?.title ?? t('work.loading')} tabIndex={-1} data-testid="work">
      <div className="d-glow" />
      <div className="d-wrap">
        <div className="d-bar">
          <button type="button" className="d-back" data-close onClick={close}>
            <BackIcon />
            {t(backLabel(background))}
          </button>
          <div className="flex items-center gap-3">
            {status && (
              <span role="status" className="text-sm text-muted" data-testid="work-status">
                {status}
              </span>
            )}
            {full && manageable.length > 0 && (
              <button type="button" className="d-ghost" onClick={() => setEditing(true)} data-testid="edit-book">
                <PencilIcon />
                {t('work.edit.open')}
              </button>
            )}
          </div>
        </div>
        {failure && (
          <p className="empty" role="alert" data-testid="work-failure">
            {failure === 'not_found' ? t('work.notFound') : errorText(failure)}
          </p>
        )}
        {!brief && !failure && <p className="empty">{t('work.loading')}</p>}
        {brief && (
          <>
            <div className="d-hero">
              <div className="d-cover" ref={cover} onPointerMove={tilt} onPointerLeave={untilt}>
                <Cover work={brief} />
              </div>
              <div>
                {eyebrow && (
                  <p className="d-eyebrow" dir="auto">
                    {eyebrowGenre}
                    {/* A genre nobody said is a guess from the author's other books: the page says so, quietly. */}
                    {eyebrowGenre && brief?.genre_guess && (
                      <span className="d-guess" title={t('work.genreGuessHint')} data-testid="genre-guess">
                        {' '}
                        ({t('work.genreGuess')})
                      </span>
                    )}
                    {eyebrowGenre && eyebrowSeries && ' · '}
                    {eyebrowSeries}
                  </p>
                )}
                <h1 className="d-title" dir="auto">
                  {brief.title}
                </h1>
                {brief.authors.length > 0 && (
                  <p className="d-by">
                    {t('work.by')}{' '}
                    {brief.authors.map((author, index) => (
                      <span key={author.id}>
                        {index > 0 && ', '}
                        <Link to={`/person/${author.id}`} dir="auto">
                          {author.name}
                        </Link>
                        {author.aliases && author.aliases.length > 0 && (
                          <span className="d-alias" dir="auto">
                            {' '}
                            {t('work.alias', { names: author.aliases.join(', ') })}
                          </span>
                        )}
                      </span>
                    ))}
                  </p>
                )}
                {full && <LaterButton key={full.id} work={full.id} listed={Boolean(full.later)} />}
                {full?.description && (
                  <div className="d-blurb">
                    {paragraphs(full.description).map((text, index) => (
                      <p key={index} dir="auto">
                        {text}
                      </p>
                    ))}
                  </div>
                )}
                <div className="d-facts">
                  {brief.year && (
                    <span>
                      <b>{brief.year}</b>
                      {t('work.facts.year')}
                    </span>
                  )}
                  {brief.pages > 0 && (
                    <span>
                      <b>{brief.pages}</b>
                      {t('work.facts.pages')}
                    </span>
                  )}
                  <span>
                    <b>{brief.editions.length}</b>
                    {t('work.facts.editions', { count: brief.editions.length })}
                  </span>
                  {firstAudio && (
                    <span>
                      <b>{duration(firstAudio.duration)}</b>
                      {t('work.facts.listen')}
                    </span>
                  )}
                </div>
              </div>
            </div>
            {full && (
              <>
                <h3 className="d-h">{t(full.editions.length === 1 ? 'work.editionsOne' : 'work.editionsMany')}</h3>
                <Twins work={full} places={places} canSet={(libraryId) => rightOf(libraryId) === 'manage'} onVariant={setVariant} onLanguage={confirmLanguage} history={finished} onHistory={() => void loadFinished()} onSaid={setStatus} onMove={setMoving} procuring={procure ? { status: procure, onReleases: setReleases, onUpload: setUploadKind, workId: full.id, onAcquire: (kind) => acquire.open([{ workId: full.id, title: full.title, authors: full.authors.map((person) => person.name), series: full.series?.name ?? '', series_pos: full.series?.pos ?? null, color: full.color, cover_url: full.cover_url }], { kinds: [kind] }) } : undefined} />
                {full.series && ghosted && gaps && (
                  <>
                    <h3 className="d-h">{t('work.series', { name: full.series.name })}</h3>
                    <VolumeShelf volumes={gaps.volumes} current={{ work: id }} label={full.series.name} onOpenWork={goTo} onOpenBook={goBook} />
                  </>
                )}
                {volumes.length > 1 && full.series && !ghosted && (
                  <>
                    <h3 className="d-h">{t('work.series', { name: full.series.name })}</h3>
                    <MiniShelf works={volumes} current={id} onOpen={goTo} label={full.series.name} />
                  </>
                )}
                {more.length > 0 && full.authors[0] && (
                  <>
                    <h3 className="d-h">{t('work.moreOf', { name: full.authors[0].name })}</h3>
                    <MiniShelf works={more} onOpen={goTo} label={authorLine(full)} />
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
      {uploadKind && full && <UploadDialog workId={full.id} kind={uploadKind} title={full.title} onClose={() => setUploadKind(null)} />}
      {releases && full && (
        <ReleasesDialog workId={full.id} title={full.title} author={full.authors[0]?.name ?? ''} kind={releases} onClose={() => setReleases(null)} onLoaded={setStatus} />
      )}
      {moving && (
        <MoveDialog
          edition={moving}
          onClose={() => setMoving(null)}
          onMoved={(said) => {
            setStatus(said)
            void load()
          }}
        />
      )}
      {editing && full && manageable.length > 0 && (
        <ManageDialog
          work={full}
          editions={manageable}
          onMove={(edition) => {
            setEditing(false)
            setMoving(edition)
          }}
          onClose={() => setEditing(false)}
          onGone={() => navigate('/', { replace: true })}
          onChanged={(workId, said) => {
            setStatus(said)
            if (workId === id) void load()
            else navigate(`/work/${workId}`, { replace: true, state: { background, said, from: state?.from } satisfies RouteState })
          }}
        />
      )}
    </section>
    {/* The turn is drawn over everything, outside the page (which is faded in meanwhile). */}
    <div className="flip" ref={flip} hidden>
      <div className="f-spine" ref={spineHost} style={{ display: 'none' }}>
        {brief && <Spine work={brief} />}
      </div>
      <div className="f-cover" ref={coverHost} style={{ display: 'none' }}>
        {brief && <Cover work={brief} />}
      </div>
    </div>
    </>
  )
}
