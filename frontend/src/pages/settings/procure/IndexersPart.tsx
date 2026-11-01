/**
 * Settings, Fetching, Indexers (the operator's): the Prowlarr of the instance and the indexers, a card for each with its
 * state, the day's count, the categories it is asked in, and the buttons to test, change, switch off and remove it. An
 * indexer is added with a test first: the test says what it can (the book search and its parameters) and offers the book
 * categories the indexer lists, the ones that fit already chosen.
 */
import { Check, Pause, Play, Plug, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type IndexerCategory, type IndexerForm, type IndexerInfo, type IndexerKind, type IndexerState, type ProwlarrInfo } from '../../../api/client'
import { Dialog } from '../../../components/Dialog'
import { useConfirm } from '../../../components/useConfirm'
import { ago } from '../../../lib/ago'
import { iso } from '../../../lib/say'
import { Button, Card, Feedback, Input, Segment, Toggle, useAction } from '../ui'
import { CardGrid, Chip, Code, ItemCard, Plaque, SectionLine, type Tone } from './bits'
import { clock, keyFollows, problemText, useLanguageNames } from './helpers'

/** While a sync with Prowlarr is queued or running the card looks again this often (milliseconds). */
const POLL_MS = 1500
/** The languages a category can be given: the codes of the server. */
const LANGUAGES = ['mixed', 'de', 'en', 'fr', 'es', 'it', 'nl', 'sv', 'no', 'pt', 'pl']

const TONE: Record<IndexerState, Tone> = { ready: 'ok', resting: 'warn', key_rejected: 'bad', off: 'muted' }

export function IndexersPart() {
  const { t } = useTranslation()
  const [indexers, setIndexers] = useState<IndexerInfo[] | null>(null)
  const [prowlarr, setProwlarr] = useState<ProwlarrInfo | null>(null)
  const [dialog, setDialog] = useState<{ edit: IndexerInfo | null } | null>(null)
  const load = useCallback(async () => {
    const [list, linked] = await Promise.all([procureApi.indexers(), procureApi.prowlarr()])
    setIndexers(list)
    setProwlarr(linked)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
  }, [load])
  const syncing = prowlarr?.sync_state && prowlarr.sync_state !== 'idle'
  useEffect(() => {
    if (!syncing) return
    const timer = window.setInterval(() => void load().catch(() => undefined), POLL_MS)
    return () => window.clearInterval(timer)
  }, [syncing, load])
  if (!indexers || !prowlarr) return <p className="text-sm text-muted">{t('procure.loading')}</p>
  return (
    <div className="space-y-6">
      <ProwlarrCard info={prowlarr} onChange={load} />
      <SectionLine title={t('procure.indexer.heading')} text={indexers.length ? t('procure.indexer.count', { count: indexers.length }) : t('procure.indexer.none')} />
      <CardGrid>
        {indexers.map((indexer) => (
          <IndexerCard key={indexer.id} indexer={indexer} onEdit={() => setDialog({ edit: indexer })} onChange={load} />
        ))}
        <button type="button" data-testid="indexer-add" onClick={() => setDialog({ edit: null })} className="card flex min-h-40 flex-col items-center justify-center gap-1 border-dashed p-4 text-center text-ink-2 hover:bg-sheet-2">
          <span className="mb-1 grid h-10 w-10 place-items-center rounded-full bg-accent-soft text-accent">
            <Plus size={20} aria-hidden />
          </span>
          <b className="font-display">{t('procure.indexer.add')}</b>
          <small className="text-muted">{t('procure.indexer.addHint')}</small>
        </button>
      </CardGrid>
      {indexers.length === 0 && <p className="text-sm text-muted italic">{t('procure.indexer.empty')}</p>}
      {dialog && (
        <IndexerDialog
          indexer={dialog.edit}
          nextPriority={indexers.length + 1}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null)
            void load()
          }}
        />
      )}
    </div>
  )
}

