/**
 * Settings, Libraries (the operator's): the folders of books nexbooks reads, who sees each of them, the scan and its
 * progress, the list of files that could not be read. Making, changing and removing a library and setting who is in it
 * is the operator's alone. Removing asks first and says that the files stay where they are.
 */
import { AlertTriangle, BookOpen, ChevronDown, ChevronUp, Clock, Plus, Users } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api, librariesApi, type LibraryAdmin, type LibraryKind, type LibraryMember, type LibraryProblem, type LibraryRight, type Me } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { useConfirm } from '../../components/useConfirm'
import { ago } from '../../lib/ago'
import { formatBytes } from '../../lib/bytes'
import { iso, listOf } from '../../lib/say'
import { useServerSettings } from './ServerCards'
import { Button, Card, Feedback, Input, Segment, Select, Toggle, useAction } from './ui'

type Roots = { root: string; folders: string[] }[]
type Server = ReturnType<typeof useServerSettings>

/** While a scan is going the cards look again this often (milliseconds). */
const POLL_MS = 2000
const KINDS: LibraryKind[] = ['both', 'ebook', 'audio']
const INTERVALS = [0, 15, 60, 360, 1440] as const

export function LibrariesPart({ server }: { server: Server }) {
  const { t } = useTranslation()
  const [list, setList] = useState<LibraryAdmin[] | null>(null)
  const [roots, setRoots] = useState<Roots>([])
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => librariesApi.every().then((found) => setList(listOf<LibraryAdmin>(found)), () => setList((now) => now ?? [])), [])
  useEffect(() => {
    void load()
    librariesApi.roots().then((found) => setRoots(listOf<Roots[number]>(found.roots)), () => setRoots([]))
  }, [load])
  const busy = list?.some((library) => library.state !== 'idle') ?? false
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [busy, load])

  const rootText = roots.map((entry) => entry.root).join(', ')
  return (
    <div className="space-y-6">
      <Card id="libraries-scan" icon={Clock} title={t('libraries.scanTitle')} text={t('libraries.scanText')}>
        {server.settings && (
          <Select
            label={t('libraries.every')}
            value={String(server.settings.scan_minutes)}
            options={INTERVALS.map((minutes) => ({ value: String(minutes), label: t(`libraries.everyOptions.${minutes}`) }))}
            onChange={(value) => void server.save({ scan_minutes: Number(value) as 0 | 15 | 60 | 360 | 1440 }, t('settings.saved'))}
          />
        )}
        <Feedback problem={server.problem} done={server.done} />
      </Card>
      {list?.length === 0 && (
        <p className="text-sm text-muted" data-testid="libraries-empty">
          {roots.length === 0 ? t('libraries.noRoots') : t('libraries.empty')}
        </p>
      )}
      {list?.map((library) => <LibraryCard key={library.id} library={library} reload={load} roots={roots} />)}
      <div>
        <Button primary onClick={() => setAdding(true)} disabled={roots.length === 0}>
          <Plus size={16} /> {t('libraries.add')}
        </Button>
      </div>
      {adding && (
        <LibraryDialog
          roots={roots}
          rootText={rootText}
          onClose={() => setAdding(false)}
          onDone={() => {
            setAdding(false)
            void load()
          }}
        />
      )}
    </div>
  )
}

function countsLine(library: LibraryAdmin, t: (key: string, options?: Record<string, unknown>) => string, language: string): string {
  const parts = [
    library.counts.ebooks > 0 || library.kind !== 'audio' ? t('libraries.ebooks', { count: library.counts.ebooks }) : '',
    library.counts.audiobooks > 0 || library.kind !== 'ebook' ? t('libraries.audiobooks', { count: library.counts.audiobooks }) : '',
    t('libraries.files', { count: library.counts.files }),
    formatBytes(library.counts.bytes, language, 1000),
  ]
  return parts.filter(Boolean).join(' · ')
}

