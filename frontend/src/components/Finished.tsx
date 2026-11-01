/**
 * "Finished" on the card of an edition: the person says by hand that they finished the book (today, or on a day
 * they name), sees the day it was entered, and can take the entry back ("Not finished", after a question). The rule at the end
 * of the book (`services/history.py`) enters the pass by itself; this is for the rest: a book read on paper, a book finished
 * before nexbooks, a pass the rule did not see.
 *
 * One press, one request: a second press before the first has been answered does nothing, and the server enters the same day
 * only once anyway.
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, historyApi, type HistoryOut } from '../api/client'
import { dayText, localToday } from '../lib/days'
import { errorText } from '../lib/errors'
import { useConfirm } from './useConfirm'

export function FinishRow({ editionId, entries, onChange, onSaid }: { editionId: number; entries: HistoryOut[]; onChange: () => void; onSaid?: (text: string) => void }) {
  const { t, i18n } = useTranslation()
  const { ask, dialog } = useConfirm()
  const [problem, setProblem] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [day, setDay] = useState(localToday())
  const [busy, setBusy] = useState(false)
  // A press is handled once until the server has answered (a double click is one press).
  const sent = useRef(false)
  const latest = entries[0]
  const mark = async (finishedOn?: string) => {
    if (sent.current) return
    sent.current = true
    setBusy(true)
    setProblem(null)
    try {
      await historyApi.finish(editionId, finishedOn)
      setPicking(false)
      onSaid?.(t('finish.marked'))
      onChange()
    } catch (error) {
      setProblem(error instanceof ApiError ? error.code : 'internal_error')
    } finally {
      sent.current = false
      setBusy(false)
    }
  }
  const undo = () => {
    if (!latest) return
    ask({
      title: t('finish.undoTitle'),
      text: t('finish.undoText', { date: dayText(latest.finished_on, i18n.language) }),
      confirm: t('finish.undo'),
      danger: false,
      run: async () => {
        await historyApi.remove(latest.id)
        onSaid?.(t('finish.removed'))
        onChange()
      },
    })
  }
  return (
    <div className="tw-done" data-testid="finish" data-edition={editionId}>
      {latest ? (
        <p className="tw-when" data-testid="finish-done" title={latest.source === 'auto' ? t('finish.auto') : undefined}>
          <b>{t('finish.done', { date: dayText(latest.finished_on, i18n.language) })}</b>
          {entries.length > 1 && <span> · {t('finish.earlier', { count: entries.length - 1 })}</span>}
        </p>
      ) : null}
      <div className="tw-done-row">
        {latest ? (
          <>
            <button type="button" className="link-btn" onClick={undo} data-testid="finish-undo">
              {t('finish.undo')}
            </button>
            {latest.finished_on !== localToday() && (
              <button type="button" className="link-btn" disabled={busy} onClick={() => void mark()} data-testid="finish-again">
                {t('finish.again')}
              </button>
            )}
          </>
        ) : (
          <button type="button" className="go ghost" disabled={busy} onClick={() => void mark()} data-testid="finish-mark">
            {t('finish.mark')}
          </button>
        )}
        {!picking && (
          <button type="button" className="link-btn" onClick={() => setPicking(true)} data-testid="finish-other">
            {t('finish.other')}
          </button>
        )}
      </div>
      {picking && (
        <form
          className="tw-day"
          onSubmit={(event) => {
            event.preventDefault()
            void mark(day)
          }}
        >
          <label>
            {t('finish.day')}
            <input type="date" value={day} max={localToday()} min="1900-01-01" onChange={(event) => setDay(event.target.value)} required data-testid="finish-date" />
          </label>
          <button type="submit" className="link-btn" disabled={busy || !day} data-testid="finish-date-ok">
            {t('finish.dayOk')}
          </button>
        </form>
      )}
      {problem && (
        <p role="alert" className="tw-problem" data-testid="finish-problem">
          {errorText(problem)}
        </p>
      )}
      {dialog}
    </div>
  )
}