// ---- Prowlarr -----------------------------------------------------------------------------------------------------

function ProwlarrCard({ info, onChange }: { info: ProwlarrInfo; onChange: () => Promise<void> }) {
  const { t, i18n } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [editing, setEditing] = useState(false)
  const syncing = info.sync_state !== 'idle'
  const skipped = (info.counts.skipped_categories ?? 0) + (info.counts.skipped_disabled ?? 0) + (info.counts.skipped_unsupported ?? 0)
  const disconnect = () =>
    ask({
      title: t('procure.prowlarr.off.title'),
      text: t('procure.prowlarr.off.text'),
      confirm: t('procure.prowlarr.off.confirm'),
      run: async () => {
        await procureApi.disconnectProwlarr()
        await onChange()
      },
    })
  return (
    <Card id="prowlarr" icon={Plug} title="Prowlarr" text={t('procure.prowlarr.text')}>
      {info.connected ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <Plaque tone={info.last_error ? 'bad' : 'ok'} testid="prowlarr-state">
              {info.last_error ? t('procure.prowlarr.failed') : t('procure.prowlarr.connected')}
            </Plaque>
            <Code testid="prowlarr-url">{info.url}</Code>
          </div>
          <p className="text-sm text-ink-2" data-testid="prowlarr-line">
            <b>{t('procure.prowlarr.taken', { count: info.taken })}</b>
            {skipped > 0 && ` · ${t('procure.prowlarr.skipped', { count: skipped })}`}
            {(info.counts.skipped_limit ?? 0) > 0 && ` · ${t('procure.prowlarr.skippedLimit', { count: info.counts.skipped_limit })}`}
            {info.last_sync_at && ` · ${t('procure.prowlarr.synced', { when: ago(info.last_sync_at, i18n.language, t('libraries.justNow')) })}`}
            {syncing && ` · ${t('procure.prowlarr.syncing')}`}
          </p>
          {info.last_error && (
            <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="prowlarr-error">
              {t('procure.prowlarr.lastFailed', { why: problemText(new ApiError(0, info.last_error)) })}
            </p>
          )}
          <Toggle
            label={t('procure.prowlarr.autoAdd')}
            hint={t('procure.prowlarr.autoAddHint')}
            checked={info.auto_add}
            disabled={busy}
            onChange={(auto_add) => void run(async () => {
              await procureApi.saveProwlarr({ url: info.url, auto_add })
              await onChange()
            })}
          />
          <div className="flex flex-wrap gap-2">
            <Button small busy={busy || syncing} onClick={() => void run(async () => {
              await procureApi.syncProwlarr()
              await onChange()
            })}>
              <RefreshCw size={14} aria-hidden /> {t('procure.prowlarr.sync')}
            </Button>
            <Button small onClick={() => setEditing(true)}>
              {t('procure.edit')}
            </Button>
            <Button small danger onClick={disconnect}>
              {t('procure.prowlarr.disconnect')}
            </Button>
          </div>
        </>
      ) : (
        <>
          <Plaque tone="muted" testid="prowlarr-state">
            {t('procure.prowlarr.notConnected')}
          </Plaque>
          <p className="text-sm text-ink-2">{t('procure.prowlarr.hint')}</p>
          <div>
            <Button small primary onClick={() => setEditing(true)}>
              {t('procure.prowlarr.connect')}
            </Button>
          </div>
        </>
      )}
      <Feedback problem={problem} done={done} values={values} />
      {editing && (
        <ProwlarrDialog
          info={info}
          onClose={() => setEditing(false)}
          onDone={() => {
            setEditing(false)
            void onChange()
          }}
        />
      )}
      {dialog}
    </Card>
  )
}

