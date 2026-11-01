/**
 * The search field of the head: the key `/` goes to it, what is typed finds the books of
 * the person's libraries by the start of words (after a breath, not on every letter), and under it the catalogues of the
 * sources, when the operator switched any on. Arrows, Enter and Escape work in the list. A hit in the library leads to the
 * shelf (`LibraryPage` scrolls to the spine, which glows, then the book opens); a hit of a catalogue opens the page of the
 * book (`ForeignPage`, with its source named), or, when the book is on the person's shelf, leads there like a hit of the
 * library.
 *
 * The catalogues give one hit for each book (its e-books, recordings and translations in one), named the way it is called in
 * the languages the operator fetches in, the book asked for first (`lib/catalogSearch`). The first few stand here, each with
 * "E-Book", "Hörbuch" and "Beides" to acquire it; all of them, with their editions, are on the page of the search in
 * Discover, which shows the same hits in the same order.
 *
 * Above both, the authors (before the person's own books): the authors of the person's libraries
 * whose name or pen name holds the words, and the authors the catalogues know by them, each with a photo or the initials,
 * the number of works and how many of them stand on the person's shelf. A press opens the page of the author.
 *
 * The list is a grid the field controls (`aria-activedescendant`): one row for each hit, the arrows up and down go from row
 * to row, and in a hit of a catalogue the arrows left and right reach "E-Book", "Hörbuch" and "Beides", which Enter presses.
 */
import { ArrowRight } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { catalogApi, worksApi, type AuthorHit, type CatalogHit, type CatalogOut, type WorkBrief } from '../api/client'
import { authorLine } from '../lib/books'
import { authorHref, peopleOf } from '../lib/catalog'
import { askCatalog, KINDS, offersKinds, QUICK_HITS, searchHref, tokenOfHit, tooOften, useAcquireHit, useHitLine, useRestingLine } from '../lib/catalogSearch'
import { useOpenAt } from '../lib/openBook'
import { genreKey } from '../lib/shelf'
import { useProcure } from '../state/procure'
import { AuthorAvatar } from './AuthorAvatar'
import { Cover, EditionChipList } from './BookCard'
import { AcquireKinds, HitChips, HitCover } from './CatalogHitParts'
import { SearchIcon } from './icons'

/** How long typing rests before the library is asked, and before the catalogues are (they cost the sources a request). */
const LIBRARY_DELAY_MS = 120
const CATALOG_DELAY_MS = 700
/** A catalogue is asked about words, not about a letter or two. */
const CATALOG_MIN = 3

/** The moment a hit is chosen (the shelf opens the book of a press, not of a press before). */
function stamp(): number {
  return Date.now()
}

