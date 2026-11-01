/**
 * The page of a book of a source that is not on a shelf, in the
 * colours of the book like the page of a book of the library (`WorkPage`): the cover and its colour come from the server, the
 * forms the sources know the book in stand as the twins (an e-book, a recording with narrator and running time, and as chips
 * the abridged one or the play), with "Später: beschaffen" shut for now; "Merken" puts it on the wish list. The series stands
 * as a small shelf with the volumes the person has as spines and the ones they lack as ghosts.
 *
 * It has an address of its own (`/discover/book/:token`), opens over the page it came from, and closes with Escape, the
 * button or the way back of the browser, like the page of a book.
 */
import { ArrowRight, Download } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams, type Location } from 'react-router-dom'

import { ApiError, discoverApi, procureApi, type BookPage, type DiscoverCard, type FormOut, type LibraryKind, type ReleaseKind, type WorkBrief } from '../api/client'
import { AuthorAvatar } from '../components/AuthorAvatar'
import { AudioIcon, BookIcon, Cover } from '../components/BookCard'
import { MiniShelf } from '../components/Shelf'
import { VolumeShelf } from '../components/VolumeShelf'
import { ReleasesDialog } from '../components/procure/ReleasesDialog'
import { BackIcon, MarkIcon } from '../components/icons'
import { authorLine, durationParts, languageName } from '../lib/books'
import { authorHref } from '../lib/catalog'
import { coverOf, dayText, sourceNames } from '../lib/discover'
import { errorText } from '../lib/errors'
import { trapTab } from '../lib/focusTrap'
import { reducedMotion } from '../lib/motion'
import { bookLayerStands, leaveBook, type BookRoute } from '../lib/openBook'
import { useDetailTurn } from '../lib/useDetailTurn'
import { acquireBookOfCard } from '../lib/procure'
import { useProcure } from '../state/procure'
import { useWish } from '../state/wish'

/** The page fades out this long before it is gone. */
const FADE_OUT_MS = 300
/** While the server is looking for the other forms of the book, the page asks again this often, this many times. */
const POLL_MS = 5000
const POLL_TIMES = 6

function backLabel(background: Location | undefined): string {
  const path = background?.pathname ?? '/discover'
  if (path === '/') return 'work.back.library'
  if (path.startsWith('/discover')) return 'work.back.discover'
  if (path.startsWith('/stack')) return 'work.back.stack'
  return 'work.back.other'
}

/** A selector for the card a page was opened from; a token that is not made of letters, digits, `-` and `_` (typed into the
 * address by hand) selects nothing. */
function cardSelector(token: string): string | null {
  return /^[\w-]{1,24}$/.test(token) ? `.view [data-foreign="${token}"]` : null
}

function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((part) => part.replace(/\s*\n\s*/g, ' ').trim())
    .filter(Boolean)
}

export function ForeignPage() {
  const { token } = useParams()
  return <ForeignView key={token} token={token ?? ''} />
}

