/**
 * Settings, Fetching, Formats and places: the trash. What an upgrade replaced lies in a hidden folder of its
 * library for the days the operator sets here, then it goes for good. The entries of the libraries the person looks after
 * are listed with their titles; one comes back where it lay, or goes now; the trash is emptied after a question.
 */
import { RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type TrashEntry } from '../../../api/client'
import { useConfirm } from '../../../components/useConfirm'
import { formatBytes } from '../../../lib/bytes'
import { shortDate } from '../../../lib/dates'
import { errorText } from '../../../lib/errors'
import { iso } from '../../../lib/say'
import { Card } from '../ui'

export function TrashCard({ days, onDays }: { days: number; onDays: (days: number) => Promise<boolean> }) {
  const { t, i18n } = useTranslation()
  const [entries, setEntries] = useState<TrashEntry[] | null>(null)
  const [text, setText] = useState(String(days))
  const [problem, setProblem] = useState<string | null>(null)
  const confirm = useConfirm()
  const load = async () => {
    try {
      setEntries((await procureApi.trash()).entries)
    } catch (error) {
      setProblem(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
    }
  }
  useEffect(() => {
    void load()
  }, [])
  useEffect(() => setText(String(days)), [days])
  const save = () => {
    const value = Number(text)
    if (Number.isInteger(value) && value >= 1 && value <= 365 && value !== days) void onDays(value)
    else setText(String(days))
  }
  const act = async (work: () => Promise<unknown>) => {
    setProblem(null)
    try {
      await work()
    } catch (error) {
      setProblem(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
    }
    await load()
  }
  const fmt = (code: string) => t(`procure.formats.name.${code}`, { defaultValue: code.toUpperCase() })
  const restore = (entry: TrashEntry) =>
    confirm.ask({
      title: t('procure.trash.restore.title'),
      text: t('procure.trash.restore.text', { title: entry.title, format: fmt(entry.format) }),
      confirm: t('procure.trash.restore.confirm'),
      danger: false,
      run: () => act(() => procureApi.restoreTrash(entry.id)),
    })
  const remove = (entry: TrashEntry) =>
    confirm.ask({
      title: t('procure.trash.delete.title'),
      text: t('procure.trash.delete.text', { title: entry.title, format: fmt(entry.format) }),
      confirm: t('procure.trash.delete.confirm'),
      run: () => act(() => procureApi.deleteTrash(entry.id)),
    })
  const empty = () =>
    confirm.ask({
      title: t('procure.trash.empty.title'),
      text: t('procure.trash.empty.text', { count: entries?.length ?? 0 }),
      confirm: t('procure.trash.empty.confirm'),
      run: () => act(() => procureApi.emptyTrash()),
    })
  const size = (entries ?? []).reduce((sum, entry) => sum + entry.size, 0)
  return (
    <Card id="formats-trash" icon={Trash2} title={t('procure.trash.title')} text={t('procure.trash.text')}>
      <label className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-semibold">{t('procure.trash.days')}</span>
        <input
          type="number"
          min={1}
          max={365}
          inputMode="numeric"
          className="h-10 w-24 rounded-xl border border-line bg-sheet px-3 text-ink"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => event.key === 'Enter' && save()}
          data-testid="trash-days"
        />
        <span className="text-muted">{t('procure.trash.daysUnit')}</span>
      </label>
      {problem && (
        <p role="alert" className="text-sm text-bad">
          {problem}
        </p>
      )}
      {entries && entries.length === 0 && <p className="text-sm text-muted" data-testid="trash-empty">{t('procure.trash.none')}</p>}
      {entries && entries.length > 0 && (
        <>
          <p className="text-sm text-ink-2">{t('procure.trash.count', { count: entries.length, size: formatBytes(size, i18n.language) })}</p>
          <ul className="divide-y divide-line rounded-lg border border-line" data-testid="trash-list">
            {entries.map((entry) => (
              <li key={entry.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm" data-trash={entry.id}>
                <div className="min-w-0 flex-1">
                  <b dir="auto">{entry.title || t('acquire.history.unknownBook')}</b>
                  <span className="text-muted" dir="auto">
                    {entry.author ? ` · ${entry.author}` : ''} · {fmt(entry.format)} · {formatBytes(entry.size, i18n.language)}
                  </span>
                  <small className="block text-muted">
                    {iso(entry.library?.name ?? '')} · {t('procure.trash.until', { day: shortDate(entry.expires_at, i18n.language) })}
                  </small>
                </div>
                <button type="button" className="inline-flex items-center justify-center gap-2 rounded-full font-semibold transition disabled:opacity-50 h-8 px-3.5 text-xs border border-line text-ink-2 hover:bg-sheet-2" onClick={() => restore(entry)} data-act="trash-restore">
                  <RotateCcw aria-hidden className="h-4 w-4" />
                  {t('procure.trash.restore.button')}
                </button>
                <button type="button" className="inline-flex items-center justify-center gap-2 rounded-full font-semibold transition disabled:opacity-50 h-8 px-3.5 text-xs border border-bad/40 text-bad hover:bg-bad/10" onClick={() => remove(entry)} data-act="trash-delete">
                  {t('procure.trash.delete.button')}
                </button>
              </li>
            ))}
          </ul>
          <div>
            <button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-full border border-bad/40 px-4 text-sm font-semibold text-bad transition hover:bg-bad/10" onClick={empty} data-act="trash-empty">
              {t('procure.trash.empty.button')}
            </button>
          </div>
        </>
      )}
      {confirm.dialog}
    </Card>
  )
}
