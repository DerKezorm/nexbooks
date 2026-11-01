/**
 * "Releases ansehen": the manual search of the indexers for one book. While it
 * runs, a line per indexer ("fragt …", "12 Treffer", "pausiert bis 01:00", "Schlüssel abgelehnt"); then a card per kind
 * ("würde nehmen" with the release, or "nichts passt" with the most frequent reasons) and the table of every release of
 * the chosen kind with its rank, indexer, format, language (detected or presumed), size, age, peers or grabs, points and
 * result. A row opens to its reasons; a release that passes loads at once, one that was turned down only after a
 * question ("Trotzdem laden"). Blocking and unblocking ask first. On a phone the table folds its columns into the name.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type IndexerProgress, type Reason, type ReleaseCard, type ReleaseKind, type ReleaseOut, type ReleaseSearch } from '../../api/client'
import { whenText } from '../../lib/ago'
import { errorText } from '../../lib/errors'
import { DOUBTS, sizeText } from '../../lib/releases'
import { AudioIcon, BookIcon } from '../BookCard'
import { Dialog } from '../Dialog'
import { useConfirm } from '../useConfirm'

/** How often the dialog asks how far the search is. */
const POLL_MS = 900

function Check() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 12.5l4.5 4.5L19 7" />
    </svg>
  )
}

function Alert() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 3 2.5 20h19z" />
      <path d="M12 10v5M12 17.6v.1" />
    </svg>
  )
}

function Caret() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M6 9l6 6 6-6" />
    </svg>
  )
}

function Refresh() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 11a8 8 0 1 0-2.3 5.7" />
      <path d="M20 4v7h-7" />
    </svg>
  )
}

