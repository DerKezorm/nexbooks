/**
 * "In andere Bibliothek verschieben": the libraries the person manages that take the kind of the
 * edition (one nexbooks may not write in is greyed out with the reason), where the files would lie there by the scheme of
 * filing, what happens, and the question before anything moves. The move runs in the background; the dialog follows it
 * and says where the files lie when it stops, with another try after a failure.
 *
 * The edition stays the same: its progress, bookmarks and history stay. Nothing is sent before the click on the button of
 * the question (rule 13), and the server checks the rights again (`/editions/{id}/move`).
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, movesApi, type EditionOut, type MoveOptions, type MoveOut } from '../api/client'
import { formatBytes } from '../lib/bytes'
import { useEditionLabel } from '../lib/editionText'
import { errorText } from '../lib/errors'
import { iso } from '../lib/say'
import { Dialog } from './Dialog'
import { shortWhy } from './procure/writeWhy'
import { useConfirm } from './useConfirm'

/** How often a running move is looked at (milliseconds). */
export const MOVE_POLL_MS = 1500

const WHY = ['no_space', 'taken', 'target_taken', 'source_changed', 'target_changed', 'source_left', 'source_not_writable', 'target_not_writable', 'no_write', 'link', 'too_long', 'gone', 'moved_meanwhile', 'outside']

