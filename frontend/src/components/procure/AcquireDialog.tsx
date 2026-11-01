/**
 * "Beschaffen": asking for a book, an e-book, a recording or both, in a language
 * and for a library. The same dialog serves a book of the sources, a work of a library that lacks a kind, an upgrade, the
 * wish list ("Alle beschaffen") and the gaps of a series ("Fehlende beschaffen"): then it names how many books and shows
 * the first ones. Without an indexer it says so and, for the operator, leads to the settings.
 */
import { Calendar, Pause } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, procureApi, type BookOptions, type ProcureBook, type ReleaseKind, type WishResult } from '../../api/client'
import { errorText } from '../../lib/errors'
import { languageName } from '../../lib/books'
import { coverOfBook } from '../../lib/procure'
import { dayText } from '../../lib/discover'
import { useAuth } from '../../state/auth'
import { AudioIcon, BookIcon, Cover } from '../BookCard'
import { Dialog } from '../Dialog'
import { shortWhy } from './writeWhy'

/** A book to ask for: a work of a library (`workId`) or a book of the sources (`token`), with what the cover needs. */
export type AcquireBook = Pick<ProcureBook, 'title' | 'authors' | 'series' | 'series_pos' | 'color' | 'cover_url'> & { workId?: number; token?: string }

/** What the dialog tells the page when it is done: the sentence for the person, and where "Ansehen" leads. */
export type Done = { text: string }

function Icon({ kind }: { kind: ReleaseKind }) {
  return kind === 'ebook' ? <BookIcon /> : <AudioIcon />
}

function asBook(book: AcquireBook): ProcureBook {
  return { work_id: book.workId ?? null, book: book.token ?? null, title: book.title, authors: book.authors, series: book.series, series_pos: book.series_pos, genre: '', color: book.color, cover_url: book.cover_url }
}

type What = ReleaseKind | 'both'
/** The most books one request may name (the server's limit, `MAX_ITEMS` in `services/procure/wishes.py`). */
export const WISH_BATCH = 100

