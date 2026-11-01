/**
 * Settings, Fetching, Rights (the operator's): who may read, who may also ask for books, and who may look after a
 * library, one library at a time. Each right includes the one before it. Taking somebody out of a library asks first.
 * This is the same list as the members of a library under Libraries, in the shape of the design.
 */
import { Trash2, Users } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, librariesApi, type LibraryAdmin, type LibraryRight, type Me } from '../../../api/client'
import { useConfirm } from '../../../components/useConfirm'
import { iso, listOf } from '../../../lib/say'
import { Button, Card, Feedback, useAction } from '../ui'
import { SectionLine } from './bits'

type AccountRow = Me & { blocked?: boolean }

const RIGHTS: LibraryRight[] = ['read', 'acquire', 'manage']

export function RightsPart() {
  const { t } = useTranslation()
  const [libraries, setLibraries] = useState<LibraryAdmin[] | null>(null)
  const [accounts, setAccounts] = useState<AccountRow[]>([])
  const [chosen, setChosen] = useState<number | null>(null)
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const load = useCallback(async () => {
    const [list, people] = await Promise.all([librariesApi.every(), api<AccountRow[]>('/api/v1/accounts').catch(() => [])])
    setLibraries(list)
    setAccounts(listOf<AccountRow>(people).filter((account) => !account.blocked))
  }, [])
  useEffect(() => {
    load().catch(() => setLibraries([]))
  }, [load])
  if (!libraries) return <p className="text-sm text-muted">{t('procure.loading')}</p>
  if (libraries.length === 0) {
    return (
      <div className="space-y-4">
        <SectionLine title={t('procure.rights.heading')} text={t('procure.rights.text')} />
        <p className="text-sm text-muted italic">{t('procure.rights.noLibrary')}</p>
      </div>
    )
  }
  const library = libraries.find((item) => item.id === chosen) ?? libraries[0]
  const rightOf = (id: number): LibraryRight | null => library.members.find((member) => member.user_id === id)?.right ?? null
  /**
   * Only the one person whose right changes is sent, with the right this page saw for them (`was`): when somebody else
   * changed that person meanwhile, the server answers 409 and changes nothing, and the list is read again here either way.
   */
  const change = async (id: number, right: LibraryRight | null) => {
    try {
      await librariesApi.changeMembers(library.id, [{ user_id: id, was: rightOf(id), right }])
    } finally {
      await load().catch(() => undefined)
    }
  }
  const set = (id: number, right: LibraryRight, name: string) =>
    run(() => change(id, right), t('procure.rights.set', { name: iso(name), right: t(`libraries.right.${right}`), library: iso(library.name) }))
  const take = (id: number, name: string) =>
    ask({
      title: t('procure.rights.take.title', { name: iso(name) }),
      text: t('procure.rights.take.text', { name: iso(name), library: iso(library.name) }),
      confirm: t('procure.rights.take.confirm'),
      run: () => change(id, null),
    })
  return (
    <div className="space-y-6">
      <SectionLine title={t('procure.rights.heading')} text={t('procure.rights.text')} />
      <div role="tablist" aria-label={t('procure.rights.library')} className="flex flex-wrap gap-2">
        {libraries.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={item.id === library.id}
            onClick={() => setChosen(item.id)}
            className={`rounded-full border px-3.5 py-1.5 text-sm font-semibold ${item.id === library.id ? 'border-accent/50 bg-accent-soft text-accent' : 'border-line bg-sheet text-ink-2 hover:text-ink'}`}
          >
            {iso(item.name)}
          </button>
        ))}
      </div>
      <Card id="rights" icon={Users} title={t('procure.rights.members', { name: iso(library.name) })} text={t('procure.rights.membersText')}>
        <ul className="space-y-2" data-testid="rights-members">
          {accounts.map((account) => {
            const right = rightOf(account.id)
            const name = account.display_name || account.name
            return (
              <li key={account.id} data-testid={`right-${account.name}`} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
                <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-soft font-bold text-accent">
                  {name.charAt(0).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <b className="[overflow-wrap:anywhere]">{iso(name)}</b>
                  <small className="block text-xs text-muted">{account.role === 'operator' ? t('procure.rights.operator') : t('procure.rights.member')}</small>
                </span>
                <div role="radiogroup" aria-label={t('procure.rights.rightOf', { name: iso(name) })} className="inline-flex rounded-full border border-line bg-sheet-2/60 p-1 text-sm font-semibold">
                  {RIGHTS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      role="radio"
                      aria-checked={right === option}
                      disabled={busy}
                      onClick={() => right !== option && void set(account.id, option, name)}
                      className={`rounded-full px-3 py-1 ${right === option ? 'bg-accent-soft text-ink' : 'text-muted hover:text-ink'}`}
                    >
                      {t(`libraries.right.${option}`)}
                    </button>
                  ))}
                </div>
                <Button small label={t('procure.rights.takeLabel', { name: iso(name) })} className="w-8 !px-0" disabled={right === null || busy} onClick={() => take(account.id, name)}>
                  <Trash2 size={14} aria-hidden />
                </Button>
              </li>
            )
          })}
        </ul>
        <div className="grid gap-1 text-sm text-ink-2" data-testid="rights-legend">
          <p>
            <b>{t('libraries.right.read')}</b> {t('procure.rights.legend.read')}
          </p>
          <p>
            <b>{t('libraries.right.acquire')}</b> {t('procure.rights.legend.acquire')}
          </p>
          <p>
            <b>{t('libraries.right.manage')}</b> {t('procure.rights.legend.manage')}
          </p>
        </div>
        <Feedback problem={problem} done={done} values={values} />
      </Card>
      {dialog}
    </div>
  )
}
