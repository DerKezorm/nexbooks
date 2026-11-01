/**
 * The hand of the person who looks after a library on the page of a book: correct what is written about an edition, take
 * back what the sources said, make an edition a book of its own, or put it under another book. Every one of them asks
 * first and says whether it can be taken back; nothing is sent before the one click on the button of the question.
 *
 * The routes are `/editions/{id}`, `/unlink`, `/split` and `/join`; the server decides who may use them
 * (a member with `manage` in the library of the edition). The page only offers them to such a member.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, editionsApi, worksApi, type Correction, type Discarded, type EditionOut, type SentAway, type WorkBrief, type WorkOut } from '../api/client'
import { authorLine } from '../lib/books'
import { changesOf, formOf, type Form } from '../lib/manageForm'
import { useEditionLabel } from '../lib/editionText'
import { errorText } from '../lib/errors'
import { iso } from '../lib/say'
import { Dialog } from './Dialog'
import { useConfirm } from './useConfirm'

const GENRES = ['roman', 'krimi', 'fantasy', 'scifi', 'klassiker', 'sachbuch', 'kinder', 'lyrik'] as const

function Join({ work, edition, onJoined, ask }: { work: WorkOut; edition: EditionOut; onJoined: (workId: number) => void; ask: ReturnType<typeof useConfirm>['ask'] }) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [found, setFound] = useState<WorkBrief[] | null>(null)
  const latest = useRef(0)
  useEffect(() => {
    const words = q.trim()
    if (!words) {
      setFound(null)
      return
    }
    const mine = ++latest.current
    const timer = window.setTimeout(() => {
      worksApi.search(words, 8).then(
        (answer) => mine === latest.current && setFound(answer.items.filter((item) => item.id !== work.id)),
        () => mine === latest.current && setFound([]),
      )
    }, 200)
    return () => window.clearTimeout(timer)
  }, [q, work.id])
  return (
    <div className="space-y-2">
      <input className="input" aria-label={t('work.edit.joinSearch')} placeholder={t('work.edit.joinSearch')} value={q} onChange={(event) => setQ(event.target.value)} />
      {found && found.length === 0 && <p className="text-sm text-muted">{t('work.edit.joinNone')}</p>}
      {found?.map((item) => (
        <button
          key={item.id}
          type="button"
          className="fix-row w-full text-left"
          onClick={() =>
            ask({
              title: t('work.confirm.join.title', { title: iso(item.title) }),
              text: t('work.confirm.join.text'),
              confirm: t('work.confirm.join.confirm'),
              danger: false,
              run: async () => {
                const done = await editionsApi.join(edition.id, item.id)
                onJoined(done.work_id)
              },
            })
          }
        >
          <span>
            <b>{item.title}</b>
            <p>{authorLine(item)}</p>
          </span>
        </button>
      ))}
    </div>
  )
}

export function ManageDialog({ work, editions, onClose, onChanged, onMove, onGone }: { work: WorkOut; editions: EditionOut[]; onClose: () => void; onChanged: (workId: number, said: string) => void; onMove?: (edition: EditionOut) => void; onGone?: () => void }) {
  const { t } = useTranslation()
  const { label } = useEditionLabel()
  const { ask, dialog } = useConfirm()
  const [chosen, setChosen] = useState(editions[0]!.id)
  const edition = editions.find((item) => item.id === chosen) ?? editions[0]!
  const before = formOf(edition)
  const [form, setForm] = useState<Form>(before)
  const [problem, setProblem] = useState<string | null>(null)
  const [joining, setJoining] = useState(false)
  // A book that was sent away: the dialog says what became of it instead of closing, so that the answer is read.
  const [sent, setSent] = useState<SentAway | null>(null)
  // An edition put in the trash: the same, the dialog says what became of it.
  const [trashed, setTrashed] = useState<Discarded | null>(null)
  useEffect(() => setForm(formOf(edition)), [edition])
  const set = (patch: Partial<Form>) => setForm((now) => ({ ...now, ...patch }))
  const changes = changesOf(before, form)
  const done = (workId: number, said: string) => {
    onChanged(workId, said)
    onClose()
  }
  const fail = (error: unknown) => {
    throw error instanceof ApiError ? error : new ApiError(0, 'internal_error')
  }
  const field = (name: keyof Form, text: string, input: ReactNode, hand: string | null) => (
    <div className="fix-field">
      <label htmlFor={`fix-${name}`}>{text}</label>
      {input}
      {hand && edition.corrected.includes(hand) && (
        <span className="fix-hand">
          {t('work.edit.byHand')} ·{' '}
          <button
            type="button"
            onClick={() =>
              ask({
                title: t('work.confirm.takeBack.title'),
                text: t('work.confirm.takeBack.text'),
                confirm: t('work.confirm.takeBack.confirm'),
                danger: false,
                run: async () => {
                  const patch = hand === 'series_pos' ? { series_pos: null } : { [hand]: null }
                  await editionsApi.correct(edition.id, patch as Correction).then((answer) => done(answer.work_id, t('work.edit.done')), fail)
                },
              })
            }
          >
            {t('work.edit.takeBack')}
          </button>
        </span>
      )}
    </div>
  )
  const finishSent = (result: SentAway) => {
    if (result.work_id !== null) done(result.work_id, t(`work.wrong.done.${result.replacement}`, { reason: result.code ? errorText(result.code) : '' }))
    else {
      onClose()
      onGone?.()
    }
  }
  if (trashed) {
    const finish = () => {
      if (trashed.work_id !== null) done(trashed.work_id, t('work.trashed.done'))
      else {
        onClose()
        onGone?.()
      }
    }
    return (
      <Dialog title={t('work.trashed.doneTitle')} onClose={finish} above>
        <div className="space-y-4" data-testid="trash-done">
          <p>{trashed.work_id !== null ? t('work.trashed.done') : t('work.trashed.doneGone')}</p>
          {trashed.missing.length > 0 && (
            <p className="text-sm" data-testid="trash-missing">
              {t('work.wrong.missing', { count: trashed.missing.length, files: trashed.missing.slice(0, 3).join(', ') })}
            </p>
          )}
          {trashed.people > 0 && (
            <p className="text-sm" data-testid="trash-people">
              {t('work.wrong.people', { count: trashed.people })}
            </p>
          )}
          <p className="text-sm text-muted">{trashed.trash_id === null ? t('work.trashed.doneBackNoFiles') : t('work.trashed.doneBack')}</p>
          <div className="flex justify-end">
            <button
              type="button"
              className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-semibold text-accent-ink hover:brightness-105"
              onClick={finish}
              data-testid="trash-done-ok"
            >
              {trashed.work_id !== null ? t('work.wrong.toBook') : t('work.wrong.toShelf')}
            </button>
          </div>
        </div>
      </Dialog>
    )
  }
  if (sent) {
    return (
      <Dialog title={t('work.wrong.doneTitle')} onClose={() => finishSent(sent)} above>
        <div className="space-y-4" data-testid="wrong-done" data-replacement={sent.replacement}>
          <p>{t(`work.wrong.done.${sent.replacement}`, { reason: sent.code ? errorText(sent.code) : '' })}</p>
          {sent.missing.length > 0 && (
            <p className="text-sm" data-testid="wrong-missing">
              {t('work.wrong.missing', { count: sent.missing.length, files: sent.missing.slice(0, 3).join(', ') })}
            </p>
          )}
          {sent.people > 0 && (
            <p className="text-sm" data-testid="wrong-people">
              {t('work.wrong.people', { count: sent.people })}
            </p>
          )}
          <p className="text-sm text-muted">{sent.trash_id === null ? t('work.wrong.doneBackNoFiles') : t('work.wrong.doneBack')}</p>
          <div className="flex justify-end">
            <button
              type="button"
              className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-semibold text-accent-ink hover:brightness-105"
              onClick={() => finishSent(sent)}
              data-testid="wrong-done-ok"
            >
              {sent.work_id !== null ? t('work.wrong.toBook') : t('work.wrong.toShelf')}
            </button>
          </div>
        </div>
      </Dialog>
    )
  }
  return (
    <>
      <Dialog title={t('work.edit.title')} onClose={onClose} above wide>
        <div className="space-y-5" data-testid="manage">
          <p className="text-sm text-muted">{t('work.edit.text')}</p>
          {editions.length > 1 && (
            <label className="fix-field">
              {t('work.edit.which')}
              <select className="input" value={chosen} onChange={(event) => setChosen(Number(event.target.value))}>
                {editions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {label(item).main} · {item.library_name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (Object.keys(changes).length === 0) {
                setProblem(t('work.edit.nothingChanged'))
                return
              }
              setProblem(null)
              ask({
                title: t('work.confirm.save.title'),
                text: t('work.confirm.save.text'),
                confirm: t('work.confirm.save.confirm'),
                danger: false,
                run: async () => {
                  await editionsApi.correct(edition.id, changes).then((answer) => done(answer.work_id, t('work.edit.saved')), fail)
                },
              })
            }}
          >
            <h3 className="font-display text-base font-semibold">{t('work.edit.fieldsTitle')}</h3>
            {field('title', t('work.edit.titleField'), <input id="fix-title" className="input" value={form.title} maxLength={300} onChange={(event) => set({ title: event.target.value })} />, 'title')}
            {field('subtitle', t('work.edit.subtitleField'), <input id="fix-subtitle" className="input" value={form.subtitle} maxLength={300} onChange={(event) => set({ subtitle: event.target.value })} />, 'subtitle')}
            {field('authors', t('work.edit.authors'), <textarea id="fix-authors" className="input" value={form.authors} onChange={(event) => set({ authors: event.target.value })} />, 'authors')}
            {edition.kind === 'audio' &&
              field('narrators', t('work.edit.narrators'), <textarea id="fix-narrators" className="input" value={form.narrators} onChange={(event) => set({ narrators: event.target.value })} />, 'narrators')}
            <div className="grid grid-cols-[1fr_6rem] gap-3">
              {field('series', t('work.edit.series'), <input id="fix-series" className="input" value={form.series} maxLength={200} onChange={(event) => set({ series: event.target.value })} />, 'series')}
              {field('pos', t('work.edit.pos'), <input id="fix-pos" className="input" inputMode="decimal" value={form.pos} onChange={(event) => set({ pos: event.target.value })} />, 'series_pos')}
            </div>
            <div className="grid grid-cols-2 gap-3">
              {field(
                'genre',
                t('work.edit.genre'),
                <select id="fix-genre" className="input" value={form.genre} onChange={(event) => set({ genre: event.target.value })}>
                  <option value="">{t('work.edit.genreNone')}</option>
                  {GENRES.map((genre) => (
                    <option key={genre} value={genre}>
                      {t(`library.genre.${genre}`)}
                    </option>
                  ))}
                </select>,
                'genre',
              )}
              {field('language', t('work.edit.language'), <input id="fix-language" className="input" value={form.language} maxLength={3} onChange={(event) => set({ language: event.target.value })} />, 'language')}
            </div>
            {problem && (
              <p role="alert" className="text-sm text-muted">
                {problem}
              </p>
            )}
            <div className="flex justify-end">
              <button type="submit" className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-semibold text-accent-ink hover:brightness-105">
                {t('work.edit.save')}
              </button>
            </div>
          </form>
          <div className="space-y-2">
            <h3 className="font-display text-base font-semibold">{t('work.edit.assignTitle')}</h3>
            {edition.matched_by.length > 0 && (
              <div className="fix-row">
                <span>
                  <b>{t('work.edit.unlink')}</b>
                  <p>
                    {t('work.edit.knownFrom', { sources: edition.matched_by.map((source) => t(`sources.names.${source}`)).join(', ') })}. {t('work.edit.unlinkHint')}
                  </p>
                </span>
                <button
                  type="button"
                  className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2"
                  onClick={() =>
                    ask({
                      title: t('work.confirm.unlink.title'),
                      text: t('work.confirm.unlink.text'),
                      confirm: t('work.confirm.unlink.confirm'),
                      run: async () => {
                        await editionsApi.unlink(edition.id).then(() => done(work.id, t('work.edit.done')), fail)
                      },
                    })
                  }
                >
                  {t('work.edit.unlink')}
                </button>
              </div>
            )}
            {onMove && edition.can_move && (
              // Every edition, also the ones the page shows only as a chip.
              <div className="fix-row">
                <span>
                  <b>{t('move.button')}</b>
                  <p>{t('work.edit.moveHint', { library: edition.library_name })}</p>
                </span>
                <button type="button" className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2" onClick={() => onMove(edition)} data-act="move-edition">
                  {t('work.edit.move')}
                </button>
              </div>
            )}
            {edition.delivery && (
              <div className="fix-row" data-testid="wrong-book-row">
                <span>
                  <b>{t('work.edit.wrong')}</b>
                  <p>{t('work.edit.wrongHint', { release: edition.delivery.release })}</p>
                </span>
                <button
                  type="button"
                  className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2"
                  data-act="wrong-book"
                  onClick={() =>
                    ask({
                      title: t('work.confirm.wrong.title'),
                      text: t('work.confirm.wrong.text', { release: edition.delivery?.release ?? '', count: edition.delivery?.trash_days ?? 30 }),
                      confirm: t('work.confirm.wrong.confirm'),
                      run: async () => {
                        setSent(await editionsApi.wrongBook(edition.id).catch(fail))
                      },
                    })
                  }
                >
                  {t('work.edit.wrongButton')}
                </button>
              </div>
            )}
            {(edition.trash_days ?? 0) > 0 && (
              <div className="fix-row" data-testid="trash-row">
                <span>
                  <b>{t('work.edit.trash')}</b>
                  <p>{t('work.edit.trashHint')}</p>
                </span>
                <button
                  type="button"
                  className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2"
                  data-act="trash-edition"
                  onClick={() =>
                    ask({
                      title: t('work.confirm.trash.title'),
                      text: t('work.confirm.trash.text', { count: edition.trash_days ?? 30 }),
                      confirm: t('work.confirm.trash.confirm'),
                      run: async () => {
                        setTrashed(await editionsApi.trash(edition.id).catch(fail))
                      },
                    })
                  }
                >
                  {t('work.edit.trashButton')}
                </button>
              </div>
            )}
            {work.editions.length > 1 && (
              <div className="fix-row">
                <span>
                  <b>{t('work.edit.split')}</b>
                  <p>{t('work.edit.splitHint')}</p>
                </span>
                <button
                  type="button"
                  className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2"
                  onClick={() =>
                    ask({
                      title: t('work.confirm.split.title'),
                      text: t('work.confirm.split.text'),
                      confirm: t('work.confirm.split.confirm'),
                      danger: false,
                      run: async () => {
                        await editionsApi.split(edition.id).then((answer) => done(answer.work_id, t('work.edit.done')), fail)
                      },
                    })
                  }
                >
                  {t('work.edit.split')}
                </button>
              </div>
            )}
            <div className="fix-row" style={{ display: 'block' }}>
              <div className="flex items-center justify-between gap-3">
                <span>
                  <b>{t('work.edit.join')}</b>
                  <p>{t('work.edit.joinHint')}</p>
                </span>
                <button type="button" className="h-9 shrink-0 rounded-full border border-line px-4 text-sm font-semibold hover:bg-sheet-2" aria-expanded={joining} onClick={() => setJoining((now) => !now)}>
                  {t('work.edit.join')}
                </button>
              </div>
              {joining && (
                <div className="mt-3">
                  <Join work={work} edition={edition} ask={ask} onJoined={(workId) => done(workId, t('work.edit.done'))} />
                </div>
              )}
            </div>
          </div>
        </div>
      </Dialog>
      {dialog}
    </>
  )
}
