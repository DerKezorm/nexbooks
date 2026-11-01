/**
 * Settings, Fetching, Automation (the operator's): the main switch with when the automatic work last ran and comes back,
 * how often the newest entries of the indexers are read, how long a wish that found nothing waits, the limit of requests a
 * day for each indexer (what was used today, what was fetched, what the indexer says of itself), a replacement after a
 * failure, how long a torrent is seeded, and what the automatic work did last. Saved at once.
 */
import { Clock, Download, History, RefreshCw, Search, ShieldCheck, Zap } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { procureApi, type AutoRun, type AutomationConfig, type AutomationStatus, type IndexerInfo } from '../../../api/client'
import { ago, inTime, timeIn, whenText } from '../../../lib/ago'
import { errorText } from '../../../lib/errors'
import { iso } from '../../../lib/say'
import { Card, Feedback, Segment, Toggle, useAction } from '../ui'

export function AutomationPart() {
  const { t, i18n } = useTranslation()
  const [config, setConfig] = useState<AutomationConfig | null>(null)
  const [indexers, setIndexers] = useState<IndexerInfo[]>([])
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const { problem, values, done, run } = useAction()
  const load = useCallback(async () => {
    const [saved, list, state] = await Promise.all([procureApi.automation(), procureApi.indexers(), procureApi.automationStatus(12)])
    setConfig(saved)
    setIndexers(list)
    setStatus(state)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
    // The page shows when the automatic work last ran and comes back: it looks again now and then.
    const timer = window.setInterval(() => void procureApi.automationStatus(12).then(setStatus, () => undefined), 30_000)
    return () => window.clearInterval(timer)
  }, [load])
  if (!config) return <p className="text-sm text-muted">{t('procure.loading')}</p>
  const off = !config.enabled
  const save = async (next: AutomationConfig) => {
    const before = config
    setConfig(next)
    const ok = await run(async () => setConfig(await procureApi.saveAutomation(next)), t('settings.saved'))
    if (!ok) setConfig(before)
  }
  const change = (part: Partial<AutomationConfig>) => void save({ ...config, ...part })
  return (
    <div className="space-y-6">
      <div className="pt-2">
        <h2 className="font-display text-xl font-semibold">{t('procure.auto.heading')}</h2>
        <p className="text-sm text-ink-2">{t('procure.auto.text')}</p>
        <div className="mt-2">
          <Feedback problem={problem} done={done} values={values} />
        </div>
      </div>
      <Card id="auto-main" icon={Zap} title={t('procure.auto.main.title')} text={t('procure.auto.main.text')}>
        <Toggle label={t('procure.auto.main.switch')} hint={config.enabled ? onText(t, i18n.language, status) : t('procure.auto.main.off')} checked={config.enabled} onChange={(enabled) => change({ enabled })} />
      </Card>
      <div className={`space-y-6 ${off ? 'opacity-60' : ''}`} data-testid="auto-rest" aria-disabled={off}>
        <Card id="auto-rss" icon={RefreshCw} title={t('procure.auto.rss.title')} text={t('procure.auto.rss.text')}>
          <div className="text-sm">
            <span className="font-semibold">{t('procure.auto.rss.label')}</span>
            <div className="mt-1">
              <Segment tint label={t('procure.auto.rss.aria')} value={String(config.rss_minutes)} options={[5, 15, 30, 60].map((minutes) => ({ value: String(minutes), label: t('procure.auto.minutes', { n: minutes }) }))} onChange={(value) => change({ rss_minutes: Number(value) as AutomationConfig['rss_minutes'] })} />
            </div>
          </div>
          {status?.indexers.map((line) => (
            <p key={line.id} className="text-sm text-muted" data-testid={`reading-${line.id}`}>
              {readingText(t, i18n.language, line)}
            </p>
          ))}
        </Card>
        <Card id="auto-retry" icon={Search} title={t('procure.auto.retry.title')} text={t('procure.auto.retry.text')}>
          <div className="text-sm">
            <span className="font-semibold">{t('procure.auto.retry.label')}</span>
            <div className="mt-1">
              <Segment tint label={t('procure.auto.retry.aria')} value={String(config.retry_hours)} options={[1, 3, 6, 12, 24].map((hours) => ({ value: String(hours), label: t('procure.auto.hours', { n: hours }) }))} onChange={(value) => change({ retry_hours: Number(value) as AutomationConfig['retry_hours'] })} />
            </div>
          </div>
        </Card>
        <Card id="auto-limits" icon={ShieldCheck} title={t('procure.auto.limits.title')} text={t('procure.auto.limits.text')}>
          {indexers.length === 0 && <p className="text-sm text-muted">{t('procure.auto.limits.none')}</p>}
          {indexers.map((indexer) => (
            <LimitRow key={indexer.id} indexer={indexer} onDone={load} />
          ))}
          {indexers.length > 0 && status && <p className="text-sm text-muted">{t('procure.auto.limits.reset', { time: timeIn(status.day_ends_at, i18n.language, status.zone) })}</p>}
        </Card>
        <Card id="auto-replace" icon={Download} title={t('procure.auto.replace.title')} text={t('procure.auto.replace.text')}>
          <Toggle label={t('procure.auto.replace.switch')} hint={t('procure.auto.replace.hint')} checked={config.replace} onChange={(replace) => change({ replace })} />
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{t('procure.auto.replace.atMost')}</span>
            <input type="number" min={1} max={10} disabled={!config.replace} value={config.replace_max} onChange={(event) => change({ replace_max: Math.max(1, Math.min(10, Number(event.target.value) || 1)) })} className="h-10 w-20 rounded-xl border border-line bg-sheet px-3 text-ink disabled:opacity-50" />
            <span>{t('procure.auto.replace.tries')}</span>
          </label>
        </Card>
        <Card id="auto-seed" icon={Download} title={t('procure.auto.seed.title')} text={t('procure.auto.seed.text')}>
          <label className="block text-sm">
            <span className="font-semibold">{t('procure.auto.seed.label')}</span>
            <select value={config.seed} onChange={(event) => change({ seed: event.target.value as AutomationConfig['seed'] })} className="mt-1 block h-10 w-full max-w-xs rounded-xl border border-line bg-sheet px-3 text-ink">
              <option value="ratio">{t('procure.auto.seed.ratio')}</option>
              <option value="7d">{t('procure.auto.seed.days')}</option>
              <option value="no">{t('procure.auto.seed.no')}</option>
            </select>
          </label>
          <p className="text-sm text-muted" data-testid="seed-hint">{t('procure.auto.seed.hint')}</p>
        </Card>
        <Card id="auto-log" icon={History} title={t('procure.auto.log.title')} text={t('procure.auto.log.text')}>
          {status && status.runs.length === 0 && <p className="text-sm text-muted">{t('procure.auto.log.empty')}</p>}
          <ul className="space-y-2" data-testid="auto-log">
            {status?.runs.map((entry) => (
              <li key={entry.id} className="rounded-xl border border-line bg-sheet-2/50 px-4 py-3 text-sm" data-run={entry.kind}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <b>{t(`procure.auto.log.kind.${entry.kind}`)}</b>
                  <small className="text-muted">{ago(entry.finished_at ?? entry.started_at, i18n.language, t('acquire.justNow'))}</small>
                </div>
                <p className="text-ink-2">{runNumbers(t, entry)}</p>
                {entry.indexers.map((line) => (
                  <small key={line.id} className="block text-muted [overflow-wrap:anywhere]">
                    {line.name ? iso(line.name) : t('procure.auto.log.gone')}: {t('procure.auto.log.requests', { count: line.requests })}, {t('procure.auto.log.entries', { count: line.entries })}
                    {line.error ? `, ${errorText(line.error)}` : ''}
                  </small>
                ))}
                {entry.error && <small className="block text-bad">{errorText(entry.error)}</small>}
              </li>
            ))}
          </ul>
        </Card>
        <Card id="auto-retention" icon={Clock} title={t('procure.auto.retention.title')} text={t('procure.auto.retention.text')}>
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{t('procure.auto.retention.label')}</span>
            <input type="number" min={0} max={20000} value={config.retention_days} onChange={(event) => change({ retention_days: Math.max(0, Math.min(20000, Math.floor(Number(event.target.value) || 0))) })} className="h-10 w-28 rounded-xl border border-line bg-sheet px-3 text-ink" />
          </label>
          <p className="text-sm text-muted">{t('procure.auto.retention.hint')}</p>
          <p className="text-sm text-muted" data-testid="retention-read">{config.retention_read ? t('procure.auto.retention.read', { days: config.retention_read }) : t('procure.auto.retention.readNone')}</p>
        </Card>
      </div>
    </div>
  )
}

