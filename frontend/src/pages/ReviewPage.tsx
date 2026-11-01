/**
 * The list of books the matching is not sure about, for the people who look after a library (a member with
 * "manage"). Each book is a card: the book as the files describe it on one side, the proposals of the sources on the
 * other (cover, title, author, year, language, source and the reasons for and against), with three ways on: take a
 * proposal, look for others, leave the book as it is. What a person decides here stays; no later run changes it.
 */
import { BookOpen, FileText, Headphones, Search, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { Trans, useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, editionsApi, librariesApi, reviewApi, type CompareRow, type FileFacts, type Library, type Proposal, type ReviewItem } from '../api/client'
import { AudioIcon, BookIcon } from '../components/BookCard'
import { Dialog } from '../components/Dialog'
import { PageHead } from '../components/PageHead'
import { useConfirm } from '../components/useConfirm'
import { Compare, FilePanel } from '../components/review/FileFacts'
import { PeekBook } from '../components/review/PeekBook'
import { PeekListen } from '../components/review/PeekListen'
import { listOf } from '../lib/say'
import { Button, Feedback, Input, useAction } from './settings/ui'

const PAGE = 50
/** The codes of the reasons that speak for a proposal, and those that speak against it; the rest are cautions. */
const FOR = new Set(['isbn_match', 'asin_match', 'title_exact', 'author_match', 'language_ok', 'volume_ok', 'year_ok', 'year_name_ok', 'runtime_ok', 'second_source'])
const AGAINST = new Set(['title_differs', 'author_differs', 'volume_differs', 'language_differs', 'companion', 'collection', 'isbn_conflict', 'year_off', 'runtime_differs', 'subtitle_differs', 'series_only'])

/** The words of what a proposal lacks (`review.gap.lacks.*`), as one list: "a, b or c" when each is enough, "a, b and c" when all are needed. */
function lackList(words: string[], join: 'or' | 'and', t: TFunction): string {
  const items = words.map((word) => t(`review.gap.lacks.${word}`))
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} ${t(join === 'and' ? 'review.gap.and' : 'review.gap.or')} ${items[items.length - 1]}`
}

/**
 * "Sure from 80 points, here 75. It lacks: an ISBN or a second source." under the points of a proposal. A proposal that is held back
 * whatever its points (a weak or missing author, an unknown language) says that instead, and names what would lift it.
 */
function gapSentence(points: number, sureAt: number, lacks: string[], join: 'or' | 'and', cap: string, t: TFunction): string {
  const head = points < sureAt ? t('review.gap.sentence', { sure: sureAt, points }) : ''
  const held = cap ? t(`review.gap.cap.${cap}`, { defaultValue: '' }) : ''
  const missing = lacks.length > 0 ? t('review.gap.missing', { list: lackList(lacks, join, t) }) : ''
  return [head, held, missing].filter(Boolean).join(' ')
}

function Picture({ src, audio }: { src: string | null; audio: boolean }) {
  return (
    <div className={`mini-cover ${audio ? 'is-audio' : ''}`} aria-hidden>
      {src ? <img src={src} alt="" loading="lazy" decoding="async" draggable={false} /> : <span>{audio ? <AudioIcon /> : <BookIcon />}</span>}
    </div>
  )
}

function line(parts: (string | number | null | undefined | false)[]): string {
  return parts.filter((part) => part !== null && part !== undefined && part !== false && part !== '').join(' · ')
}

function ProposalRow({ proposal, kind, sureAt, busy, onTake, compare }: { proposal: Proposal; kind: ReviewItem['kind']; sureAt: number; busy: boolean; onTake: () => void; compare?: CompareRow[] }) {
  const { t } = useTranslation()
  const details = line([
    proposal.year,
    proposal.languages.slice(0, 3).map((code) => code.toUpperCase()).join(' '),
    proposal.series && (proposal.series_pos !== null ? `${proposal.series} ${proposal.series_pos}` : proposal.series),
    proposal.runtime_min ? t('review.minutes', { count: proposal.runtime_min }) : '',
    proposal.variant && proposal.variant !== 'unabridged' ? t(`library.variant.${proposal.variant}`) : '',
    proposal.narrators.length > 0 && proposal.narrators.slice(0, 2).join(', '),
  ])
  const where = line([t(`sources.names.${proposal.source}`), proposal.market ? proposal.market.toUpperCase() : ''])
  return (
    <li className={`proposal ${proposal.fatal ? 'is-fatal' : ''}`} data-testid="proposal">
      <Picture src={proposal.cover_url} audio={kind === 'audio'} />
      <div className="min-w-0 flex-1">
        <p className="font-display text-[1.02rem] font-semibold leading-snug [overflow-wrap:anywhere]">
          {proposal.title}
          {proposal.subtitle && <span className="font-normal text-ink-2">: {proposal.subtitle}</span>}
        </p>
        {proposal.authors.length > 0 && <p className="text-sm text-ink-2">{proposal.authors.join(', ')}</p>}
        {details && <p className="text-xs text-muted">{details}</p>}
        <p className="text-xs text-muted">
          {where}
          {proposal.found && ` · ${t('review.found')}`}
        </p>
        <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label={t('review.why.label')}>
          {proposal.why.map((why) => (
            <li key={why.code} className={`why ${FOR.has(why.code) ? 'is-for' : AGAINST.has(why.code) ? 'is-against' : 'is-careful'}`} data-code={why.code}>
              {t(`review.why.${why.code}`, { defaultValue: why.text })}
            </li>
          ))}
          {/* What speaks against it though it could still be the book (red), and what is missing to be sure of it (grey, dashed). */}
          {proposal.against
            .filter((against) => !proposal.why.some((why) => why.code === against.code))
            .map((against) => (
              <li key={`against-${against.code}`} className="why is-against" data-code={against.code}>
                {t(`review.why.${against.code}`, { defaultValue: against.text, ...(proposal.detail[against.code] ?? {}) })}
              </li>
            ))}
          {proposal.missing.map((missing) => (
            <li key={`missing-${missing.code}`} className="why is-missing" data-code={missing.code}>
              {t(`review.why.${missing.code}`, { defaultValue: missing.text })}
            </li>
          ))}
        </ul>
        {compare && <Compare rows={compare} />}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <Button primary={!proposal.fatal} small onClick={onTake} busy={busy}>
          {proposal.fatal ? t('review.takeAnyway') : t('review.take')}
        </Button>
        <span className="text-[0.7rem] text-muted">{t('review.points', { count: proposal.score })}</span>
        {!proposal.fatal && (proposal.score < sureAt || proposal.cap) && (
          <span className="max-w-[16rem] text-right text-[0.7rem] leading-snug text-muted" data-testid="proposal-gap">
            {gapSentence(proposal.score, sureAt, proposal.lacks, proposal.lacks_join, proposal.cap, t)}
          </span>
        )}
      </div>
    </li>
  )
}

function SearchForm({ item, busy, onSearch, onClose }: { item: ReviewItem; busy: boolean; onSearch: (title: string, author: string) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [title, setTitle] = useState(item.title)
  const [author, setAuthor] = useState(item.authors[0] ?? '')
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        onSearch(title.trim(), author.trim())
      }}
      className="grid gap-3 rounded-xl border border-line bg-sheet-2/50 p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
    >
      <Input label={t('review.searchTitle')} value={title} onChange={setTitle} maxLength={200} />
      <Input label={t('review.searchAuthor')} value={author} onChange={setAuthor} maxLength={200} />
      <div className="flex gap-2">
        <Button primary type="submit" busy={busy}>
          <Search size={15} aria-hidden /> {t('review.search')}
        </Button>
        <Button onClick={onClose}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}

function Entry({ item, library, onGone, onChanged }: { item: ReviewItem; library?: string; onGone: (id: number) => void; onChanged: (item: ReviewItem) => void }) {
  const { t } = useTranslation()
  const { busy, problem, values, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [searching, setSearching] = useState(false)
  // Looking into the book: the first pages (e-book), a short listen (recording), and what the file says about itself.
  const [reading, setReading] = useState(false)
  const [listening, setListening] = useState(false)
  const [factsOpen, setFactsOpen] = useState(false)
  const [facts, setFacts] = useState<FileFacts | null>(null)
  const [factsProblem, setFactsProblem] = useState<string | null>(null)
  const proposalIds = item.proposals.map((proposal) => proposal.id).join('|')
  useEffect(() => {
    // Open: read when asked, and again when a search added proposals (the comparison is made for each of them).
    if (!factsOpen) return
    let gone = false
    reviewApi.file(item.id).then(
      (found) => {
        if (gone) return
        setFacts(found)
        setFactsProblem(null)
      },
      (error) => !gone && setFactsProblem(error instanceof ApiError ? error.code : 'internal_error'),
    )
    return () => {
      gone = true
    }
  }, [factsOpen, item.id, proposalIds])
  const details = line([
    item.language && item.language.toUpperCase(),
    item.year,
    item.series && (item.series_pos !== null ? `${item.series} ${item.series_pos}` : item.series),
    item.runtime_min ? t('review.minutes', { count: item.runtime_min }) : '',
    item.kind === 'audio' && t('review.audio'),
  ])
  // "What fits is not close enough" says nothing a person can act on: the points, what they need and what is missing say it,
  // under the points of the proposal (not said twice, here as well). "More than one book fits" says which two and what tells
  // them apart.
  const tie = item.gap?.tie
  const reasonText = tie
    ? t(`review.gap.tie.${tie.difference}`, { a: tie.a ?? '', b: tie.b ?? '' })
    : item.reason === 'low_score' && item.gap
      ? ''
      : item.reason
        ? t(`review.reason.${item.reason}`, { defaultValue: item.reason_text })
        : ''
  const take = (proposal: Proposal) => void run(async () => {
    await reviewApi.accept(item.id, proposal.id)
    onGone(item.id)
  })
  const keep = () => void run(async () => {
    await reviewApi.keep(item.id)
    onGone(item.id)
  })
  const throwAway = () =>
    ask({
      title: t('work.confirm.trash.title'),
      text: t('work.confirm.trash.text', { count: item.trash_days }),
      confirm: t('work.confirm.trash.confirm'),
      run: async () => {
        await editionsApi.trash(item.edition_id)
        onGone(item.id)
      },
    })
  const search = (title: string, author: string) => void run(async () => {
    onChanged(await reviewApi.search(item.id, title, author))
    setSearching(false)
  })
  return (
    <article className="card review-item" data-testid="review-item" aria-label={item.title}>
      <div className="review-mine">
        <Picture src={item.cover_url} audio={item.kind === 'audio'} />
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">
            {t('review.yours')}
            {library && (
              <span className="ml-2 rounded-full border border-line px-2 py-0.5 normal-case tracking-normal" data-testid="review-library">
                {library}
              </span>
            )}
          </p>
          <h2 className="font-display text-lg font-semibold leading-snug [overflow-wrap:anywhere]">{item.title}</h2>
          {item.authors.length > 0 && <p className="text-sm text-ink-2">{item.authors.join(', ')}</p>}
          {details && <p className="text-xs text-muted">{details}</p>}
          <p className="mt-1 break-all text-xs text-dim" data-testid="review-file">
            {item.file}
          </p>
          <div className="review-look" data-testid="review-look">
            {item.kind === 'ebook' ? (
              <button type="button" className="peek-btn" onClick={() => setReading(true)} data-testid="review-read">
                <BookOpen size={15} aria-hidden /> {t('review.peek.read')}
              </button>
            ) : (
              <button type="button" className="peek-btn" aria-expanded={listening} onClick={() => setListening((now) => !now)} data-testid="review-listen">
                <Headphones size={15} aria-hidden /> {t('review.peek.listen')}
              </button>
            )}
            <button type="button" className="peek-btn" aria-expanded={factsOpen} onClick={() => setFactsOpen((now) => !now)} data-testid="review-facts">
              <FileText size={15} aria-hidden /> {t('review.facts.open')}
            </button>
          </div>
          {factsOpen && factsProblem && <Feedback problem={factsProblem} />}
          <p className="mt-2 text-sm" data-testid="review-reason">
            <span className={`font-semibold ${item.state === 'maybe' ? 'text-warn' : 'text-muted'}`}>{t(`review.state.${item.state}`)}</span>
            {reasonText && <span className="text-ink-2"> · {reasonText}</span>}
          </p>
        </div>
      </div>
      <div className="review-offers">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t('review.proposals')}</p>
        {item.proposals.length > 0 ? (
          <ul className="space-y-2.5">
            {item.proposals.map((proposal) => (
              <ProposalRow
                key={proposal.id}
                proposal={proposal}
                kind={item.kind}
                sureAt={item.gap?.sure_at ?? 80}
                busy={busy}
                onTake={() => take(proposal)}
                compare={factsOpen ? facts?.proposals.find((found) => found.proposal_id === proposal.id)?.rows : undefined}
              />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted" data-testid="review-no-proposals">
            {t('review.noProposals')}
          </p>
        )}
        {searching && <div className="mt-3"><SearchForm item={item} busy={busy} onSearch={search} onClose={() => setSearching(false)} /></div>}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {!searching && (
            <Button onClick={() => setSearching(true)}>
              <Search size={15} aria-hidden /> {t('review.other')}
            </Button>
          )}
          <Button onClick={keep} busy={busy}>
            {t('review.keep')}
          </Button>
          <span className="text-xs text-muted">{t('review.keepHint')}</span>
          <button type="button" className="peek-btn ml-auto" onClick={throwAway} data-testid="review-trash">
            <Trash2 size={15} aria-hidden /> {t('review.trash')}
          </button>
        </div>
        <Feedback problem={problem} values={values} />
      </div>
      {listening && (
        <Dialog title={item.title} sub={t('review.peek.listen')} onClose={() => setListening(false)} above>
          <PeekListen editionId={item.edition_id} title={item.title} />
        </Dialog>
      )}
      {factsOpen && facts && (
        <div className="review-facts" data-testid="review-facts-panel">
          <FilePanel facts={facts} />
        </div>
      )}
      {reading && <PeekBook editionId={item.edition_id} title={item.title} onClose={() => setReading(false)} />}
      {dialog}
    </article>
  )
}

export function ReviewPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const [libraries, setLibraries] = useState<Library[] | null>(null)
  const [items, setItems] = useState<ReviewItem[]>([])
  const [total, setTotal] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    librariesApi.mine().then(
      (found) => setLibraries(listOf<Library>(found).filter((library) => library.my_right === 'manage')),
      () => setLibraries([]),
    )
  }, [])
  // Every library the person looks after in one list, as the note on the library page counts them; one library
  // only when it is chosen above (or named in the address).
  const asked = Number(params.get('library'))
  const current = libraries?.find((library) => library.id === asked)
  const currentId = libraries && libraries.length > 0 ? (current?.id ?? 0) : undefined
  const names = new Map((libraries ?? []).map((library) => [library.id, library.name]))
  const all = (libraries ?? []).reduce((sum, library) => sum + library.counts.review, 0)

  const load = useCallback(async (library: number, offset = 0) => {
    setLoading(true)
    try {
      const page = library === 0 ? await reviewApi.listAll(PAGE, offset) : await reviewApi.list(library, PAGE, offset)
      setItems((now) => (offset === 0 ? listOf<ReviewItem>(page.items) : [...now, ...listOf<ReviewItem>(page.items)]))
      setTotal(Number(page.total) || 0)
      setProblem(null)
    } catch (error) {
      setProblem(error instanceof ApiError ? error.code : 'internal_error')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    if (currentId !== undefined) void load(currentId)
  }, [currentId, load])

  const gone = (id: number) => {
    setItems((now) => now.filter((item) => item.id !== id))
    setTotal((now) => Math.max(0, now - 1))
  }
  const changed = (next: ReviewItem) => setItems((now) => now.map((item) => (item.id === next.id ? next : item)))

  return (
    <div className="view">
      <PageHead title={t('review.title')} text={t('review.text')}>
        {libraries && libraries.length > 1 && (
          <div className="seg" role="group" aria-label={t('review.library')}>
            <button type="button" className={currentId === 0 ? 'on' : ''} aria-pressed={currentId === 0} onClick={() => setParams({})} data-library="all">
              {t('review.all')}
              {all > 0 && <span className="ml-1 text-xs text-muted">{all}</span>}
            </button>
            {libraries.map((library) => (
              <button key={library.id} type="button" className={library.id === currentId ? 'on' : ''} aria-pressed={library.id === currentId} onClick={() => setParams({ library: String(library.id) })}>
                {library.name}
                {library.counts.review > 0 && <span className="ml-1 text-xs text-muted">{library.counts.review}</span>}
              </button>
            ))}
          </div>
        )}
      </PageHead>
      {problem && (
        <div className="mb-4">
          <Feedback problem={problem} />
        </div>
      )}
      {libraries && libraries.length === 0 && (
        <p className="empty" data-testid="review-no-library">
          <Trans i18nKey="review.noLibrary" components={{ settings: <Link to="/settings?tab=libraries" /> }} />
        </p>
      )}
      {currentId !== undefined && !loading && items.length === 0 && !problem && (
        <p className="empty" data-testid="review-empty">
          {t('review.empty')}
        </p>
      )}
      <div className="space-y-5">
        {items.map((item) => (
          <Entry key={item.id} item={item} library={currentId === 0 && (libraries?.length ?? 0) > 1 ? names.get(item.library_id) : undefined} onGone={gone} onChanged={changed} />
        ))}
      </div>
      {items.length < total && currentId !== undefined && (
        <div className="mt-6 flex justify-center">
          <Button onClick={() => void load(currentId, items.length)} busy={loading}>
            {t('review.more', { shown: items.length, total })}
          </Button>
        </div>
      )}
    </div>
  )
}