function typing(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return !!element && (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT' || element.isContentEditable)
}

export function SearchBox() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const openAt = useOpenAt()
  const hitLine = useHitLine()
  const procure = useProcure()
  const acquire = useAcquireHit()
  const restingLine = useRestingLine()
  const input = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const listId = useId()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [hits, setHits] = useState<WorkBrief[] | null>(null)
  const [ownPeople, setOwnPeople] = useState<AuthorHit[]>([])
  const [catalog, setCatalog] = useState<CatalogOut | null>(null)
  // Why the catalogues gave nothing: the person searched too much at once (`busy`), or they did not answer.
  const [catalogProblem, setCatalogProblem] = useState<'' | 'busy' | 'failed'>('')
  const [active, setActive] = useState(0)
  // The cell of the active row: 0 the hit itself, 1 to 3 "E-Book", "Hörbuch", "Beides" (only after the arrows went into the
  // list: until then the arrows left and right move in the text).
  const [col, setCol] = useState(0)
  const [inList, setInList] = useState(false)
  // The latest question: an answer to an older one is dropped.
  const asked = useRef(0)
  // Whether the operator has any catalogue on, as the first answer of one said (no question goes out before).
  const catalogOn = useRef(true)
  // The question to the catalogues that is still asked, and whether one was given up because the list closed: the list
  // that opens again for the same words asks anew (`round`).
  const catalogAsk = useRef<AbortController | null>(null)
  const gaveUp = useRef(false)
  const [round, setRound] = useState(0)

  // The library, after a breath.
  useEffect(() => {
    const words = q.trim()
    setCol(0)
    setInList(false)
    if (!words) {
      setHits(null)
      setCatalog(null)
      setCatalogProblem('')
      setOwnPeople([])
      return
    }
    const mine = ++asked.current
    const controller = new AbortController()
    const remoteAsk = new AbortController()
    controller.signal.addEventListener('abort', () => remoteAsk.abort(), { once: true })
    gaveUp.current = false
    const library = window.setTimeout(() => {
      worksApi.search(words, 8, controller.signal).then(
        (answer) => {
          if (mine !== asked.current) return
          setHits(answer.items)
          setActive(0)
        },
        () => mine === asked.current && setHits([]),
      )
      if (words.length >= 2) {
        catalogApi.people(words, controller.signal).then(
          (answer) => mine === asked.current && setOwnPeople(Array.isArray(answer.items) ? answer.items : []),
          () => mine === asked.current && setOwnPeople([]),
        )
      } else setOwnPeople([])
    }, LIBRARY_DELAY_MS)
    // The catalogues, after a longer one.
    let remote = 0
    if (catalogOn.current && words.length >= CATALOG_MIN) {
      remote = window.setTimeout(() => {
        catalogAsk.current = remoteAsk
        askCatalog(words, remoteAsk.signal, (answer) => {
          if (mine !== asked.current) return
          catalogOn.current = answer.available
          setCatalogProblem('')
          setCatalog(answer.available ? answer : null)
        })
          .catch((error: unknown) => {
            if (mine !== asked.current || remoteAsk.signal.aborted) return
            setCatalogProblem(tooOften(error) ? 'busy' : 'failed')
          })
          .finally(() => {
            if (catalogAsk.current === remoteAsk) catalogAsk.current = null
          })
      }, CATALOG_DELAY_MS)
    }
    return () => {
      controller.abort()
      window.clearTimeout(library)
      if (remote) window.clearTimeout(remote)
    }
  }, [q, round])

  // The list closes (Escape, a press outside it, a book chosen): a question to the catalogues still asked is given up, so
  // that the sources are not asked for what nobody looks at; it is asked anew when the list opens again.
  const close = () => {
    setOpen(false)
    const ask = catalogAsk.current
    if (ask && !ask.signal.aborted) {
      ask.abort()
      catalogAsk.current = null
      gaveUp.current = true
    }
  }
  const reopen = () => {
    setOpen(true)
    if (gaveUp.current) {
      gaveUp.current = false
      setRound((now) => now + 1)
    }
  }

  const closeRef = useRef(close)
  closeRef.current = close
  // "/" goes to the field from anywhere that is not a field itself; a press outside the field closes the list.
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !typing(event.target)) {
        event.preventDefault()
        input.current?.focus()
      }
    }
    const away = (event: PointerEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) closeRef.current()
    }
    window.addEventListener('keydown', key)
    document.addEventListener('pointerdown', away)
    return () => {
      window.removeEventListener('keydown', key)
      document.removeEventListener('pointerdown', away)
    }
  }, [])

  const chooseAuthor = (hit: AuthorHit) => {
    close()
    input.current?.blur()
    const href = authorHref(hit)
    if (href) {
      navigate(href)
      return
    }
    // An author the search only named: the catalogue is made now that the person opens it.
    catalogApi.open(hit.name).then(
      (found) => {
        const target = authorHref(found)
        if (target) navigate(target)
      },
      () => undefined,
    )
  }
  const choose = (work: WorkBrief) => {
    close()
    input.current?.blur()
    navigate('/', { state: { goto: work.id, at: stamp() } })
  }
  // A hit of a catalogue: the book of the shelf it is, or the page of a book that is not on it (made now when it has none).
  const chooseHit = (hit: CatalogHit) => {
    close()
    input.current?.blur()
    if (hit.owned_work_id !== null) {
      navigate('/', { state: { goto: hit.owned_work_id, at: stamp() } })
      return
    }
    tokenOfHit(hit, catalog?.ticket).then(
      (token) => openAt(`/discover/book/${encodeURIComponent(token)}`, {}),
      () => setCatalogProblem('failed'),
    )
  }

  const shown = open && q.trim() !== ''
  const people = peopleOf(ownPeople, Array.isArray(catalog?.people) ? catalog.people : [])
  const lead = people.length
  const found = hits ?? []
  const remote = (catalog?.items ?? []).slice(0, QUICK_HITS)
  const more = (catalog?.items.length ?? 0) > remote.length
  // The arrows and Enter go through the authors first, then the library hits, then the hits of the catalogues.
  const total = lead + found.length + remote.length
  const resting = restingLine(catalog)
  const nothing = hits !== null && found.length === 0 && people.length === 0 && !(catalog && catalog.items.length > 0) && !catalog?.pending && !resting
  const activeHit = active >= lead + found.length ? remote[active - lead - found.length] : undefined
  const cells = (hit: CatalogHit | undefined) => (hit && offersKinds(hit, procure.allowed, procure.kindsOf({ token: hit.token }).length > 0) ? KINDS.length : 0)
  const cellId = (index: number, cell: number) => (cell ? `${listId}-c${index}-k${cell}` : `${listId}-c${index}`)
  const activeId = !shown
    ? undefined
    : active < lead
      ? `${listId}-p${active}`
      : active - lead < found.length && found[active - lead]
        ? `${listId}-${found[active - lead]!.id}`
        : activeHit
          ? cellId(active - lead - found.length, Math.min(col, cells(activeHit)))
          : undefined
  const move = (to: number | ((now: number) => number)) => {
    setActive(to)
    setCol(0)
  }
  return (
    <div className="search" ref={box}>
      <SearchIcon />
      <input
        ref={input}
        id="q"
        type="search"
        role="combobox"
        aria-haspopup="grid"
        aria-expanded={shown}
        aria-controls={listId}
        aria-activedescendant={activeId}
        aria-label={t('search.label')}
        placeholder={t('search.placeholder')}
        autoComplete="off"
        value={q}
        onChange={(event) => {
          setQ(event.target.value)
          setOpen(event.target.value.trim() !== '')
        }}
        onFocus={() => q.trim() && reopen()}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && open) {
            event.preventDefault()
            event.stopPropagation()
            close()
          } else if (event.key === 'ArrowDown' && total) {
            event.preventDefault()
            if (!open) reopen()
            setInList(true)
            move((now) => Math.min(total - 1, now + 1))
          } else if (event.key === 'ArrowUp' && total) {
            event.preventDefault()
            setInList(true)
            move((now) => Math.max(0, now - 1))
          } else if ((event.key === 'ArrowRight' || event.key === 'ArrowLeft') && shown && inList && cells(activeHit)) {
            event.preventDefault()
            setCol((now) => Math.max(0, Math.min(cells(activeHit), now + (event.key === 'ArrowRight' ? 1 : -1))))
          } else if (event.key === 'Enter') {
            if (shown && active < lead && people[active]) {
              event.preventDefault()
              chooseAuthor(people[active]!)
            } else if (shown && active - lead < found.length && found[active - lead]) {
              event.preventDefault()
              choose(found[active - lead]!)
            } else if (shown && activeHit) {
              event.preventDefault()
              const cell = Math.min(col, cells(activeHit))
              if (cell) {
                close()
                acquire(activeHit, catalog?.ticket, KINDS[cell - 1]!.kinds).catch(() => setCatalogProblem('failed'))
              } else chooseHit(activeHit)
            }
          }
        }}
      />
      <kbd>/</kbd>
      {/* (Said to a screen reader once the catalogues have answered: how many hits there are.) */}
      <p className="sr-only" aria-live="polite" data-testid="catalog-count">
        {shown && catalog && !catalog.pending ? t('search.catalogCount', { count: catalog.items.length }) : ''}
      </p>
      {shown && (
        <div className="results" data-testid="results">
          {hits === null && <p className="none">{t('search.searching')}</p>}
          {/* (A grid without rows is none: it stands only when there is something in it.) */}
          <div id={listId} role={total ? 'grid' : undefined} aria-label={total ? t('search.label') : undefined}>
            {people.length > 0 && (
              <div role="rowgroup" aria-label={t('search.people')}>
                <p className="res-head" aria-hidden="true">
                  {t('search.people')}
                </p>
                {people.map((hit, index) => (
                  <div
                    key={`${hit.person_id ?? ''}-${hit.token ?? ''}-${hit.name}`}
                    role="row"
                    aria-selected={index === active}
                    className={`res res-au ${index === active ? 'act' : ''}`}
                    data-testid="author-hit"
                    onMouseEnter={() => move(index)}
                    onClick={() => chooseAuthor(hit)}
                  >
                    <AuthorAvatar hit={hit} />
                    <span role="gridcell" id={`${listId}-p${index}`}>
                      <b dir="auto">{hit.name}</b>
                      <small>
                        {[
                          hit.genre ? t(`library.genre.${genreKey(hit.genre)}`) : '',
                          hit.works !== null ? t('search.works', { count: hit.works }) : '',
                          hit.on_shelf ? (hit.works !== null ? t('search.onShelf', { count: hit.on_shelf }) : t('search.mineOnShelf', { count: hit.on_shelf })) : hit.works !== null ? t('search.noneOnShelf') : '',
                          hit.alias ? t('search.alsoAs', { name: hit.alias }) : '',
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </small>
                    </span>
                    <span className="res-go">
                      {t('search.allBooks')}
                      <ArrowRight aria-hidden />
                    </span>
                  </div>
                ))}
              </div>
            )}
            {found.length > 0 && (
              <div role="rowgroup" aria-label={t('search.library')}>
                <p className="res-head" aria-hidden="true">
                  {t('search.library')}
                </p>
                {found.map((work, index) => (
                  <div
                    key={work.id}
                    role="row"
                    aria-selected={lead + index === active}
                    className={`res ${lead + index === active ? 'act' : ''}`}
                    data-id={work.id}
                    onMouseEnter={() => move(lead + index)}
                    onClick={() => choose(work)}
                  >
                    <Cover work={work} mini />
                    <span role="gridcell" id={`${listId}-${work.id}`}>
                      <b dir="auto">{work.title}</b>
                      <small>
                        <span dir="auto">{authorLine(work)}</span> · {t(`library.genre.${genreKey(work.genre)}`)}
                      </small>
                    </span>
                    <span className="chips">
                      <EditionChipList work={work} />
                    </span>
                  </div>
                ))}
              </div>
            )}
            {catalog && remote.length > 0 && (
              <div role="rowgroup" aria-label={t('search.catalog')}>
                <p className="res-head" aria-hidden="true">
                  {t('search.catalog')} · {[...new Set(remote.flatMap((item) => item.sources))].map((source) => t(`sources.names.${source}`)).join(', ')}
                </p>
                {remote.map((item, index) => {
                  const here = lead + found.length + index === active
                  return (
                    <div
                      key={item.key}
                      role="row"
                      aria-selected={here}
                      className={`res is-catalog ${here ? 'act' : ''}`}
                      data-testid="catalog-hit"
                      data-group={item.group}
                      onMouseEnter={() => !here && move(lead + found.length + index)}
                      onClick={() => chooseHit(item)}
                    >
                      <HitCover hit={item} />
                      <span role="gridcell" id={cellId(index, 0)} aria-selected={here && col === 0}>
                        <b dir="auto">{item.title}</b>
                        <small dir="auto">{hitLine(item)}</small>
                      </span>
                      <span className="chips">
                        <HitChips hit={item} />
                        {item.owned_work_id === null && (
                          <AcquireKinds hit={item} ticket={catalog.ticket} cells={{ cellId: (cell) => cellId(index, cell), active: here ? col : 0 }} onOpen={close} />
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          {catalog && remote.length > 0 && (more || catalog.pending) && (
            <button
              type="button"
              tabIndex={-1}
              className="res-more"
              data-testid="catalog-all"
              onClick={() => {
                close()
                input.current?.blur()
                navigate(searchHref(q))
              }}
            >
              {catalog.pending ? t('search.hit.moreComing') : t('search.hit.all', { count: catalog.items.length })}
              <ArrowRight aria-hidden />
            </button>
          )}
          {catalogProblem === 'busy' && (
            <p className="none" role="status" data-testid="catalog-busy">
              {t('search.catalogBusy')}
            </p>
          )}
          {resting && !catalogProblem && (
            <p className="none" data-testid="catalog-resting">
              {resting}
            </p>
          )}
          {(catalog?.failed || catalogProblem === 'failed') && !resting && <p className="none">{t('search.catalogFailed')}</p>}
          {nothing && !catalog?.failed && !catalogProblem && <p className="none">{t('search.none')}</p>}
        </div>
      )}
    </div>
  )
}