/** The limit of requests a day of one indexer: typed, saved when the field is left. */
function LimitRow({ indexer, onDone }: { indexer: IndexerInfo; onDone: () => Promise<void> }) {
  const { t } = useTranslation()
  const { problem, values, run } = useAction()
  const [value, setValue] = useState(String(indexer.daily_limit))
  useEffect(() => setValue(String(indexer.daily_limit)), [indexer.daily_limit])
  const save = (limit: number) => {
    setValue(String(limit))
    if (limit !== indexer.daily_limit) void run(async () => {
      await procureApi.changeIndexer(indexer.id, { daily_limit: limit })
      await onDone()
    })
  }
  const commit = () => save(Math.max(0, Math.floor(Number(value) || 0)))
  // What the indexer says of itself is a suggestion for a limit that is not set yet.
  const suggestion = indexer.daily_limit === 0 && indexer.api_max ? indexer.api_max : null
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm" data-testid={`limit-${indexer.id}`}>
      <b className="min-w-0 flex-1 [overflow-wrap:anywhere]">{iso(indexer.name)}</b>
      <small className="text-muted">{indexer.daily_limit ? t('procure.auto.limits.today', { used: indexer.used_today, limit: indexer.daily_limit }) : t('procure.auto.limits.todayFree', { used: indexer.used_today })}</small>
      <small className="text-muted" data-testid={`grabs-${indexer.id}`}>{t('procure.auto.limits.downloads', { count: indexer.grabs_today })}</small>
      <label className="flex items-center gap-2">
        <input type="number" min={0} value={value} onChange={(event) => setValue(event.target.value)} onBlur={commit} aria-label={t('procure.auto.limits.label', { name: indexer.name })} className="h-9 w-24 rounded-xl border border-line bg-sheet px-3 text-ink" />
        {t('procure.auto.limits.perDay')}
      </label>
      {suggestion && (
        <p className="basis-full text-muted" data-testid={`suggest-${indexer.id}`}>
          {t('procure.auto.limits.suggest', { max: suggestion })}{' '}
          <button type="button" className="font-semibold text-accent underline" onClick={() => save(suggestion)}>
            {t('procure.auto.limits.take')}
          </button>
        </p>
      )}
      <Feedback problem={problem} values={values} />
    </div>
  )
}