function ProwlarrDialog({ info, onClose, onDone }: { info: ProwlarrInfo; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState(info.url)
  const [key, setKey] = useState('')
  const [message, setMessage] = useState<{ good: boolean; text: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const { busy, problem, values, run } = useAction()
  // The saved key is sent only to the place it was saved for: another address needs the key again.
  const needKey = info.connected && !key && !keyFollows(info.url, url.trim())
  const test = async () => {
    if (!url.trim()) return setMessage({ good: false, text: t('procure.prowlarr.dialog.needAddress') })
    if (needKey) return setMessage({ good: false, text: t('procure.keyAgain') })
    setTesting(true)
    setMessage(null)
    try {
      const found = await procureApi.testProwlarr({ url: url.trim(), api_key: key || undefined })
      setMessage({ good: true, text: t('procure.prowlarr.dialog.ok', { indexers: found.indexers, version: found.version, books: found.with_books }) })
    } catch (error) {
      setMessage({ good: false, text: problemText(error) })
    } finally {
      setTesting(false)
    }
  }
  return (
    <Dialog title="Prowlarr" broad onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (needKey) return setMessage({ good: false, text: t('procure.keyAgain') })
          void run(async () => {
            await procureApi.saveProwlarr({ url: url.trim(), api_key: key || undefined, auto_add: info.connected ? info.auto_add : true })
            onDone()
          })
        }}
      >
        <Input label={t('procure.address')} value={url} onChange={setUrl} placeholder="http://prowlarr.example.com:9696" mono />
        <Input label={t('procure.apiKey')} type="password" value={key} onChange={setKey} placeholder={info.connected && !needKey ? t('procure.keyKept') : t('procure.prowlarr.dialog.keyPlaceholder')} hint={needKey ? t('procure.keyAgain') : undefined} mono />
        <div className="flex flex-wrap items-center gap-3">
          <Button small onClick={() => void test()} busy={testing}>
            <Plug size={14} aria-hidden /> {t('procure.test')}
          </Button>
          {message && (
            <span role="status" data-testid="prowlarr-test" className={`text-sm ${message.good ? 'font-semibold text-ok' : 'text-bad'}`}>
              {message.good && <Check size={14} className="mr-1 inline" aria-hidden />}
              {message.text}
            </span>
          )}
        </div>
        <Feedback problem={problem} values={values} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="submit" primary busy={busy}>
            {t('common.save')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** What the indexer itself states as its limits for the day (`apilimits` in its answers): searches and grabs, counted apart. */
function Reported({ indexer, testid }: { indexer: Pick<IndexerInfo, 'api_max' | 'grab_max'>; testid: string }) {
  const { t, i18n } = useTranslation()
  const number = (value: number) => value.toLocaleString(i18n.language)
  const searches = indexer.api_max
  const grabs = indexer.grab_max
  if (searches === null && grabs === null) return null
  const key = searches !== null && grabs !== null ? 'both' : searches !== null ? 'searches' : 'grabs'
  return (
    <p className="text-xs text-muted" data-testid={testid}>
      {t(`procure.indexer.reported.${key}`, { searches: number(searches ?? 0), grabs: number(grabs ?? 0) })}
    </p>
  )
}

/** Whether an own limit of the day is below what the indexer allows for searches: the own one then stops earlier than needed. */
function belowReported(own: number, indexer: Pick<IndexerInfo, 'api_max'>): boolean {
  return own > 0 && indexer.api_max !== null && own < indexer.api_max
}

// ---- An indexer ----------------------------------------------------------------------------------------------------

function IndexerCard({ indexer, onEdit, onChange }: { indexer: IndexerInfo; onEdit: () => void; onChange: () => Promise<void> }) {
  const { t, i18n } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [testing, setTesting] = useState(false)
  const name = useLanguageNames()
  const rest = indexer.resting_until ? clock(indexer.resting_until, i18n.language) : ''
  const percent = indexer.daily_limit ? Math.min(100, (indexer.used_today / indexer.daily_limit) * 100) : 0
  const on = indexer.categories.filter((category) => category.on)
  const test = async () => {
    setTesting(true)
    try {
      const found = await procureApi.testIndexer(indexer.id, i18n.language)
      await run(async () => undefined, t('procure.indexer.tested', { name: iso(indexer.name), count: found.category_count }))
    } catch (error) {
      await run(async () => {
        throw error
      })
    } finally {
      setTesting(false)
      await onChange()
    }
  }
  const remove = () =>
    ask({
      title: t('procure.indexer.remove.title', { name: iso(indexer.name) }),
      text: t('procure.indexer.remove.text'),
      confirm: t('procure.indexer.remove.confirm'),
      run: async () => {
        await procureApi.removeIndexer(indexer.id)
        await onChange()
      },
    })
  return (
    <ItemCard dim={indexer.state === 'off'} testid={`indexer-${indexer.id}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold [overflow-wrap:anywhere]">{iso(indexer.name)}</h3>
          <p className="mt-1 flex flex-wrap gap-1.5">
            <Chip>{indexer.kind === 'newznab' ? 'Newznab' : 'Torznab'}</Chip>
            {indexer.via_prowlarr && <Chip>{t('procure.indexer.viaProwlarr')}</Chip>}
          </p>
        </div>
        <Plaque tone={TONE[indexer.state]} testid={`indexer-state-${indexer.id}`}>
          {indexer.state === 'resting' ? t('procure.indexer.state.resting', { time: rest }) : t(`procure.indexer.state.${indexer.state}`)}
        </Plaque>
      </div>
      <Code>{indexer.url}</Code>
      {indexer.state === 'resting' && (
        <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn" data-testid={`indexer-note-${indexer.id}`}>
          {t(`procure.indexer.cause.${indexer.resting_cause || 'request_limit'}`)} {t('procure.indexer.restUntil', { time: rest })}
        </p>
      )}
      {indexer.state === 'key_rejected' && <p className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{t('procure.indexer.keyRejected')}</p>}
      <div>
        <div className="h-1.5 overflow-hidden rounded-full bg-ink/10" aria-hidden>
          <i className={`block h-full rounded-full ${indexer.state === 'resting' ? 'bg-warn' : 'bg-accent'}`} style={{ width: `${percent}%` }} />
        </div>
        <p className="mt-1 text-xs text-muted" data-testid={`indexer-use-${indexer.id}`}>
          {indexer.daily_limit ? t('procure.indexer.useLimit', { used: indexer.used_today, limit: indexer.daily_limit }) : t('procure.indexer.useFree', { used: indexer.used_today })} · {t('procure.indexer.priority', { n: indexer.priority })}
        </p>
        <Reported indexer={indexer} testid={`indexer-reported-${indexer.id}`} />
      </div>
      {belowReported(indexer.daily_limit, indexer) && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn" data-testid={`indexer-below-${indexer.id}`}>
          <span className="min-w-0 flex-1 basis-56">{t('procure.indexer.below.text', { own: indexer.daily_limit.toLocaleString(i18n.language), max: (indexer.api_max ?? 0).toLocaleString(i18n.language) })}</span>
          <Button
            small
            onClick={() => void run(async () => {
              await procureApi.changeIndexer(indexer.id, { daily_limit: 0 })
              await onChange()
            }, t('procure.indexer.below.done'))}
          >
            {t('procure.indexer.below.take')}
          </Button>
        </div>
      )}
      <p className="flex flex-wrap gap-1.5">
        {on.map((category) => (
          <Chip key={category.id} title={category.asked === false ? t('procure.indexer.notAsked', { language: name(category.lang) }) : name(category.lang)}>
            <span className={category.asked === false ? 'line-through opacity-60' : undefined} data-asked={category.asked === false ? '0' : '1'}>
              {category.name || t('procure.indexer.category', { id: category.id })} <small className="text-muted">{category.id}</small>
            </span>
          </Chip>
        ))}
      </p>
      {on.some((category) => category.asked === false) && (
        <p className="text-xs text-warn" data-testid={`indexer-not-asked-${indexer.id}`}>
          {t('procure.indexer.notAskedCard', { count: on.filter((category) => category.asked === false).length })}
        </p>
      )}
      {(indexer.proposals ?? 0) > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn" data-testid={`indexer-proposals-${indexer.id}`}>
          <span className="min-w-0 flex-1">{t('procure.indexer.proposals.text', { count: indexer.proposals ?? 0 })}</span>
          <Button
            small
            onClick={() => void run(async () => {
              await procureApi.takeIndexerProposals(indexer.id)
              await onChange()
            }, t('procure.indexer.proposals.done'))}
          >
            {t('procure.indexer.proposals.take')}
          </Button>
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2">
        <Button small busy={testing} onClick={() => void test()}>
          {t('procure.test')}
        </Button>
        <Button small onClick={onEdit}>
          {indexer.state === 'key_rejected' ? t('procure.indexer.changeKey') : t('procure.edit')}
        </Button>
        <Button
          small
          label={indexer.enabled ? t('procure.indexer.switchOff') : t('procure.indexer.switchOn')}
          className="w-8 !px-0"
          onClick={() => void run(async () => {
            await procureApi.changeIndexer(indexer.id, { enabled: !indexer.enabled })
            await onChange()
          })}
        >
          {indexer.enabled ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}
        </Button>
        <Button small label={indexer.via_prowlarr ? t('procure.indexer.fromProwlarr') : t('procure.indexer.removeLabel')} className="w-8 !px-0" disabled={indexer.via_prowlarr || busy} onClick={remove}>
          <Trash2 size={14} aria-hidden />
        </Button>
      </div>
      <Feedback problem={problem} done={done} values={values} />
      {dialog}
    </ItemCard>
  )
}

function IndexerDialog({ indexer, nextPriority, onClose, onDone }: { indexer: IndexerInfo | null; nextPriority: number; onClose: () => void; onDone: () => void }) {
  const { t, i18n } = useTranslation()
  const name = useLanguageNames()
  const edit = indexer !== null
  const via = indexer?.via_prowlarr ?? false
  const [kind, setKind] = useState<IndexerKind>(indexer?.kind ?? 'newznab')
  const [label, setLabel] = useState(indexer?.name ?? '')
  const [url, setUrl] = useState(indexer?.url ?? '')
  const [key, setKey] = useState('')
  const [priority, setPriority] = useState(String(indexer?.priority ?? nextPriority))
  const [limit, setLimit] = useState(String(indexer?.daily_limit ?? 0))
  const [tested, setTested] = useState(edit)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<{ good: boolean; text: string } | null>(null)
  const [categories, setCategories] = useState<IndexerCategory[]>(indexer?.categories ?? [])
  // The languages books are fetched in: a category of another language is never asked (the server says it, the dialog shows it).
  const [languages, setLanguages] = useState<string[]>(indexer?.languages ?? [])
  const notAsked = (category: IndexerCategory) => languages.length > 0 && category.lang !== 'mixed' && !languages.includes(category.lang)
  const { busy, problem, values, run } = useAction()
  const [local, setLocal] = useState<string | null>(null)
  const connection = (change: () => void) => {
    change()
    setTested(false)
  }
  // The saved key is sent only to the place it was saved for: another address needs the key again.
  const needKey = edit && !via && !key && !keyFollows(indexer.url, url.trim())
  const test = async () => {
    if (!url.trim()) return setMessage({ good: false, text: t('procure.indexer.dialog.needAddress') })
    if (needKey) return setMessage({ good: false, text: t('procure.keyAgain') })
    setTesting(true)
    setMessage(null)
    try {
      const found = await procureApi.testAddress({ indexer_id: indexer?.id, kind, url: url.trim(), api_key: key || undefined, language: i18n.language })
      const params = found.book_search ? found.book_params.join(', ') : ''
      setMessage({
        good: true,
        text: found.book_search ? t('procure.indexer.test.ok', { params, count: found.category_count }) : t('procure.indexer.test.noBookSearch', { count: found.category_count }),
      })
      setTested(true)
      setLanguages(found.languages ?? [])
      setCategories((now) => {
        if (!edit) return found.categories
        const kept = new Map(now.map((category) => [category.id, category]))
        return found.categories.map((category) => ({ ...category, on: kept.get(category.id)?.on ?? false, lang: kept.get(category.id)?.lang ?? category.lang }))
      })
    } catch (error) {
      setMessage({ good: false, text: problemText(error) })
    } finally {
      setTesting(false)
    }
  }
  const toggle = (id: number, change: Partial<IndexerCategory>) => setCategories((now) => now.map((category) => (category.id === id ? { ...category, ...change } : category)))
  const save = () => {
    setLocal(null)
    if (!label.trim() || !url.trim()) return setLocal(t('procure.indexer.dialog.needNameAddress'))
    if (needKey) return setLocal(t('procure.keyAgain'))
    if (!tested) return setLocal(t('procure.indexer.dialog.needTest'))
    const chosen = categories.filter((category) => category.on)
    const form: IndexerForm = {
      name: label.trim(),
      kind,
      url: url.trim(),
      priority: Number(priority) || 1,
      daily_limit: Number(limit) || 0,
      categories: chosen.map((category) => category.id),
      category_langs: Object.fromEntries(chosen.map((category) => [String(category.id), category.lang])),
      language: i18n.language,
      ...(key ? { api_key: key } : {}),
    }
    void run(async () => {
      if (edit && indexer) {
        const { name: _name, kind: _kind, url: _url, language: _language, ...change } = form
        await procureApi.changeIndexer(indexer.id, via ? change : { name: form.name, kind: form.kind, url: form.url, ...change })
      } else {
        await procureApi.addIndexer(form)
      }
      onDone()
    })
  }
  const group = (type: 'ebook' | 'audio') => categories.filter((category) => category.type === type)
  return (
    <Dialog title={edit ? t('procure.indexer.dialog.editTitle', { name: iso(indexer.name) }) : t('procure.indexer.add')} broad onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {!via && (
            <div>
              <span className="text-sm font-semibold">{t('procure.indexer.dialog.kind')}</span>
              <div className="mt-1">
                <Segment<IndexerKind>
                  tint
                  label={t('procure.indexer.dialog.kind')}
                  value={kind}
                  options={[
                    { value: 'newznab', label: t('procure.indexer.dialog.newznab') },
                    { value: 'torznab', label: t('procure.indexer.dialog.torznab') },
                  ]}
                  onChange={(value) => connection(() => setKind(value))}
                />
              </div>
            </div>
          )}
          <Input label={t('procure.name')} value={label} onChange={setLabel} placeholder={t('procure.indexer.dialog.namePlaceholder')} autoFocus />
          <Input label={t('procure.address')} value={url} onChange={(value) => connection(() => setUrl(value))} placeholder="https://indexer.example.com/api" className="sm:col-span-2" mono />
          {!via && (
            <Input
              label={t('procure.apiKey')}
              type="password"
              value={key}
              onChange={(value) => connection(() => setKey(value))}
              placeholder={edit && !needKey && indexer?.state !== 'key_rejected' ? t('procure.keyKept') : t('procure.indexer.dialog.keyPlaceholder')}
              hint={needKey ? t('procure.keyAgain') : undefined}
              className="sm:col-span-2"
              mono
            />
          )}
          <Input label={t('procure.indexer.dialog.priority')} type="number" min={1} max={50} value={priority} onChange={setPriority} hint={t('procure.indexer.dialog.priorityHint')} />
          <Input label={t('procure.indexer.dialog.limit')} type="number" min={0} value={limit} onChange={setLimit} hint={t('procure.indexer.dialog.limitHint')} />
        </div>
        {indexer && (indexer.api_max !== null || indexer.grab_max !== null) && (
          <div className="space-y-2" data-testid="indexer-dialog-limits">
            <Reported indexer={indexer} testid="indexer-dialog-reported" />
            {belowReported(Number(limit) || 0, indexer) && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn" data-testid="indexer-dialog-below">
                <span className="min-w-0 flex-1 basis-56">{t('procure.indexer.below.text', { own: (Number(limit) || 0).toLocaleString(i18n.language), max: (indexer.api_max ?? 0).toLocaleString(i18n.language) })}</span>
                <Button small onClick={() => setLimit('0')}>
                  {t('procure.indexer.below.take')}
                </Button>
              </div>
            )}
          </div>
        )}
        {!via && (
          <div className="flex flex-wrap items-center gap-3">
            <Button small onClick={() => void test()} busy={testing}>
              <Plug size={14} aria-hidden /> {t('procure.test')}
            </Button>
            <span role="status" data-testid="indexer-test" className={`text-sm ${message ? (message.good ? 'font-semibold text-ok' : 'text-bad') : 'text-muted'}`}>
              {message?.good && <Check size={14} className="mr-1 inline" aria-hidden />}
              {message ? message.text : edit ? '' : t('procure.indexer.dialog.testFirst')}
            </span>
          </div>
        )}
        {(tested || via) && categories.length > 0 && (
          <div data-testid="indexer-categories" className="space-y-3">
            <div>
              <h3 className="font-semibold">{t('procure.indexer.dialog.categories')}</h3>
              <p className="text-sm text-muted">{t('procure.indexer.dialog.categoriesHint')}</p>
            </div>
            {(['ebook', 'audio'] as const).map((type) =>
              group(type).length ? (
                <fieldset key={type} className="space-y-1.5 rounded-xl border border-line p-3">
                  <legend className="px-1 text-xs font-bold tracking-wide text-muted uppercase">{t(`procure.indexer.dialog.${type}`)}</legend>
                  {group(type).map((category) => (
                    <div key={category.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <label className="flex min-w-0 flex-1 items-center gap-2">
                        <input type="checkbox" checked={category.on} onChange={(event) => toggle(category.id, { on: event.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                        <span className="[overflow-wrap:anywhere]">{category.name || t('procure.indexer.category', { id: category.id })}</span>
                      </label>
                      <code className="text-xs text-muted">{category.id}</code>
                      <select
                        aria-label={t('procure.indexer.dialog.languageOf', { name: category.name || category.id })}
                        value={category.lang}
                        onChange={(event) => toggle(category.id, { lang: event.target.value })}
                        className="h-8 rounded-lg border border-line bg-sheet px-2 text-xs"
                      >
                        {[...new Set([...LANGUAGES, category.lang])].map((code) => (
                          <option key={code} value={code}>
                            {name(code)}
                          </option>
                        ))}
                      </select>
                      {category.suggested && <Chip>{t('procure.indexer.dialog.suggestion')}</Chip>}
                      {category.proposed && category.proposed !== category.lang && (
                        <button type="button" className="text-xs text-warn underline" onClick={() => toggle(category.id, { lang: category.proposed })} data-testid={`category-proposed-${category.id}`}>
                          {t('procure.indexer.proposals.one', { language: name(category.proposed) })}
                        </button>
                      )}
                      {notAsked(category) && (
                        <span className="text-xs text-warn" data-testid={`category-not-asked-${category.id}`}>
                          {t('procure.indexer.notAsked', { language: name(category.lang) })}
                        </span>
                      )}
                    </div>
                  ))}
                </fieldset>
              ) : null,
            )}
          </div>
        )}
        {local && (
          <p role="alert" className="text-sm text-bad" data-testid="indexer-local-problem">
            {local}
          </p>
        )}
        <Feedback problem={problem} values={values} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="submit" primary busy={busy}>
            {edit ? t('common.save') : t('procure.indexer.dialog.create')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