export function AcquireDialog({ books, kinds, onClose, onDone }: { books: AcquireBook[]; kinds?: ReleaseKind[]; onClose: () => void; onDone: (done: Done) => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const operator = me?.role === 'operator'
  const first = books[0]
  const many = books.length > 1
  const [options, setOptions] = useState<BookOptions | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  // A refusal of the request itself is told inside the dialog, which stays; a book that could not be read is the whole dialog.
  const [refusal, setRefusal] = useState<string | null>(null)
  const sending = useRef(false)
  const [what, setWhat] = useState<What | null>(null)
  const [language, setLanguage] = useState<string | null>(null)
  const [library, setLibrary] = useState('auto')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    procureApi.bookOptions(first.workId != null ? { work_id: first.workId } : { book: first.token }).then(
      (found) => live && setOptions(found),
      (error) => live && setFailure(error instanceof ApiError ? error.code : 'internal_error'),
    )
    return () => {
      live = false
    }
  }, [first.workId, first.token])

  // For many books every kind the first one allows counts as free: the server tells each book what it already has.
  const free = useMemo(() => (options ? options.kinds.filter((entry) => many || entry.state === 'free' || entry.state === 'upgrade') : []), [options, many])
  const preset = kinds && kinds.length > 0 ? free.filter((entry) => kinds.includes(entry.kind)) : free
  const chosen: What = what ?? (preset.length === 2 ? 'both' : preset[0]?.kind ?? free[0]?.kind ?? 'ebook')
  const selected = useMemo<ReleaseKind[]>(() => (chosen === 'both' ? ['ebook', 'audio'] : [chosen]), [chosen])
  const name = (code: string) => languageName(code, i18n.language)
  // Only the languages the operator allows (settings, "Sprachen"; the book's original one when "Originalsprache" is
  // ticked). All of them are chosen at first; one of them can be picked instead. The account's language is the
  // interface's and plays no part.
  const languages = useMemo(() => {
    if (!options) return []
    const label = (code: string) => {
      const named = name(code).replace(/^./, (letter) => letter.toLocaleUpperCase(i18n.language))
      // The original language is the first book's: for many books it says nothing about the others.
      return !many && code === options.original_language ? `${named} (${t('acquire.dialog.original')})` : named
    }
    const codes = [...new Set(options.languages.filter(Boolean))]
    if (codes.length === 0) return [{ code: '', label: t('acquire.dialog.anyLanguage') }]
    // With one language allowed the wish names it, so that it says on the wish what it looks for.
    if (codes.length === 1) return [{ code: codes[0], label: label(codes[0]) }]
    return [{ code: '', label: t('acquire.dialog.allowed', { list: codes.map((code) => name(code)).join(t('acquire.dialog.otherwise')) }) }, ...codes.map((code) => ({ code, label: label(code) }))]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, i18n.language])
  const picked = language !== null && languages.some((item) => item.code === language) ? language : (languages[0]?.code ?? '')
  // A library that takes every kind that is asked for.
  const libraries = useMemo(() => {
    const per = free.filter((entry) => selected.includes(entry.kind)).map((entry) => entry.libraries)
    if (per.length === 0) return []
    return per[0].filter((item) => per.every((list) => list.some((other) => other.id === item.id)))
  }, [free, selected])
  // The library nexbooks would use for a kind, and whether it may write there: a library mounted read-only takes no book.
  const entryOf = (kind: ReleaseKind) => options?.kinds.find((item) => item.kind === kind)
  const standing = (kind: ReleaseKind, choice: string) => {
    const entry = entryOf(kind)
    const id = choice === 'auto' ? entry?.default_library : Number(choice)
    return entry?.libraries.find((item) => item.id === id)
  }
  // The server's own choice is one nexbooks cannot write into: the first library that can take every kind asked for is
  // proposed instead (and chosen, so that the wish does not wait for nothing).
  const autoBad = selected.some((kind) => standing(kind, 'auto')?.writable === false)
  const proposal = autoBad ? libraries.find((item) => item.writable) : undefined
  const choice = library === 'auto' && proposal ? String(proposal.id) : library
  const place = (kind: ReleaseKind) => standing(kind, choice)?.name ?? ''
  const unwritable = selected.map((kind) => standing(kind, choice)).find((item) => item && !item.writable)
  // Several libraries take a kind asked for and the operator chose none: the person names one, nexbooks does not pick.
  const mustChoose = choice === 'auto' && selected.some((kind) => entryOf(kind)?.choose)

  if (failure || !options) {
    return (
      <Dialog title={t('acquire.dialog.title')} onClose={onClose} className="acq pd" sub={many ? undefined : first.title}>
        {failure ? <p role="alert" className="text-sm text-bad">{errorText(failure)}</p> : <p className="text-sm text-muted">{t('acquire.dialog.loading')}</p>}
      </Dialog>
    )
  }

  const head = many ? (
    <div className="pd-head many" data-testid="acquire-many">
      <div className="pd-t">
        <b>{t('acquire.dialog.manyTitle', { count: books.length })}</b>
        <span dir="auto">{books.slice(0, 3).map((item) => item.title).join(' · ')}{books.length > 3 ? ` · +${books.length - 3}` : ''}</span>
      </div>
    </div>
  ) : (
    <div className="pd-head">
      <div className="pd-cv">
        <Cover work={coverOfBook(asBook(first))} />
      </div>
      <div className="pd-t">
        <b dir="auto">{first.title}</b>
        <span dir="auto">{first.authors.join(', ')}</span>
        {first.series && <small dir="auto">{first.series}{first.series_pos != null ? `, ${t('acquire.dialog.volume', { n: first.series_pos })}` : ''}</small>}
      </div>
    </div>
  )

  if (options.indexers === 0) {
    return (
      <Dialog title={t('acquire.dialog.title')} onClose={onClose} className="acq pd">
        {head}
        <div className="note warn flat" data-testid="acquire-no-indexer">
          <Pause aria-hidden className="ic" />
          <p>
            <b>{t('acquire.dialog.noIndexer.title')}</b> {operator ? t('acquire.dialog.noIndexer.operator') : t('acquire.dialog.noIndexer.member')}
          </p>
        </div>
        <div className="dlg-f">
          <button type="button" className="bbtn" onClick={onClose}>
            {t('common.close')}
          </button>
          {operator && (
            <Link className="bbtn primary" to="/settings?tab=procure&sub=indexers" onClick={onClose}>
              {t('acquire.dialog.toSettings')}
            </Link>
          )}
        </div>
      </Dialog>
    )
  }

  if (free.length === 0) {
    const have = options.kinds.length > 0 && options.kinds.every((entry) => entry.state === 'have')
    return (
      <Dialog title={t('acquire.dialog.title')} onClose={onClose} className="acq pd">
        {head}
        <p className="dlg-p" data-testid="acquire-nothing">{have ? t('acquire.dialog.haveBoth') : t('acquire.dialog.allRunning')}</p>
        <div className="dlg-f">
          <button type="button" className="bbtn" onClick={onClose}>
            {t('common.close')}
          </button>
          <Link className="bbtn primary" to="/acquire" onClick={onClose}>
            {t('acquire.dialog.seeAcquire')}
          </Link>
        </div>
      </Dialog>
    )
  }

  const segments: { value: What; label: string }[] = [
    ...free.map((entry) => ({ value: entry.kind as What, label: t(`procure.releases.kind.${entry.kind}`) })),
    ...(free.length === 2 ? [{ value: 'both' as What, label: t('acquire.dialog.both') }] : []),
  ]
  const hints = options.kinds.filter((entry) => !free.includes(entry)).map((entry) => t(`acquire.dialog.hint.${entry.state}`, { kind: t(`procure.releases.kind.${entry.kind}`) }))
  const formatName = (code: string) => (i18n.exists(`procure.formats.name.${code}`) ? t(`procure.formats.name.${code}`) : code.toUpperCase())

  const send = async () => {
    // One press, one request: a second press before the first is answered does nothing.
    if (sending.current) return
    sending.current = true
    setBusy(true)
    setRefusal(null)
    try {
      // The server takes at most WISH_BATCH books per request: a longer list goes in parts, one after the other.
      const items = books.map((item) => (item.workId != null ? { work_id: item.workId } : { book: item.token }))
      const results: WishResult[] = []
      for (let start = 0; start < items.length; start += WISH_BATCH) {
        const answer = await procureApi.wish({
          items: items.slice(start, start + WISH_BATCH),
          kinds: selected,
          language: picked || undefined,
          library_id: choice === 'auto' ? undefined : Number(choice),
        })
        results.push(...answer.results)
      }
      onDone({ text: doneText(results, books, options, t) })
    } catch (error) {
      setRefusal(error instanceof ApiError ? error.code : 'internal_error')
      sending.current = false
      setBusy(false)
    }
  }

  return (
    <Dialog title={many ? t('acquire.dialog.manyHead', { count: books.length }) : t('acquire.dialog.title')} sub={t('acquire.dialog.sub')} onClose={onClose} className="acq pd">
      {head}
      <div className="fld">
        <span className="lab" id="acq-what">
          {t('acquire.dialog.what')}
        </span>
        <div className="seg" role="radiogroup" aria-labelledby="acq-what">
          {segments.map((item) => (
            <button key={item.value} type="button" role="radio" aria-checked={chosen === item.value} className={chosen === item.value ? 'on' : ''} onClick={() => setWhat(item.value)} data-what={item.value}>
              {item.value !== 'both' && <Icon kind={item.value} />}
              {item.label}
            </button>
          ))}
        </div>
        {hints.map((hint) => (
          <small className="hint" key={hint}>
            {hint}
          </small>
        ))}
      </div>
      <div className="fld">
        <label className="lab" htmlFor="acq-lang">
          {t('acquire.dialog.language')}
        </label>
        <select id="acq-lang" className="sel" value={picked} onChange={(event) => setLanguage(event.target.value)}>
          {languages.map((item) => (
            <option key={item.code || 'any'} value={item.code}>
              {item.label}
            </option>
          ))}
        </select>
        <small className="hint" data-testid="acquire-language-hint">
          {t('acquire.dialog.languageHint')}
        </small>
      </div>
      <div className="fld">
        <label className="lab" htmlFor="acq-lib">
          {t('acquire.dialog.library')}
        </label>
        <select id="acq-lib" className="sel" value={choice} onChange={(event) => setLibrary(event.target.value)}>
          <option value="auto" disabled={selected.some((kind) => entryOf(kind)?.choose)}>
            {selected.some((kind) => entryOf(kind)?.choose) ? t('acquire.dialog.pickLibrary') : t('acquire.dialog.byKind', { ebook: place('ebook') || '·', audio: place('audio') || '·' })}
          </option>
          {libraries.map((item) => (
            // A library nexbooks may not write into is shown, greyed out with the reason, but cannot be chosen.
            <option key={item.id} value={item.id} disabled={!item.writable}>
              {item.writable ? item.name : t('procure.formats.place.unwritableOption', { name: item.name, why: shortWhy(item.write_problem, t) })}
            </option>
          ))}
        </select>
        {libraries.some((item) => !item.writable) && (
          <small className="hint" data-testid="acquire-unwritable">
            {t('acquire.dialog.unwritableList', { list: libraries.filter((item) => !item.writable).map((item) => `${item.name} (${shortWhy(item.write_problem, t)})`).join(', ') })}
          </small>
        )}
        {library === 'auto' && proposal && autoBad && (
          <small className="hint" data-testid="acquire-proposal">
            {t('acquire.dialog.proposal', { library: place(selected[0]) || proposal.name })}
          </small>
        )}
        {mustChoose && (
          <small className="hint warn" role="alert" data-testid="acquire-choose">
            {t('acquire.dialog.chooseLibrary')}
          </small>
        )}
        {unwritable && (
          <small className="hint warn" role="alert" data-testid="acquire-readonly">
            {t(libraries.some((item) => item.writable) ? 'acquire.dialog.readOnlyOther' : 'acquire.dialog.readOnlyAll', {
              why: t(`procure.formats.place.problem.${['missing', 'read_only', 'no_permission'].includes(unwritable.write_problem) ? unwritable.write_problem : 'write_failed'}`, { name: unwritable.name }),
            })}
          </small>
        )}
      </div>
      <div className="pd-sum" data-testid="acquire-sum">
        <ul>
          {selected.map((kind) => {
            const entry = options.kinds.find((item) => item.kind === kind)
            const takes = (entry?.takes ?? []).map(formatName)
            return (
              <li key={kind}>
                <span>
                  <Icon kind={kind} />
                </span>
                <div>
                  <b>{t(`procure.releases.kind.${kind}`)}:</b> {t('acquire.dialog.takes', { formats: takes.slice(0, 3).join(t('acquire.dialog.then')), more: takes.length > 3 ? t('acquire.dialog.more') : '', goal: formatName(entry?.goal ?? '') })}
                  {kind === 'audio' && !entry?.abridged ? t('acquire.dialog.unabridged') : ''} {t('acquire.dialog.goesTo', { library: place(kind) })}
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      {!many && options.release_date && (
        <div className="note flat" data-testid="acquire-not-out">
          <Calendar aria-hidden className="ic" />
          <p>
            <b>{t('acquire.dialog.notOut', { date: dayText(options.release_date, i18n.language, true) })}</b> {t('acquire.dialog.notOutText')}
          </p>
        </div>
      )}
      {!options.automatic && (
        <div className="note flat" data-testid="acquire-manual">
          <Pause aria-hidden className="ic" />
          <p>
            <b>{t('acquire.dialog.manual.title')}</b> {t('acquire.dialog.manual.text')}
          </p>
        </div>
      )}
      {refusal && (
        <p role="alert" className="mt-3 text-sm text-bad" data-testid="acquire-failure">
          {errorText(refusal)}
        </p>
      )}
      <div className="dlg-f">
        <button type="button" className="bbtn" onClick={onClose}>
          {t('common.cancel')}
        </button>
        <button type="button" className="bbtn primary" onClick={() => void send()} disabled={busy || mustChoose} data-testid="acquire-go">
          {busy ? '…' : t('acquire.dialog.go')}
        </button>
      </div>
    </Dialog>
  )
}

/** The sentence after asking: how many wishes were made, and what happens next. */
function doneText(results: WishResult[], books: AcquireBook[], options: BookOptions, t: (key: string, values?: Record<string, unknown>) => string): string {
  const made = results.filter((item) => item.status === 'created')
  if (made.length === 0) {
    const known = results.filter((item) => item.status === 'exists').length
    if (known > 0) return t('acquire.done.exists', { title: books[0].title })
    if (results.some((item) => item.status === 'have')) return t('acquire.done.have', { title: books[0].title })
    return t('acquire.done.none')
  }
  if (books.length > 1 || made.length > 2) return t('acquire.done.many', { count: made.length })
  const waiting = made.some((item) => item.wish?.state === 'waiting')
  const next = waiting ? t('acquire.done.waiting') : options.automatic ? t('acquire.done.searching') : t('acquire.done.manual')
  return t(made.length > 1 ? 'acquire.done.two' : 'acquire.done.one', { title: books[0].title, next })
}