type Say = (key: string, values?: Record<string, unknown>) => string

/** The line under the main switch: when the automatic work last did something and comes back. */
function onText(t: Say, language: string, status: AutomationStatus | null): string {
  if (!status || (!status.last_run_at && !status.next_run_at)) return t('procure.auto.main.on')
  const last = status.last_run_at ? ago(status.last_run_at, language, t('acquire.justNow')) : ''
  const next = status.next_run_at ? inTime(status.next_run_at, language, t('procure.auto.main.now')) : ''
  if (last && next) return t('procure.auto.main.onRuns', { last, next })
  return last ? t('procure.auto.main.onLast', { last }) : t('procure.auto.main.onNext', { next })
}

/** What the last reading of one indexer's newest entries was, and when the next one is. */
function readingText(t: Say, language: string, line: AutomationStatus['indexers'][number]): string {
  const name = iso(line.name)
  if (line.resting_until) return t('procure.auto.rss.resting', { name, time: whenText(line.resting_until, language) })
  if (line.error === 'rss_share_used') return t('procure.auto.rss.share', { name })
  if (line.error) return t('procure.auto.rss.failed', { name, why: errorText(line.error) })
  if (!line.checked_at) return t('procure.auto.rss.never', { name })
  return t('procure.auto.rss.read', { name, last: ago(line.checked_at, language, t('acquire.justNow')), next: inTime(line.next_at, language, t('procure.auto.main.now')) })
}

/** The numbers of one round of the log, in a sentence. */
function runNumbers(t: Say, entry: AutoRun): string {
  const parts: string[] = []
  if (entry.kind === 'rss') {
    parts.push(t('procure.auto.log.entries', { count: entry.indexers.reduce((sum, line) => sum + line.entries, 0) }))
    if (entry.wishes) parts.push(t('procure.auto.log.compared', { count: entry.wishes }))
  } else if (entry.wishes) {
    parts.push(t('procure.auto.log.settled', { count: entry.wishes }))
  }
  if (entry.handovers) parts.push(t('procure.auto.log.handovers', { count: entry.handovers }))
  parts.push(t('procure.auto.log.loaded', { count: entry.loaded }))
  return parts.join(', ')
}
