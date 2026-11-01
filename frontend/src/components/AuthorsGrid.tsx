/**
 * The authors of the library: a grid of round photos with the name and "12 Werke · 4 Hörbücher", in the order of
 * the name, of the number of books or of the newest book, with the genres as chips and a field to find a name. An author
 * without a photo is a circle with their initials in the colour of the genre most of their books are. A press leads to the
 * page of the author. Only authors of books the person sees (the server counts nothing else).
 *
 * The authors come a page at a time; the photos are loaded when they come near (`loading="lazy"`). Looking the authors up on
 * the internet happens on the server in the background: a page that has none yet shows the circles, and the photos appear
 * the next time.
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, authorsApi, type AuthorCard, type AuthorPage } from '../api/client'
import { figure, inkFor } from '../lib/books'
import { errorText } from '../lib/errors'
import { circleColor, initials } from '../lib/listing'
import { GENRES, genreKey } from '../lib/shelf'
import { SearchIcon } from './icons'

export type AuthorSort = 'name' | 'count' | 'recent'
const PER_PAGE = 60
const TYPING_MS = 250

export function AuthorPhoto({ name, photoUrl, genre, size }: { name: string; photoUrl: string | null; genre: string; size?: number }) {
  const { t } = useTranslation()
  const color = circleColor(genreKey(genre))
  const style = { '--c': color, '--ink': inkFor(color), ...(size ? { '--size': `${size}px` } : {}) } as CSSProperties
  return photoUrl ? (
    <span className="a-photo" style={style}>
      <img src={photoUrl} alt={t('authors.photoOf', { name })} loading="lazy" decoding="async" draggable={false} />
    </span>
  ) : (
    <span className="a-photo initials" style={style} role="img" aria-label={t('authors.initialsOf', { name })} data-testid="author-initials">
      <span aria-hidden>{initials(name)}</span>
    </span>
  )
}

export function AuthorsGrid({ sort, onSort }: { sort: AuthorSort; onSort: (sort: AuthorSort) => void }) {
  const { t, i18n } = useTranslation()
  const [genre, setGenre] = useState<string | null>(null)
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<AuthorCard[]>([])
  const [page, setPage] = useState(1)
  const [info, setInfo] = useState<Pick<AuthorPage, 'total' | 'genres'> | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // The numbers of the chips are those of all authors; they stay while a genre is chosen.
  const genres = useRef<Record<string, number>>({})
  const everyone = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(typed.trim()), TYPING_MS)
    return () => window.clearTimeout(timer)
  }, [typed])
  useEffect(() => setPage(1), [sort, genre, query])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    authorsApi.list({ sort, per_page: PER_PAGE, page, ...(genre ? { genre } : {}), ...(query ? { q: query } : {}) }, controller.signal).then(
      (found) => {
        setProblem(null)
        const got = Array.isArray(found.items) ? found.items : []
        setItems((now) => (page === 1 ? got : [...now, ...got.filter((item) => !now.some((known) => known.id === item.id))]))
        if (!genre && !query) {
          genres.current = found.genres ?? {}
          everyone.current = Number(found.total) || 0
        }
        setInfo({ total: Number(found.total) || 0, genres: genres.current })
        setLoading(false)
      },
      (error) => {
        if (error instanceof ApiError && error.code === 'cancelled') return
        setProblem(error instanceof ApiError ? error.code : 'internal_error')
        setLoading(false)
      },
    )
    return () => controller.abort()
  }, [sort, genre, query, page])
  const language = i18n.language
  const counts = info?.genres ?? {}
  const total = info?.total ?? 0
  const sorts: AuthorSort[] = ['name', 'count', 'recent']
  return (
    <div data-testid="authors">
      <div className="authors-tools">
        <div className="seg small" role="group" aria-label={t('authors.sort.label')}>
          {sorts.map((key) => (
            <button key={key} type="button" className={sort === key ? 'on' : ''} aria-pressed={sort === key} onClick={() => onSort(key)} data-sort={key}>
              {t(`authors.sort.${key}`)}
            </button>
          ))}
        </div>
        <label className="author-search">
          <SearchIcon />
          <input type="search" value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={t('authors.search')} aria-label={t('authors.search')} maxLength={100} data-testid="author-search" />
        </label>
        <span className="author-count" data-testid="author-count">
          {t('authors.count', { count: total })}
        </span>
      </div>
      <div className="genres" role="group" aria-label={t('library.genres')}>
        <button type="button" className={`g ${genre === null ? 'on' : ''}`} aria-pressed={genre === null} onClick={() => setGenre(null)} data-g="">
          {t('library.allGenres')} <small>{everyone.current || ''}</small>
        </button>
        {GENRES.map((key) =>
          (counts[key] ?? 0) > 0 ? (
            <button key={key} type="button" className={`g ${genre === key ? 'on' : ''}`} aria-pressed={genre === key} onClick={() => setGenre(key)} data-g={key}>
              {t(`library.genre.${key}`)} <small>{counts[key]}</small>
            </button>
          ) : null,
        )}
      </div>
      {problem && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {errorText(problem)}
        </p>
      )}
      {!problem && !loading && items.length === 0 && (
        <p className="empty" data-testid="authors-empty">
          {query || genre ? t('authors.empty') : t('authors.noneYet')}
        </p>
      )}
      <ul className="authors-grid" data-testid="authors-grid">
        {items.map((author) => (
          <li key={author.id}>
            <Link to={`/person/${author.id}`} className="author" aria-label={t('authors.open', { name: author.name })} data-author={author.id}>
              <AuthorPhoto name={author.name} photoUrl={author.photo_url} genre={author.genre} />
              <b dir="auto">{author.name}</b>
              <span className="counts">
                {[t('authors.works', { count: author.works }), author.audio > 0 ? t('authors.audio', { count: author.audio }) : ''].filter(Boolean).join(' · ')}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {loading && items.length === 0 && <p className="empty">{t('authors.loading')}</p>}
      {items.length < total && !loading && (
        <p className="more-row">
          <button type="button" className="d-ghost" onClick={() => setPage((now) => now + 1)} data-testid="authors-more">
            {t('library.more', { shown: figure(items.length, language), total: figure(total, language) })}
          </button>
        </p>
      )}
    </div>
  )
}
