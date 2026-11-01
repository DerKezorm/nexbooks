/**
 * The first time the operator sets up fetching (and for an instance that never chose): what nexbooks brings and what it
 * does not, and the one question about Audible. Audible is off from the start; the answer is saved as the switch of the
 * source, so the question is asked once. Closing the window without an answer leaves Audible off and asks again at the
 * next visit of the settings.
 */
import { Headphones, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, sourcesApi, type SourcesOut } from '../../api/client'
import { errorText } from '../../lib/errors'
import { Dialog } from '../Dialog'

export function StartDialog({ onClose, onAnswer }: { onClose: () => void; onAnswer: (next: SourcesOut) => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const answer = async (audible: boolean) => {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      onAnswer(await sourcesApi.save({ sources: { audible } }))
    } catch (error) {
      setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : undefined))
      setBusy(false)
    }
  }
  const button = 'inline-flex h-10 items-center justify-center rounded-full px-4 text-sm font-semibold transition disabled:opacity-50'
  return (
    <Dialog title={t('procure.start.title')} onClose={onClose}>
      <div className="space-y-4 text-sm text-ink-2" data-testid="start-dialog">
        <p className="flex items-start gap-2 rounded-xl border border-line bg-sheet-2/50 px-3 py-2" data-testid="start-notice">
          <ShieldCheck size={16} className="mt-0.5 shrink-0 text-accent" aria-hidden />
          <span>{t('procure.start.notice')}</span>
        </p>
        <div>
          <h3 className="flex items-center gap-2 font-display text-base font-semibold text-ink">
            <Headphones size={16} aria-hidden /> {t('procure.start.audibleTitle')}
          </h3>
          <p className="mt-1">{t('procure.start.audible')}</p>
        </div>
        {problem && (
          <p role="alert" className="text-bad">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" data-autofocus="" disabled={busy} onClick={() => void answer(false)} className={`${button} border border-line text-ink-2 hover:bg-sheet-2`}>
            {t('procure.start.no')}
          </button>
          <button type="button" disabled={busy} onClick={() => void answer(true)} className={`${button} bg-accent text-accent-ink hover:brightness-105`}>
            {t('procure.start.yes')}
          </button>
        </div>
        <p className="text-xs text-muted">{t('procure.start.foot')}</p>
      </div>
    </Dialog>
  )
}

/** The question, where the operator sets up fetching or the sources: shown while Audible has not been chosen about. */
export function StartGate({ onAnswered }: { onAnswered?: (next: SourcesOut) => void }) {
  const [state, setState] = useState<SourcesOut | null>(null)
  const [shut, setShut] = useState(false)
  useEffect(() => {
    let alive = true
    sourcesApi.state().then(
      (next) => alive && setState(next),
      () => undefined,
    )
    return () => {
      alive = false
    }
  }, [])
  if (!state || state.audible_decided || shut) return null
  return (
    <StartDialog
      onClose={() => setShut(true)}
      onAnswer={(next) => {
        setState(next)
        onAnswered?.(next)
      }}
    />
  )
}