export function MoveDialog({ edition, onClose, onMoved }: { edition: EditionOut; onClose: () => void; onMoved: (text: string) => void }) {
  const { t, i18n } = useTranslation()
  const { label } = useEditionLabel()
  const { ask, dialog } = useConfirm()
  const [options, setOptions] = useState<MoveOptions | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  const [move, setMove] = useState<MoveOut | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    movesApi.options(edition.id).then(
      (found) => {
        setOptions(found)
        setFailure(null)
        // A move that has not ended yet, or failed, is shown; a finished one is history.
        if (found.latest && found.latest.state !== 'done') setMove(found.latest)
      },
      (error: unknown) => setFailure(error instanceof ApiError ? error.code : 'internal_error'),
    )
  }, [edition.id])
  useEffect(load, [load])

  // While the move runs, the dialog looks how far it is.
  const running = move !== null && (move.state === 'queued' || move.state === 'running')
  useEffect(() => {
    if (!running || !move) return
    const timer = window.setInterval(() => {
      movesApi.read(move.id).then(
        (found) => {
          setMove(found)
          if (found.state === 'done') onMoved(t('move.state.done', { target: found.target?.name ?? '' }))
        },
        () => undefined,
      )
    }, MOVE_POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, move, onMoved, t])

  const text = label(edition)
  const choosable = (options?.targets ?? []).filter((item) => item.writable && !item.problem)
  const chosen = options?.targets.find((item) => item.id === (picked ?? choosable[0]?.id)) ?? null
  const source = options?.source.name ?? edition.library_name
  const why = (code: string) => t(`move.why.${WHY.includes(code) ? code : 'failed'}`)

  const go = () => {
    if (!chosen || !options) return
    ask({
      title: t('move.confirm.title', { library: iso(chosen.name) }),
      text: t('move.confirm.text', { source: iso(source), target: iso(chosen.name) }),
      confirm: t('move.confirm.confirm'),
      danger: false,
      run: async () => {
        setMove(await movesApi.start(edition.id, chosen.id))
      },
    })
  }
  const again = async () => {
    if (!move || busy) return
    setBusy(true)
    try {
      setMove(await movesApi.retry(move.id))
    } catch (error) {
      setFailure(error instanceof ApiError ? error.code : 'internal_error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Dialog title={t('move.title')} sub={t('move.sub', { edition: text.main, library: source })} onClose={onClose} above>
        <div className="space-y-4 text-sm" data-testid="move">
          {failure && (
            <p role="alert" className="text-bad">
              {errorText(failure)}
            </p>
          )}
          {!options && !failure && <p className="text-muted">{t('move.loading')}</p>}
          {move && (
            <div className="space-y-2" data-testid="move-state" data-state={move.state}>
              <p className={move.state === 'failed' ? 'font-semibold text-bad' : 'font-semibold'}>
                {move.state === 'failed'
                  ? t('move.state.failed', { why: why(move.error) })
                  : t(`move.state.${move.state}`, { placed: move.placed, files: move.files, target: move.target?.name ?? '' })}
              </p>
              {move.state === 'failed' && (
                <p className="text-ink-2" data-testid="move-where">
                  {move.where === 'source'
                    ? t('move.where.source', { source: move.source?.name ?? source })
                    : move.step === 'place'
                      ? t('move.where.bothPlace', { source: move.source?.name ?? source, target: move.target?.name ?? '', count: move.left })
                      : t('move.where.bothClear', { source: move.source?.name ?? source, target: move.target?.name ?? '', count: move.left })}
                </p>
              )}
              <div className="flex justify-end gap-2">
                {move.state === 'failed' && move.step === 'place' && (
                  // Before the switch the book lies whole where it lay: another target may be chosen instead.
                  <button type="button" className="h-10 rounded-full border border-line px-5 font-semibold hover:bg-sheet-2" onClick={() => setMove(null)} data-act="move-other">
                    {t('move.other')}
                  </button>
                )}
                {move.state === 'failed' && (
                  <button type="button" className="h-10 rounded-full bg-accent px-5 font-semibold text-accent-ink hover:brightness-105 disabled:opacity-50" disabled={busy} onClick={() => void again()} data-act="move-retry">
                    {t('move.retry')}
                  </button>
                )}
                {move.state === 'done' && (
                  <button type="button" className="h-10 rounded-full border border-line px-5 font-semibold hover:bg-sheet-2" onClick={onClose}>
                    {t('common.close')}
                  </button>
                )}
              </div>
            </div>
          )}
          {options && !move && (
            <>
              {options.targets.length === 0 ? (
                <p className="text-muted" data-testid="move-none">
                  {t('move.none')}
                </p>
              ) : (
                <>
                  <label className="fix-field">
                    {t('move.target')}
                    <select className="input" value={chosen?.id ?? ''} onChange={(event) => setPicked(Number(event.target.value))} data-testid="move-target">
                      {options.targets.map((item) => (
                        <option key={item.id} value={item.id} disabled={!item.writable || Boolean(item.problem)}>
                          {item.writable ? (item.problem ? t('move.noPlaceOption', { name: item.name }) : item.name) : t('procure.formats.place.unwritableOption', { name: item.name, why: shortWhy(item.write_problem, t) })}
                        </option>
                      ))}
                    </select>
                  </label>
                  {chosen && chosen.preview.length > 0 && (
                    <div className="space-y-1" data-testid="move-preview">
                      <small className="block text-muted">{t('move.preview', { library: chosen.name })}</small>
                      {chosen.preview.map((path) => (
                        <code key={path} className="block break-all rounded-lg bg-sheet-2 px-2.5 py-1.5 font-mono text-xs" dir="auto">
                          {path}
                        </code>
                      ))}
                      {chosen.more > 0 && <small className="block text-muted">{t('move.more', { count: chosen.more })}</small>}
                    </div>
                  )}
                  {chosen === null && <p className="text-muted">{t('move.noneChoosable')}</p>}
                  <p className="text-ink-2" data-testid="move-what">
                    {t('move.what', { files: t('move.files', { count: options.files }), size: formatBytes(options.bytes, i18n.language, 1000) })}
                  </p>
                  {chosen?.copies && <p className="text-muted">{t('move.copies')}</p>}
                  {!options.source.writable && (
                    <p role="alert" className="text-bad">
                      {t('move.sourceReadOnly', { source, why: shortWhy(options.source.write_problem, t) })}
                    </p>
                  )}
                  {options.busy && (
                    <p role="status" className="text-warn">
                      {t('move.busy')}
                    </p>
                  )}
                </>
              )}
              <div className="flex justify-end gap-2">
                <button type="button" className="h-10 rounded-full border border-line px-5 font-semibold hover:bg-sheet-2" onClick={onClose}>
                  {t('common.cancel')}
                </button>
                {options.targets.length > 0 && (
                  <button type="button" className="h-10 rounded-full bg-accent px-5 font-semibold text-accent-ink hover:brightness-105 disabled:opacity-50" disabled={!chosen || options.busy || !options.source.writable} onClick={go} data-act="move-go">
                    {t('move.go')}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </Dialog>
      {dialog}
    </>
  )
}