function timeOf(iso: string, language: string): string {
  try {
    return new Date(iso).toLocaleTimeString(language, { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

/** The texts of the dialog that depend on the page's language. */
function useWords() {
  const { t, i18n } = useTranslation()
  const names = (() => {
    try {
      return new Intl.DisplayNames([i18n.language], { type: 'language' })
    } catch {
      return null
    }
  })()
  const language = (code: string) => {
    const named = code ? names?.of(code) : ''
    return named ? named.charAt(0).toLocaleUpperCase(i18n.language) + named.slice(1) : code.toUpperCase()
  }
  const format = (code: string | null | undefined) => (code ? (i18n.exists(`procure.formats.name.${code}`) ? t(`procure.formats.name.${code}`) : code.toUpperCase()) : t('procure.releases.formatUnknown'))
  const size = (bytes: number | null) => (bytes === null ? '·' : sizeText(bytes, i18n.language))
  const chip = (reason: Reason) => t(`procure.releases.reasons.${reason.code}.chip`, { defaultValue: reason.code })
  const text = (reason: Reason, wanted: string[]) => {
    const values: Record<string, string> = { wanted: wanted.map(language).join(' / ') }
    if (reason.code === 'other_language' || reason.code === 'language_unsure') values.language = language(String(reason.arg ?? ''))
    if (reason.code === 'format_not_wanted' || reason.code === 'not_better') values.format = format(String(reason.arg ?? ''))
    // An abridged reading is turned down by the settings, or because an unabridged one fits and goes first.
    const key = reason.code === 'abridged' && reason.arg === 'alternative' ? 'abridgedAlternative' : reason.code
    return t(`procure.releases.reasons.${key}.text`, { ...values, defaultValue: reason.code })
  }
  return { t, i18n, language, format, size, chip, text }
}

/** Why an indexer is not asked: one line of the note that stands where the cards would. */
type Blocker = { key: string; cause: string; name: string; until: string | null; limit: number | null }

/** The causes the progress of a finished search can name by the code of the failure. */
const CAUSE_OF_ERROR: Record<string, string> = {
  indexer_daily_limit: 'daily_limit',
  indexer_share_used: 'share_used',
  indexer_limit_reached: 'request_limit',
  indexer_key_rejected: 'key_rejected',
  indexer_unreachable: 'server_error',
  indexer_timeout: 'server_error',
  indexer_http_error: 'server_error',
}

/** What the search itself ran into (an indexer at its limit when it asked): from its progress lines. */
function blockersOfSearch(found: ReleaseSearch): Blocker[] {
  const list: Blocker[] = []
  for (const entry of found.indexers) {
    const cause = CAUSE_OF_ERROR[entry.error]
    if (!cause) continue
    if (cause !== 'key_rejected' && !entry.until) continue
    list.push({ key: `p${entry.id}`, cause, name: entry.name, until: entry.until, limit: entry.limit })
  }
  return list
}

/** What keeps a new search from asking right now, read live: the indexers that rest and the searches of the day used up. */
function blockersNow(found: ReleaseSearch): Blocker[] {
  const list: Blocker[] = []
  if (found.searches_used_until) list.push({ key: 'used', cause: 'searches_used', name: '', until: found.searches_used_until, limit: null })
  for (const entry of found.resting) list.push({ key: `r${entry.id}`, cause: entry.cause, name: entry.name, until: entry.until, limit: entry.limit })
  return list
}

function LimitNote({ blockers, heading, testid }: { blockers: Blocker[]; heading: string; testid: string }) {
  const { t, i18n } = useTranslation()
  return (
    <div className="rel-limit" role="status" data-testid={testid}>
      <p className="rel-limit-h">{heading}</p>
      <ul>
        {blockers.map((item) => {
          const key = `procure.releases.limit.${item.cause}${item.limit ? '' : 'Free'}`
          const text = t(i18n.exists(key) ? key : `procure.releases.limit.${item.cause}`, {
            name: item.name,
            limit: item.limit ? item.limit.toLocaleString(i18n.language) : '',
            time: item.until ? whenText(item.until, i18n.language) : '',
          })
          return (
            <li key={item.key} data-cause={item.cause} data-testid="limit-note-item">
              {text}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function ProgressRow({ entry, done }: { entry: IndexerProgress; done: boolean }) {
  const { t, i18n } = useTranslation()
  const finished = done || ['done', 'failed', 'timeout', 'skipped'].includes(entry.state)
  const ok = entry.state === 'done'
  let right = t('procure.releases.asking')
  if (finished) {
    if (ok) right = t('procure.releases.hits', { count: entry.found })
    else if (entry.until && (entry.error === 'indexer_limit_reached' || entry.error === 'indexer_daily_limit' || entry.error === 'indexer_share_used')) right = t('procure.releases.resting', { time: timeOf(entry.until, i18n.language) })
    else if (entry.error === 'indexer_key_rejected') right = t('procure.releases.keyRejected')
    else if (entry.state === 'timeout') right = t('procure.releases.timeout')
    else if (entry.state === 'skipped') right = t('procure.releases.skipped')
    else right = errorText(entry.error || 'internal_error')
  }
  return (
    <div className={`irow ${finished ? (ok ? 'ok' : 'warn') : ''}`} data-testid="indexer-row" data-state={entry.state}>
      <span className="ii">{finished ? ok ? <Check /> : <Alert /> : <i className="spin" aria-hidden />}</span>
      <b>{entry.name}</b>
      <small>{entry.kind === 'newznab' ? 'Newznab' : 'Torznab'}</small>
      <span className="ir">{right}</span>
    </div>
  )
}

function Card({ card, on, onPick }: { card: ReleaseCard; on: boolean; onPick: () => void }) {
  const { t, language, format, size, chip } = useWords()
  const top = card.top
  const Icon = card.kind === 'ebook' ? BookIcon : AudioIcon
  return (
    <button type="button" className={`fcard ${on ? 'on' : ''} ${top ? 'good' : 'none'}`} aria-pressed={on} onClick={onPick} data-testid="release-card" data-kind={card.kind}>
      <span className="fh">
        <Icon />
        <b>{t(`procure.releases.kind.${card.kind}`)}</b>
        <span className={`rchip ${top ? 'ok' : 'warn'}`}>{top ? t('procure.releases.wouldTake') : t('procure.releases.nothing')}</span>
      </span>
      {top ? (
        <>
          <span className="rel" dir="auto">
            {top.title}
          </span>
          <small>
            {top.format && <span className="tno">{`${String(top.tier + 1).padStart(2, '0')} `}</span>}
            {t('procure.releases.topLine', { format: format(top.format), size: size(top.size), indexer: top.indexer, language: language(top.language), how: top.language_sure ? t('procure.releases.sure') : t('procure.releases.guessed') })}
          </small>
          {card.below_goal && <small className="goalnote">{t('procure.releases.below', { goal: format(card.goal) })}</small>}
        </>
      ) : (
        <span className="fn">{card.total === 0 ? t('procure.releases.noneFound') : t('procure.releases.none', { count: card.total })}</span>
      )}
      <span className="fw">
        <small>{t('procure.releases.passing', { ok: card.passing, total: card.total })}</small>
        {card.reasons.map((reason) => (
          <span key={reason.code} className="rchip">
            {t('procure.releases.reasonCount', { count: reason.count, chip: chip({ code: reason.code }) })}
          </span>
        ))}
      </span>
    </button>
  )
}

function Row({ release, open, onToggle, wanted, onLoad, onBlock, onUnblock, busy, first }: { release: ReleaseOut; open: boolean; onToggle: () => void; wanted: string[]; onLoad: (release: ReleaseOut, force: boolean) => void; onBlock: (release: ReleaseOut) => void; onUnblock: (release: ReleaseOut) => void; busy: boolean; first: boolean }) {
  const { t, language, format, size, chip, text } = useWords()
  const bad = release.reasons.length > 0
  const top = !bad && release.rank === 1
  const tierNo = release.format ? `${String(release.tier + 1).padStart(2, '0')} ` : ''
  const peers = release.protocol === 'torrent' ? (release.peers ?? release.seeders) : release.grabs
  return (
    <>
      <tr className={`${bad ? 'rej' : 'okr'} ${top ? 'best' : ''} ${open ? 'open' : ''}`} data-row={release.id} tabIndex={0} onClick={onToggle} onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && (event.preventDefault(), onToggle())} aria-expanded={open} data-testid="release-row">
        <td className="c-rank">{bad ? '·' : release.rank}</td>
        <td>
          <span className="rel" dir="auto">
            {release.title}
          </span>
          <small className="hide-l">
            {release.indexer} · {format(release.format)} · {size(release.size)}
          </small>
        </td>
        <td className="hide-s">
          {release.indexer}
          <small>{release.protocol === 'torrent' ? t('procure.releases.torrent') : t('procure.releases.usenet')}</small>
        </td>
        <td className="hide-s">
          {tierNo && <span className="tno">{tierNo}</span>}
          {format(release.format)}
        </td>
        <td className="hide-s">
          {language(release.language)}
          <small>{release.language_sure ? t('procure.releases.sure') : t('procure.releases.guessed')}</small>
        </td>
        <td className="r hide-s">{size(release.size)}</td>
        <td className="r hide-s">{release.age_days === null ? '·' : t('procure.releases.days', { count: release.age_days })}</td>
        <td className="r hide-s">{peers ?? '·'}</td>
        <td className="r hide-s">{bad ? '·' : release.score}</td>
        <td>
          {bad ? (
            <span className="rchips">
              {release.reasons.map((reason) => (
                <span key={reason.code} className={DOUBTS.has(reason.code) ? 'rchip warn' : 'rchip'} data-doubt={DOUBTS.has(reason.code) ? '' : undefined}>
                  {chip(reason)}
                </span>
              ))}
            </span>
          ) : top ? (
            <span className="rchip ok">{t('procure.releases.wouldTake')}</span>
          ) : (
            <span className="rchip">{t('procure.releases.fits')}</span>
          )}
          {bad && (
            <span className="car">
              <Caret />
            </span>
          )}
        </td>
      </tr>
      {open && (
        <tr className="why-row" data-testid="why-row">
          <td />
          <td colSpan={9}>
            {bad && (
              <ul className="whyl">
                {release.reasons.map((reason) => (
                  <li key={reason.code}>{text(reason, wanted)}</li>
                ))}
              </ul>
            )}
            <div className="wact">
              {bad ? (
                <>
                  <button type="button" className="bbtn small" disabled={busy} onClick={() => onLoad(release, true)} data-testid="force">
                    {t('procure.releases.force')}
                  </button>
                  <span className="muted">{t('procure.releases.forceNote')}</span>
                </>
              ) : (
                <button type="button" className="bbtn small primary" disabled={busy} onClick={() => onLoad(release, false)} data-testid="take">
                  {first ? t('procure.releases.take') : t('procure.releases.takeInstead')}
                </button>
              )}
              {release.blocked_by !== null ? (
                <button type="button" className="bbtn small" disabled={busy} onClick={() => onUnblock(release)} data-testid="unblock">
                  {t('procure.releases.unblock')}
                </button>
              ) : (
                // A block made in another library turns the release down here too, and is not this person's to lift.
                !release.reasons.some((item) => item.code === 'blocklisted') && (
                  <button type="button" className="bbtn small" disabled={busy} onClick={() => onBlock(release)} data-testid="block">
                    {t('procure.releases.block')}
                  </button>
                )
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** The dialog for a work of a library (`workId`) or a book of the sources (`book`, the token of its page). `kind` picks the
 * card that is open first. `onLoaded` gets the sentence to show after a release went to a program. */
export function ReleasesDialog({ workId, book, title, author, kind, language, onClose, onLoaded }: { workId?: number; book?: string; title: string; author: string; kind?: ReleaseKind; language?: string; onClose: () => void; onLoaded: (said: string) => void }) {
  const { t, i18n } = useTranslation()
  const words = useWords()
  const [found, setFound] = useState<ReleaseSearch | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [selected, setSelected] = useState<ReleaseKind>(kind ?? 'ebook')
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const { ask, dialog } = useConfirm()
  const alive = useRef(true)
  const searchId = useRef<number | null>(null)
  const timer = useRef(0)

  const read = useCallback(async (id: number) => {
    const next = await procureApi.readSearch(id)
    if (alive.current) setFound(next)
    return next
  }, [])

  /** Opens the dialog on the search of this book: the one that is running, or one that finished a few minutes ago, else a
   * new one. `fresh` asks the indexers again ("Neu suchen"). */
  const begin = useCallback(
    async (fresh: boolean) => {
      window.clearTimeout(timer.current)
      // The language of the wish it was opened from, else every language the operator allows (settings, "Sprachen"):
      // the language of the interface plays no part.
      const wanted = language ? { language } : {}
      const poll = async (id: number) => {
        try {
          const next = await read(id)
          if (alive.current && (next.state === 'queued' || next.state === 'running')) timer.current = window.setTimeout(() => void poll(id), POLL_MS)
        } catch (error) {
          if (alive.current) setProblem(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
        }
      }
      try {
        const body = workId !== undefined ? { work_id: workId, ...wanted } : { book, ...wanted }
        const started = await procureApi.search(fresh ? { ...body, new: true } : body)
        if (!alive.current) return
        searchId.current = started.id
        setFound(started)
        if (!started.kinds.includes(kind ?? 'ebook') && started.kinds.length > 0) setSelected(started.kinds[0])
        if (started.state === 'queued' || started.state === 'running') timer.current = window.setTimeout(() => void poll(started.id), POLL_MS)
      } catch (error) {
        if (!alive.current) return
        if (error instanceof ApiError && error.code === 'no_indexers') setProblem(t('procure.releases.noIndexers'))
        else setProblem(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workId, book, kind, language],
  )

  useEffect(() => {
    alive.current = true
    void begin(false)
    return () => {
      alive.current = false
      window.clearTimeout(timer.current)
    }
    // The search starts once, when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const again = () => {
    setFound(null)
    setOpen(new Set())
    setNote(null)
    setProblem(null)
    void begin(true)
  }

  const load = (release: ReleaseOut, force: boolean) => {
    if (!found) return
    const go = async () => {
      setBusy(true)
      setNote(null)
      try {
        const done = await procureApi.load(found.id, release.id, force)
        onLoaded(done.state === 'unsure' ? t('procure.releases.unsure', { client: done.client, title: release.title }) : t('procure.releases.loadingNow', { title: release.title }))
        onClose()
      } catch (error) {
        setNote(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
      } finally {
        if (alive.current) setBusy(false)
      }
    }
    if (!force) {
      void go()
      return
    }
    ask({
      title: t('procure.releases.forceTitle'),
      text: t('procure.releases.forceText', { reasons: release.reasons.map((reason) => words.text(reason, found.languages)).join(' ') }),
      confirm: t('procure.releases.force'),
      danger: false,
      run: go,
    })
  }

  const block = (release: ReleaseOut) => {
    if (!found) return
    ask({
      title: t('procure.releases.blockTitle'),
      text: t('procure.releases.blockText'),
      confirm: t('procure.releases.block'),
      run: async () => {
        try {
          await procureApi.block(found.id, release.id)
          await read(found.id)
          setNote(t('procure.releases.blocked'))
        } catch (error) {
          setNote(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
        }
      },
    })
  }

  const unblock = (release: ReleaseOut) => {
    if (!found || release.blocked_by === null) return
    const entry = release.blocked_by
    ask({
      title: t('procure.releases.unblockTitle'),
      text: t('procure.releases.unblockText'),
      confirm: t('procure.releases.unblock'),
      danger: false,
      run: async () => {
        try {
          await procureApi.unblock(entry)
          await read(found.id)
          setNote(t('procure.releases.unblocked'))
        } catch (error) {
          setNote(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
        }
      },
    })
  }

  const done = found?.state === 'done'
  // Nothing was asked because the indexers were at their limits: say so where the cards would be, instead of "no release
  // found". An earlier search with hits stays visible below the note; a new search is not offered while none can ask.
  const noHits = done && found.releases.length === 0
  const searched = found?.indexers.some((entry) => entry.state === 'done') ?? false
  const cannotAgain = done && !found.can_search_again
  const blockersLive = cannotAgain ? blockersNow(found) : []
  const blockersThen = noHits && !searched ? blockersOfSearch(found) : []
  const blockers = blockersLive.length > 0 ? blockersLive : blockersThen
  const unasked = noHits && !searched && blockers.length > 0
  const noteHeading = unasked ? t('procure.releases.notAsked') : found && found.releases.length > 0 ? t('procure.releases.earlier', { when: whenText(found.finished_at ?? found.created_at, i18n.language) }) : t('procure.releases.nowResting')
  const rows = done ? found.releases.filter((release) => release.kind === selected) : []
  const firstRank = rows.find((release) => release.rank === 1)?.id
  return (
    <Dialog title={t('procure.releases.title')} sub={author ? `${title} · ${author}` : title} onClose={onClose} broad className="rel-dlg">
      {problem ? (
        <p className="rel-empty" role="alert" data-testid="release-problem">
          {problem}
        </p>
      ) : (
        <>
          {found && (
            <div className="isearch" data-testid="indexer-progress">
              <p className="isr-h">
                {done || found.state === 'failed' ? t('procure.releases.finished') : t('procure.releases.running')}
                {(done || found.state === 'failed') && found.finished_at && (
                  <span className="isr-at"> · {cannotAgain ? t('procure.releases.searchedWhen', { when: whenText(found.finished_at, i18n.language) }) : t('procure.releases.searchedAt', { time: timeOf(found.finished_at, i18n.language) })}</span>
                )}
              </p>
              {found.indexers.map((entry) => (
                <ProgressRow key={entry.id} entry={entry} done={done} />
              ))}
            </div>
          )}
          {found?.state === 'failed' && (
            <p className="rel-empty" role="alert" data-testid="search-failed">
              {t('procure.releases.failed')}
            </p>
          )}
          {(done || found?.state === 'failed') && (
            <div className="rel-again">
              <button type="button" className="bbtn small" onClick={again} disabled={cannotAgain} title={cannotAgain ? t('procure.releases.againBlocked') : t('procure.releases.againHint')} data-testid="search-again">
                <Refresh />
                {t('procure.releases.again')}
              </button>
            </div>
          )}
          {done && blockers.length > 0 && <LimitNote blockers={blockers} heading={noteHeading} testid="limit-note" />}
          {done && !unasked && (
            <div data-testid="release-results">
              <div className="fcards">
                {found.cards.map((card) => (
                  <Card
                    key={card.kind}
                    card={card}
                    on={card.kind === selected}
                    onPick={() => {
                      setSelected(card.kind)
                      setOpen(new Set())
                    }}
                  />
                ))}
              </div>
              <h3 className="rt-h">{t('procure.releases.all', { kind: t(`procure.releases.kind.${selected}`) })}</h3>
              {note && (
                <p role="status" className="mb-2 text-sm text-muted" data-testid="release-note">
                  {note}
                </p>
              )}
              <div className="rtab-w">
                <table className="rtab">
                  <thead>
                    <tr>
                      <th className="c-rank">{t('procure.releases.col.rank')}</th>
                      <th>{t('procure.releases.col.release')}</th>
                      <th className="hide-s">{t('procure.releases.col.indexer')}</th>
                      <th className="hide-s">{t('procure.releases.col.format')}</th>
                      <th className="hide-s">{t('procure.releases.col.language')}</th>
                      <th className="r hide-s">{t('procure.releases.col.size')}</th>
                      <th className="r hide-s">{t('procure.releases.col.age')}</th>
                      <th className="r hide-s">{t('procure.releases.col.peers')}</th>
                      <th className="r hide-s">{t('procure.releases.col.score')}</th>
                      <th>{t('procure.releases.col.result')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((release) => (
                      <Row
                        key={release.id}
                        release={release}
                        open={open.has(release.id)}
                        wanted={found.languages}
                        busy={busy}
                        first={release.id === firstRank}
                        onToggle={() =>
                          setOpen((before) => {
                            const next = new Set(before)
                            if (next.has(release.id)) next.delete(release.id)
                            else next.add(release.id)
                            return next
                          })
                        }
                        onLoad={load}
                        onBlock={block}
                        onUnblock={unblock}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
      {dialog}
    </Dialog>
  )
}