function LibraryCard({ library, reload, roots }: { library: LibraryAdmin; reload: () => Promise<void>; roots: Roots }) {
  const { t, i18n } = useTranslation()
  const { busy, problem, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [editing, setEditing] = useState(false)
  const [access, setAccess] = useState(false)
  const [showProblems, setShowProblems] = useState(false)
  const report = library.report
  const running = library.state === 'running'

  return (
    <Card id={`library-${library.id}`} icon={BookOpen} title={library.name} text={library.path}>
      <p className="text-sm text-ink-2" data-testid={`library-counts-${library.id}`}>
        {t(`libraries.kinds.${library.kind}`)} · {countsLine(library, t, i18n.language)}
      </p>

      {library.state !== 'idle' ? (
        <div className="space-y-1.5" data-testid={`library-state-${library.id}`}>
          <p className="text-sm font-semibold">
            {t(`libraries.state.${library.state}`)}
            {running && library.progress.total > 0 && <span className="font-normal text-muted"> · {t('libraries.progress', { done: library.progress.done, total: library.progress.total })}</span>}
          </p>
          {running && library.progress.total > 0 && (
            <div role="progressbar" aria-valuemin={0} aria-valuemax={library.progress.total} aria-valuenow={library.progress.done} className="h-2 overflow-hidden rounded-full bg-line">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.min(100, (100 * library.progress.done) / library.progress.total)}%` }} />
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted" data-testid={`library-last-${library.id}`}>
          {library.scanned_at ? t('libraries.lastRun', { when: ago(library.scanned_at, i18n.language, t('libraries.justNow')) }) : t('libraries.never')}
        </p>
      )}

      {report?.aborted && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn" data-testid={`library-aborted-${library.id}`}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          {t(`libraries.aborted.${report.aborted}`)}
        </p>
      )}
      {report && !report.aborted && library.state === 'idle' && (
        <p className="text-sm text-muted" data-testid={`library-report-${library.id}`}>
          {t('libraries.report', { added: report.added, changed: report.changed, missing: report.missing })}
          {report.deferred > 0 && ` ${t('libraries.deferred', { count: report.deferred })}`}
          {report.links > 0 && ` ${t('libraries.links', { count: report.links })}`}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid={`library-members-${library.id}`}>
        <Users size={15} className="text-muted" aria-hidden />
        {library.members.length === 0 ? (
          <span className="text-muted">{t('libraries.membersNone')}</span>
        ) : (
          library.members.map((member) => (
            <span key={member.user_id} className="rounded-full border border-line px-2.5 py-0.5 text-xs font-semibold text-ink-2">
              {iso(member.display_name || member.name)} · {t(`libraries.right.${member.right}`)}
            </span>
          ))
        )}
        <button type="button" onClick={() => setAccess(true)} className="text-xs font-semibold text-accent underline underline-offset-2">
          {t('libraries.membersEdit')}
        </button>
      </div>

      {library.counts.problems > 0 && (
        <div>
          <button type="button" aria-expanded={showProblems} onClick={() => setShowProblems(!showProblems)} className="inline-flex items-center gap-1.5 text-sm font-semibold text-warn">
            {showProblems ? <ChevronUp size={16} aria-hidden /> : <ChevronDown size={16} aria-hidden />}
            {showProblems ? t('libraries.problemsHide') : t('libraries.problemsShow', { count: library.counts.problems })}
          </button>
          {showProblems && <ProblemList library={library} />}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <Button primary busy={busy} disabled={library.state !== 'idle'} onClick={() => void run(async () => { await librariesApi.scan(library.id); await reload() })}>
          {t('libraries.scanNow')}
        </Button>
        <Button
          disabled={library.state !== 'idle'}
          onClick={() =>
            ask({
              title: t('libraries.rescanTitle', { name: library.name }),
              text: t('libraries.rescanText'),
              confirm: t('libraries.rescanConfirm'),
              run: async () => {
                await librariesApi.scan(library.id, true)
                await reload()
              },
            })
          }
        >
          {t('libraries.rescan')}
        </Button>
        <Button onClick={() => setEditing(true)}>{t('libraries.edit')}</Button>
        <Button
          danger
          onClick={() =>
            ask({
              title: t('libraries.removeTitle', { name: library.name }),
              text: t('libraries.removeText'),
              confirm: t('libraries.removeConfirm'),
              run: async () => {
                await librariesApi.remove(library.id)
                await reload()
              },
            })
          }
        >
          {t('libraries.remove')}
        </Button>
      </div>
      <Feedback problem={problem} />
      {dialog}
      {editing && (
        <LibraryDialog
          library={library}
          roots={roots}
          rootText=""
          onClose={() => setEditing(false)}
          onDone={() => {
            setEditing(false)
            void reload()
          }}
        />
      )}
      {access && (
        <MembersDialog
          library={library}
          onClose={() => setAccess(false)}
          onDone={() => {
            setAccess(false)
            void reload()
          }}
        />
      )}
    </Card>
  )
}

/** The files the scan could not read, with the reason in plain words, a page at a time. */
function ProblemList({ library }: { library: LibraryAdmin }) {
  const { t } = useTranslation()
  const [items, setItems] = useState<LibraryProblem[]>([])
  const [total, setTotal] = useState(0)
  const [failed, setFailed] = useState<string | null>(null)
  const more = useCallback(
    async (offset: number) => {
      try {
        const page = await librariesApi.problems(library.id, offset)
        setItems((now) => (offset === 0 ? page.items : [...now, ...page.items]))
        setTotal(page.total)
      } catch (error) {
        setFailed(error instanceof ApiError ? error.code : 'internal_error')
      }
    },
    [library.id],
  )
  useEffect(() => {
    void more(0)
  }, [more, library.scanned_at])
  return (
    <div className="mt-2 space-y-2" data-testid={`library-problems-${library.id}`}>
      {failed && <Feedback problem={failed} />}
      {!failed && items.length === 0 && <p className="text-sm text-muted">{t('libraries.problemsNone')}</p>}
      <ul className="divide-y divide-line rounded-xl border border-line text-sm">
        {items.map((item) => (
          <li key={item.path} className="px-3 py-2">
            <span className="block break-all font-mono text-xs">{iso(item.path)}</span>
            <span className="block text-xs text-muted">{t(`libraries.reason.${item.reason}`, { defaultValue: item.message })}</span>
          </li>
        ))}
      </ul>
      {items.length < total && (
        <Button small onClick={() => void more(items.length)}>
          {t('libraries.problemsMore')}
        </Button>
      )}
    </div>
  )
}

/** Making a library (a name, a folder, what it holds) or changing one (the folder cannot be changed: make a new one). */
function LibraryDialog({ library, roots, rootText, onClose, onDone }: { library?: LibraryAdmin; roots: Roots; rootText: string; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState(library?.name ?? '')
  const [path, setPath] = useState('')
  const [kind, setKind] = useState<LibraryKind>(library?.kind ?? 'both')
  const [auto, setAuto] = useState(library?.scan_enabled ?? true)
  const { busy, problem, values, run } = useAction()
  const folders = roots.flatMap((entry) => entry.folders)
  const example = folders[0] ?? roots[0]?.root ?? '/books'
  const ready = name.trim() !== '' && (library !== undefined || path.trim() !== '')
  // A refusal about the folder is told at the field of the folder, any other one below the form.
  const atPath = !library && problem !== null && (problem.startsWith('path_') || problem === 'no_roots')
  const save = () =>
    run(async () => {
      if (library) await librariesApi.change(library.id, { name: name.trim(), kind, scan_enabled: auto })
      else await librariesApi.create({ name: name.trim(), path: path.trim(), kind, scan_enabled: auto })
      onDone()
    })
  return (
    <Dialog title={library ? t('libraries.editTitle', { name: library.name }) : t('libraries.add')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (ready) void save()
        }}
      >
        {!library && <p className="text-sm text-ink-2">{t('libraries.addText', { roots: rootText })}</p>}
        <Input label={t('libraries.name')} value={name} onChange={setName} placeholder={t('libraries.namePlaceholder')} maxLength={80} autoFocus />
        {!library && (
          <div className="text-sm">
            <Input label={t('libraries.path')} value={path} onChange={setPath} placeholder={example} hint={t('libraries.pathHint', { example })} />
            {atPath && (
              <div className="mt-2">
                <Feedback problem={problem} values={values} />
              </div>
            )}
            {folders.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5" aria-label={t('libraries.path')}>
                {folders.slice(0, 12).map((folder) => (
                  <button key={folder} type="button" onClick={() => setPath(folder)} className="rounded-xl border border-line px-2.5 py-1 text-left font-mono text-xs break-all text-ink-2 hover:bg-sheet-2">
                    {folder}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="text-sm">
          <span className="font-semibold">{t('libraries.kind')}</span>
          <div className="mt-1">
            <Segment label={t('libraries.kind')} value={kind} onChange={setKind} options={KINDS.map((value) => ({ value, label: t(`libraries.kinds.${value}`) }))} />
          </div>
        </div>
        <Toggle label={t('libraries.auto')} hint={t('libraries.autoHint')} checked={auto} onChange={setAuto} />
        {!atPath && <Feedback problem={problem} values={values} />}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="submit" primary busy={busy} disabled={!ready}>
            {library ? t('common.save') : t('libraries.create')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

type AccountRow = Me & { blocked?: boolean }

/** Who is in the library: every account with a choice of no access, read or manage; saved all at once. */
function MembersDialog({ library, onClose, onDone }: { library: LibraryAdmin; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [accounts, setAccounts] = useState<AccountRow[]>([])
  const [rights, setRights] = useState<Record<number, LibraryRight | 'none'>>(() => Object.fromEntries(library.members.map((member: LibraryMember) => [member.user_id, member.right])))
  const { busy, problem, values, run } = useAction()
  useEffect(() => {
    api<AccountRow[]>('/api/v1/accounts').then((found) => setAccounts(Array.isArray(found) ? found : []), () => setAccounts([]))
  }, [])
  const save = () =>
    run(async () => {
      const members = Object.entries(rights)
        .filter(([, right]) => right !== 'none')
        .map(([user, right]) => ({ user_id: Number(user), right: right as LibraryRight }))
      await librariesApi.members(library.id, members)
      onDone()
    })
  return (
    <Dialog title={t('libraries.membersTitle', { name: library.name })} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <p className="text-sm text-ink-2">{t('libraries.membersText')}</p>
        <ul className="divide-y divide-line rounded-xl border border-line">
          {accounts
            .filter((account) => !account.blocked)
            .map((account) => (
              <li key={account.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 truncate font-semibold">{iso(account.display_name || account.name)}</span>
                <select
                  aria-label={iso(account.display_name || account.name)}
                  value={rights[account.id] ?? 'none'}
                  onChange={(event) => setRights({ ...rights, [account.id]: event.target.value as LibraryRight | 'none' })}
                  className="h-9 rounded-xl border border-line bg-sheet px-2 text-ink"
                >
                  {(['none', 'read', 'acquire', 'manage'] as const).map((right) => (
                    <option key={right} value={right}>
                      {t(`libraries.right.${right}`)}
                    </option>
                  ))}
                </select>
              </li>
            ))}
        </ul>
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
