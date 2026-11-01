/**
 * Settings, Tidying (the operator's): renaming and moving the books of a library by a scheme. Off from the start:
 * nexbooks reads the folders and changes nothing in them. With the switch the operator picks a library, writes a scheme
 * (`{Author}/{Series}/{Volume} - {Title} ({Year})`), sees a preview of every file that moves and everything that is in the way,
 * and, after a question, starts the run. A run goes on in the background (the card looks at it every moment) and can be
 * undone. The card says when the folder is mounted read-only (the libraries are, from the start) and what to do.
 *
 * Nothing is sent without the operator's yes: the run and its undoing each ask first and say whether it can be taken back.
 */
import { FolderTree } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, librariesApi, organizeApi, type LibraryAdmin, type OrganizeMove, type OrganizeRun, type OrganizeStatus } from '../../api/client'
import { useConfirm } from '../../components/useConfirm'
import { errorText } from '../../lib/errors'
import { listOf } from '../../lib/say'
import { Button, Card, Feedback, Input, Select, Toggle, useAction } from './ui'

/** While a run goes on the card looks at it this often (milliseconds). */
export const POLL_MS = 1500
const ACTIVE = ['queued', 'running', 'undoing']
const EXAMPLE = '{Author}/{Series}/{Volume} - {Title} ({Year})'