function ForeignView({ token }: { token: string }) {
  const { t, i18n } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const wish = useWish()
  const state = location.state as BookRoute
  const background = state?.background
  const [book, setBook] = useState<BookPage | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [shown, setShown] = useState(false)
  const root = useRef<HTMLElement>(null)
  const cover = useRef<HTMLDivElement>(null)
  const flip = useRef<HTMLDivElement>(null)
  const spineHost = useRef<HTMLDivElement>(null)
  const coverHost = useRef<HTMLDivElement>(null)
  const leaving = useRef(false)
  const tries = useRef(0)
  const [again, setAgain] = useState(0)

  const brief: DiscoverCard | null = book ?? state?.card ?? null
  // The title in the person's language, as the tile that led here says it; the one of the source goes under it.
  const language = i18n.language.split('-')[0]
  const inLanguage = book?.titles?.find((item) => item.language === language)?.text
  const title = inLanguage ?? brief?.title ?? ''

  useEffect(() => {
    let live = true
    discoverApi.book(token).then(
      (found) => {
        if (!live) return
        setBook(found)
        setFailure(null)
      },
      (error) => live && setFailure(error instanceof ApiError ? error.code : 'internal_error'),
    )
    return () => {
      live = false
    }
  }, [token, again])

  // The other forms of the book are looked for in the background; the page looks again a few times.
  const pending = book?.pending || book?.series_pending
  useEffect(() => {
    if (!pending || tries.current >= POLL_TIMES) return
    const timer = window.setTimeout(() => {
      tries.current += 1
      setAgain((n) => n + 1)
    }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [pending, book])

  useEffect(() => {
    document.body.classList.add('lock')
    const gone = bookLayerStands()
    return () => {
      document.body.classList.remove('lock')
      gone()
    }
  }, [])

  useEffect(() => {
    const layer = root.current
    if (!layer) return
    const key = (event: KeyboardEvent) => void trapTab(event, layer)
    layer.addEventListener('keydown', key)
    return () => layer.removeEventListener('keydown', key)
  }, [])

  useEffect(() => {
    root.current?.focus({ preventScroll: true })
    return () => {
      const selector = background ? cardSelector(token) : null
      if (selector) document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true })
    }
  }, [token, background])

  useDetailTurn(brief !== null, background ? cardSelector(token) : null, { cover, flip, spineHost, coverHost }, setShown)

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
  const goWork = (work: WorkBrief) => navigate(`/work/${work.id}`, { replace: true, state: { background, work, from: state?.from } satisfies BookRoute })
  const goBook = (card: DiscoverCard) =>
    navigate(card.owned_work_id !== null ? `/work/${card.owned_work_id}` : `/discover/book/${encodeURIComponent(card.token)}`, {
      replace: true,
      state: { background, ...(card.owned_work_id !== null ? {} : { card }), from: state?.from } satisfies BookRoute,
    })

  // A book that stands on a shelf has its page there: this one would say it knows the book only from the catalogue.
  const owned = book?.owned_work_id ?? null
  useEffect(() => {
    if (owned !== null) navigate(`/work/${owned}`, { replace: true, state: { background, from: state?.from } satisfies BookRoute })
  }, [owned, navigate, background, state?.from])

  const eyebrow = brief
    ? [brief.genre ? t(`library.genre.${brief.genre}`) : '', brief.series ? (brief.series_pos !== null ? t('work.eyebrowSeries', { series: brief.series, pos: brief.series_pos }) : brief.series) : '']
        .filter(Boolean)
        .join(' · ')
    : ''
  const style = { '--bc': brief?.color ?? 'var(--accent)' } as CSSProperties
  const wished = brief ? wish.wished(brief) : false
  const procure = useProcure()
  const asked = brief ? procure.kindsOf({ token: brief.token }) : []
  const merken = brief ? (
    <button type="button" className={wished || procure.allowed ? '' : 'primary'} aria-pressed={wished} data-wish={brief.token} onClick={() => void wish.toggle(brief)}>
      <MarkIcon />
      {wished ? t('discover.book.wished') : t('discover.book.wish')}
    </button>
  ) : null
  // The kinds of book the person may ask for: by the libraries they have the right in.
  const [mayKinds, setMayKinds] = useState<ReleaseKind[]>([])
  const [releases, setReleases] = useState<ReleaseKind | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  useEffect(() => {
    const allows = (library: LibraryKind, kind: ReleaseKind) => library === 'both' || library === kind
    procureApi.targets().then(
      (targets) => setMayKinds((['ebook', 'audio'] as const).filter((kind) => (Array.isArray(targets) ? targets : []).some((target) => allows(target.kind, kind)))),
      () => setMayKinds([]),
    )
  }, [])
  useEffect(() => {
    if (!said) return
    const timer = window.setTimeout(() => setSaid(null), 6000)
    return () => window.clearTimeout(timer)
  }, [said])
  const forms = book?.forms_known ?? []
  const ebook = forms.find((form) => form.kind === 'ebook')
  const audio = forms.find((form) => form.kind === 'audio' && form.variant === 'unabridged') ?? forms.find((form) => form.kind === 'audio')
  const others = forms.filter((form) => form !== ebook && form !== audio)
  const pages = forms.find((form) => form.pages)?.pages ?? 0
  const sourceList = brief ? sourceNames(brief.sources, t, i18n.language) : ''
  const volumes = book?.volumes ?? []
  const author = brief ? authorLine({ authors: brief.authors.map((name, index) => ({ id: index, name })) }) : ''

  return (
    <>
      <section ref={root} className={`detail ${shown ? 'show' : ''}`} style={style} role="dialog" aria-modal="true" aria-label={title || t('discover.book.loading')} tabIndex={-1} data-testid="foreign">
        <div className="d-glow" />
        <div className="d-wrap">
          <div className="d-bar">
            <button type="button" className="d-back" data-close onClick={close}>
              <BackIcon />
              {t(backLabel(background))}
            </button>
          </div>
          {failure && (
            <p className="empty" role="alert" data-testid="foreign-failure">
              {failure === 'not_found' ? t('discover.book.notFound') : errorText(failure)}
            </p>
          )}
          {!brief && !failure && <p className="empty">{t('discover.book.loading')}</p>}
          {brief && (
            <>
              <div className="d-hero">
                <div className="d-cover" ref={cover} onPointerMove={tilt} onPointerLeave={untilt}>
                  <Cover work={coverOf(brief)} />
                </div>
                <div>
                  {eyebrow && (
                    <p className="d-eyebrow" dir="auto">
                      {eyebrow}
                    </p>
                  )}
                  <h1 className="d-title" dir="auto">
                    {title}
                  </h1>
                  {title !== brief.title && (
                    <p className="d-original" dir="auto" data-testid="original-title">
                      {t('author.originalTitle', { title: brief.title })}
                    </p>
                  )}
                  {author && (
                    <p className="d-by">
                      {t('work.by')}{' '}
                      {brief.authors.map((name, index) => {
                        const about = (book?.about ?? []).find((item) => item.name.toLocaleLowerCase() === name.toLocaleLowerCase())
                        const href = about ? authorHref(about) : null
                        return (
                          <span key={`${name}-${index}`}>
                            {index > 0 && ', '}
                            {href ? (
                              <Link to={href} className="au-link" dir="auto" data-testid="author-link">
                                {name}
                              </Link>
                            ) : (
                              <span dir="auto">{name}</span>
                            )}
                          </span>
                        )
                      })}
                    </p>
                  )}
                  {book?.description && (
                    <div className="d-blurb">
                      {paragraphs(book.description).map((text, index) => (
                        <p key={index} dir="auto">
                          {text}
                        </p>
                      ))}
                    </div>
                  )}
                  {book?.owned_work_id != null ? (
                    <div className="want">
                      <Link className="primary" to={`/work/${book.owned_work_id}`} replace state={{ background, from: state?.from } satisfies BookRoute} data-testid="on-shelf">
                        {t('discover.book.openShelf')}
                      </Link>
                      <span className="notmine">{t('discover.book.onShelf')}</span>
                    </div>
                  ) : (
                    <>
                      <div className="want">
                        {!procure.allowed && merken}
                        {procure.allowed &&
                          (asked.length > 0 ? (
                            <Link className="bbtn state-link" to={asked.some((item) => item.download) ? '/acquire?reiter=running' : '/acquire'} data-testid="acquire-state">
                              <Download size={15} aria-hidden />
                              {asked.some((item) => item.download) ? t('acquire.entry.loading') : t('acquire.entry.wished')}
                            </Link>
                          ) : (
                            <button type="button" className="primary" onClick={() => procure.open([acquireBookOfCard(brief)])} data-testid="acquire-open">
                              <Download size={15} aria-hidden />
                              {t('acquire.entry.get')}
                            </button>
                          ))}
                        {mayKinds.length > 0 && (
                          <button type="button" onClick={() => setReleases(mayKinds[0])} data-testid="want-releases">
                            <SearchIcon />
                            {t('procure.releases.open')}
                          </button>
                        )}
                        {procure.allowed && merken}
                      </div>
                      <span className="notmine" data-testid="notmine">
                        {t('discover.book.notMine', { names: sourceList })}
                      </span>
                    </>
                  )}
                  {said && (
                    <p role="status" className="mt-3 text-sm text-muted" data-testid="foreign-status">
                      {said}
                    </p>
                  )}
                  {wish.problem && (
                    <p role="alert" className="mt-3 text-sm text-bad">
                      {wish.problem}
                    </p>
                  )}
                  <div className="d-facts">
                    {book?.year && (
                      <span>
                        <b>{book.year}</b>
                        {t('work.facts.year')}
                      </span>
                    )}
                    {pages > 0 && (
                      <span>
                        <b>{pages}</b>
                        {t('work.facts.pages')}
                      </span>
                    )}
                    {forms.length > 0 && (
                      <span>
                        <b>{forms.length}</b>
                        {t('work.facts.editions', { count: forms.length })}
                      </span>
                    )}
                    {audio?.minutes ? (
                      <span>
                        <b>{duration(t, audio.minutes * 60)}</b>
                        {t('work.facts.listen')}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
              {book && (
                <>
                  <h3 className="d-h">{t(forms.length === 1 ? 'work.editionsOne' : 'work.editionsMany')}</h3>
                  <div className="twins" data-testid="twins">
                    <Form form={ebook} kind="ebook" book={book} names={sourceList} onReleases={mayKinds.includes('ebook') && book.owned_work_id == null ? () => setReleases('ebook') : undefined} />
                    <div className="tw-link">
                      <span title={t('work.twin.link')}>⇄</span>
                    </div>
                    <Form form={audio} kind="audio" book={book} names={sourceList} onReleases={mayKinds.includes('audio') && book.owned_work_id == null ? () => setReleases('audio') : undefined} />
                  </div>
                  {others.length > 0 && (
                    <div className="more-fass" data-testid="more-editions">
                      {others.map((form, index) => (
                        <span key={`${form.kind}${form.variant}${form.language}${index}`}>
                          {form.kind === 'ebook' ? <BookIcon /> : <AudioIcon />}
                          {formTitle(form, t, i18n.language).main} · {formTitle(form, t, i18n.language).sub.split(' · ')[0]}
                        </span>
                      ))}
                    </div>
                  )}
                  {book.pending && (
                    <p className="mt-3 text-sm text-muted" data-testid="looking">
                      {t('discover.book.looking')}
                    </p>
                  )}
                  {volumes.length > 1 && brief.series && (
                    <>
                      <h3 className="d-h">{t('work.series', { name: brief.series })}</h3>
                      <VolumeShelf volumes={volumes} current={{ token }} label={brief.series} onOpenWork={goWork} onOpenBook={goBook} />
                    </>
                  )}
                  {(book.about ?? []).slice(0, 1).map((about) => (
                    <div key={about.name} data-testid="about-author">
                      <h3 className="d-h">{t('author.about', { name: about.name })}</h3>
                      <Link className="acard" to={authorHref(about) ?? '#'}>
                        <AuthorAvatar hit={about} size="m" />
                        <span className="ac-t">
                          <b dir="auto">{about.name}</b>
                          <small>
                            {[
                              about.works !== null ? t('author.aboutWorks', { count: about.works }) : '',
                              about.on_shelf ? t('author.aboutOnShelf', { count: about.on_shelf }) : t('author.aboutNone'),
                              about.soon ? t('author.aboutSoon', { count: about.soon }) : '',
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </small>
                        </span>
                        <span className="ac-go">
                          {t('author.allBooks')}
                          <ArrowRight aria-hidden />
                        </span>
                      </Link>
                    </div>
                  ))}
                  {book.more.length > 0 && brief.authors[0] && (
                    <>
                      <h3 className="d-h">{t('work.moreOf', { name: brief.authors[0] })}</h3>
                      <MiniShelf works={book.more} onOpen={goWork} label={author} />
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </section>
      <div className="flip" ref={flip} hidden>
        <div className="f-spine" ref={spineHost} style={{ display: 'none' }} />
        <div className="f-cover" ref={coverHost} style={{ display: 'none' }}>
          {brief && <Cover work={coverOf(brief)} />}
        </div>
      </div>
      {releases && brief && (
        <ReleasesDialog book={brief.token} title={title} author={brief.authors[0] ?? ''} kind={releases} onClose={() => setReleases(null)} onLoaded={setSaid} />
      )}
    </>
  )
}

function duration(t: (key: string, values?: Record<string, unknown>) => string, seconds: number): string {
  const { h, m } = durationParts(seconds)
  return h ? t('work.duration.hours', { h, m }) : t('work.duration.minutes', { m })
}

/** What a card says of a form the sources know: the first line ("E-Book · Deutsch", "Hörbuch · ungekürzt") and the second
 * (pages, or running time and narrator). */
function formTitle(form: FormOut, t: (key: string, values?: Record<string, unknown>) => string, language: string): { main: string; sub: string } {
  if (form.kind === 'ebook') {
    const name = languageName(form.language, language)
    return { main: name ? t('work.twin.ebook', { language: name }) : t('work.twin.noLanguage'), sub: form.pages ? t('work.twin.pages', { count: form.pages }) : '' }
  }
  const variant = t(`work.variant.${form.variant || 'unabridged'}`)
  const who = form.narrators.join(', ')
  const time = form.minutes ? duration(t, form.minutes * 60) : ''
  const sub = [time, who ? (form.variant === 'dramatized' ? who : t('work.twin.readBy', { name: who })) : ''].filter(Boolean).join(' · ')
  return { main: t('work.twin.audio', { variant }), sub }
}

/** One of the twins: the form the sources know, or the dashed card that says they know none. */
function Form({ form, kind, book, names, onReleases }: { form: FormOut | undefined; kind: 'ebook' | 'audio'; book: BookPage; names: string; onReleases?: () => void }) {
  const { t, i18n } = useTranslation()
  const Icon = kind === 'ebook' ? BookIcon : AudioIcon
  if (!form) {
    return (
      <div className="twin missing" data-testid="twin-missing" data-kind={kind}>
        <div className="tw-h">
          <span className="tw-ic">
            <Icon />
          </span>
          <div>
            <h4>{t(kind === 'ebook' ? 'discover.book.noEbook' : 'discover.book.noAudio')}</h4>
            <span className="tw-sub">{t('discover.book.neither', { names, count: book.sources.length })}</span>
          </div>
        </div>
        {onReleases && (
          <button type="button" className="go" onClick={onReleases} data-testid="releases">
            <SearchIcon />
            {t('procure.releases.open')}
          </button>
        )}
      </div>
    )
  }
  const text = formTitle(form, t, i18n.language)
  return (
    <div className="twin" data-testid="twin" data-kind={kind}>
      <div className="tw-h">
        <span className="tw-ic">
          <Icon />
        </span>
        <div>
          <h4>{text.main}</h4>
          <span className="tw-sub">{text.sub}</span>
        </div>
      </div>
      <div className="tw-pos">
        {book.upcoming ? <Trans i18nKey="discover.book.upcoming" values={{ date: dayText(book.released, i18n.language, true) }} components={{ b: <b /> }} /> : t('discover.book.notOwned')}
      </div>
      {onReleases && (
        <button type="button" className="go" onClick={onReleases} data-testid="releases">
          <SearchIcon />
          {t('procure.releases.open')}
        </button>
      )}
    </div>
  )
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  )
}