function Moves({ run, state, title }: { run: OrganizeRun; state: OrganizeMove['state']; title: string }) {
  const { t } = useTranslation()
  const [items, setItems] = useState<OrganizeMove[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const load = useCallback(
    async (offset: number) => {
      setBusy(true)
      try {
        const page = await organizeApi.moves(run.id, state, offset)
        const got = Array.isArray(page.items) ? page.items : []
        setItems((now) => (offset === 0 ? got : [...now, ...got]))
        setTotal(Number(page.total) || 0)
      } catch {
        // The list stays as it was; the next look tries again.
      } finally {
        setBusy(false)
      }
    },
    [run.id, state],
  )
  useEffect(() => {
    void load(0)
  }, [load, run.state, run.summary.digest])
  if (total === 0) return null
  return (
    <div data-testid={`org-${state}`}>
      <h3 className="pt-2 font-semibold">
        {title} <small className="text-muted">{total}</small>
      </h3>
      <ul className="org-moves">
        {items.map((move) => (
          <li key={move.id}>
            {move.title && <span className="title">{move.title}</span>}
            <em>{t('organize.from')}</em>
            <b dir="auto">{move.source}</b>
            <em>{t('organize.to')}</em>
            <b dir="auto">{move.target}</b>
            {move.reason && <span className="why">{t(`organize.reason.${move.reason}`, { defaultValue: move.reason })}</span>}
          </li>
        ))}
      </ul>
      {items.length < total && (
        <Button small onClick={() => void load(items.length)} busy={busy}>
          {t('organize.showMore')}
        </Button>
      )}
    </div>
  )
}

function RunPanel({ run, library, writable, onChanged }: { run: OrganizeRun; library: LibraryAdmin; writable: boolean; onChanged: () => void }) {
  const { t } = useTranslation()
  const { ask, dialog } = useConfirm()
  const { busy, problem, values, done, run: act } = useAction()
  const summary = run.summary
  const preview = run.state === 'preview'
  const active = ACTIVE.includes(run.state)
  const percent = run.total > 0 ? Math.min(100, Math.round((run.done / run.total) * 100)) : 0
  const startAsk = () =>
    ask({
      title: t('organize.startTitle'),
      text: t('organize.startText', { files: summary.move_files ?? run.total, library: library.name }),
      confirm: t('organize.startConfirm'),
      danger: false,
      run: async () => {
        await organizeApi.start(run.id)
        onChanged()
      },
    })
  const undoAsk = () =>
    ask({
      title: t('organize.undoTitle'),
      text: t('organize.undoText'),
      confirm: t('organize.undoConfirm'),
      danger: false,
      run: async () => {
        await organizeApi.undo(run.id)
        onChanged()
      },
    })
  const empty = Object.entries(summary.empty ?? {})
  return (
    <div className="space-y-3" data-testid="org-run" data-state={run.state}>
      {preview && (
        <>
          <div className="org-summary" data-testid="org-summary">
            {(summary.move_books ?? 0) > 0 && <span className="move">{t('organize.summary.move', { count: summary.move_books, files: summary.move_files })}</span>}
            {(summary.stay_books ?? 0) > 0 && <span>{t('organize.summary.stay', { count: summary.stay_books })}</span>}
            {(summary.problem_books ?? 0) > 0 && <span className="problem">{t('organize.summary.problem', { count: summary.problem_books })}</span>}
          </div>
          {run.total === 0 && (summary.problem_books ?? 0) === 0 && <p className="text-sm text-muted">{t('organize.summary.none')}</p>}
          {empty.map(([name, count]) => (
            <p key={name} className="text-sm text-muted">
              {t('organize.empty', { count, name })}
            </p>
          ))}
        </>
      )}
      {active && (
        <div data-testid="org-progress">
          <p className="mb-1 text-sm">{run.state === 'queued' ? t('organize.queued') : t(run.state === 'undoing' ? 'organize.undoing' : 'organize.running', { done: run.done, total: run.total })}</p>
          <div className="org-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <i style={{ width: `${percent}%` }} />
          </div>
        </div>
      )}
      {run.state === 'done' && <p className="text-sm font-semibold text-ok">{t('organize.done', { count: run.done })}</p>}
      {run.state === 'undone' && <p className="text-sm font-semibold text-ok">{t('organize.undone')}</p>}
      {run.state === 'failed' && (
        <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="org-failed">
          {t('organize.failed', { reason: t(`organize.fail.${run.error}`, { defaultValue: run.error }) })}
        </p>
      )}
      {!preview && !active && run.done > 0 && run.state !== 'undone' && (
        <>
          <Moves run={run} state="moved" title={t('organize.moves')} />
          <Moves run={run} state="skipped" title={t('organize.problems')} />
        </>
      )}
      {preview && (
        <>
          <Moves run={run} state="planned" title={t('organize.moves')} />
          <Moves run={run} state="problem" title={t('organize.problems')} />
        </>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        {preview && (
          <>
            <Button primary onClick={startAsk} disabled={run.total === 0 || !writable} busy={busy}>
              {t('organize.start')}
            </Button>
            <Button onClick={() => void act(async () => { await organizeApi.discard(run.id); onChanged() })} busy={busy}>
              {t('organize.discard')}
            </Button>
          </>
        )}
        {(run.state === 'done' || run.state === 'failed') && run.done > 0 && (
          <Button onClick={undoAsk} disabled={!writable} busy={busy}>
            {t('organize.undo')}
          </Button>
        )}
      </div>
      <Feedback problem={problem} done={done} values={values} />
      {dialog}
    </div>
  )
}

export function OrganizePart() {
  const { t } = useTranslation()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [libraries, setLibraries] = useState<LibraryAdmin[] | null>(null)
  // Libraries that exist but are not offered, because the operator is no member of them with the right to manage.
  const [kept, setKept] = useState(0)
  const [chosen, setChosen] = useState<number | null>(null)
  const [status, setStatus] = useState<OrganizeStatus | null>(null)
  const [scheme, setScheme] = useState(EXAMPLE)
  const [schemeProblem, setSchemeProblem] = useState<string | null>(null)
  const switching = useAction()
  const previewing = useAction()
  const timer = useRef<number | null>(null)

  useEffect(() => {
    organizeApi.enabled().then((found) => setEnabled(found.enabled), () => setEnabled(false))
    librariesApi.every().then(
      (found) => {
        // Only the libraries the operator manages as a member: a preview names their books (the server says 404 to others).
        const all = listOf<LibraryAdmin>(found)
        const list = all.filter((item) => item.my_right === 'manage')
        setKept(all.length - list.length)
        setLibraries(list)
        setChosen((now) => now ?? list[0]?.id ?? null)
      },
      () => setLibraries([]),
    )
  }, [])

  const load = useCallback(async () => {
    if (chosen === null || !enabled) return
    try {
      setStatus(await organizeApi.status(chosen))
    } catch {
      setStatus(null)
    }
  }, [chosen, enabled])
  useEffect(() => {
    setStatus(null)
    void load()
  }, [load])
  // The scheme the library was last tidied by stands in the field when the card opens.
  useEffect(() => {
    const last = status?.runs[0]
    if (last && !previewing.busy) setScheme((now) => (now === EXAMPLE ? last.scheme : now))
    // Once for each library: after that the field belongs to the operator.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen, status === null])

  const latest = status?.runs[0]
  const going = latest !== undefined && ACTIVE.includes(latest.state)
  useEffect(() => {
    if (!going) return
    timer.current = window.setInterval(() => void load(), POLL_MS)
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current)
    }
  }, [going, load])

  const library = libraries?.find((item) => item.id === chosen) ?? null
  const makePreview = async () => {
    if (chosen === null) return
    setSchemeProblem(null)
    await previewing.run(async () => {
      try {
        await organizeApi.preview(chosen, scheme)
      } catch (error) {
        if (error instanceof ApiError && error.code === 'scheme_invalid') {
          setSchemeProblem(t(`organize.schemeError.${String(error.values.reason ?? '')}`, { detail: String(error.values.detail ?? ''), defaultValue: errorText('scheme_invalid') }))
          return
        }
        throw error
      }
      await load()
    })
  }
  const writableText = status ? (status.writable ? t('organize.writable') : status.reason === 'read_only' ? t('organize.readOnly') : status.reason === 'missing' ? t('organize.missing') : t('organize.noPermission')) : ''
  return (
    <div className="space-y-6">
      <Card id="organize-main" icon={FolderTree} title={t('organize.title')} text={t('organize.text')}>
        <Toggle
          label={t('organize.switch')}
          hint={t('organize.switchHint')}
          checked={enabled === true}
          disabled={enabled === null || switching.busy}
          onChange={(next) => void switching.run(async () => setEnabled((await organizeApi.setEnabled(next)).enabled), t('settings.saved'))}
        />
        <Feedback problem={switching.problem} done={switching.done} values={switching.values} />
      </Card>
      {enabled && libraries !== null && libraries.length === 0 && <p className="empty">{t(kept > 0 ? 'organize.notManaged' : 'organize.noLibrary')}</p>}
      {enabled && libraries !== null && libraries.length > 0 && (
        <Card id="organize-run" icon={FolderTree} title={t('organize.library')}>
          <Select label={t('organize.library')} value={String(chosen ?? '')} options={libraries.map((item) => ({ value: String(item.id), label: item.name }))} onChange={(value) => setChosen(Number(value))} />
          {kept > 0 && (
            <p className="text-sm text-muted" data-testid="org-kept">
              {t('organize.notManaged')}
            </p>
          )}
          {status && (
            <p className={`text-sm ${status.writable ? 'text-muted' : 'text-warn'}`} data-testid="org-writable">
              {writableText}
            </p>
          )}
          <div className="org-scheme">
            <Input label={t('organize.scheme')} value={scheme} onChange={setScheme} hint={t('organize.schemeHint')} maxLength={300} />
            <div className="org-names">
              <span>{t('organize.names')}</span>
              {(status?.placeholders ?? []).map((item) => (
                <code key={item.name} title={item.aliases.join(', ')}>{`{${item.name}}`}</code>
              ))}
            </div>
            <p className="text-xs text-muted">{t('organize.example', { example: EXAMPLE })}</p>
          </div>
          {schemeProblem && (
            <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="org-scheme-problem">
              {schemeProblem}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button primary onClick={() => void makePreview()} busy={previewing.busy} disabled={going || !scheme.trim()}>
              {t('organize.preview')}
            </Button>
          </div>
          <Feedback problem={previewing.problem} done={previewing.done} values={previewing.values} />
          {latest && library && <RunPanel key={latest.id} run={latest} library={library} writable={status?.writable ?? false} onChanged={() => void load()} />}
          {status && status.runs.length > 1 && (
            <div data-testid="org-history">
              <h3 className="pt-2 font-semibold">{t('organize.history')}</h3>
              <ul className="space-y-1 text-sm text-muted">
                {status.runs.slice(1).map((run) => (
                  <li key={run.id}>
                    {new Date(run.created_at).toLocaleString()} · {t(`organize.runState.${run.state}`)} · <code>{run.scheme}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
