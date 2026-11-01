/**
 * E-readers on the account page, under Devices and apps: one row for each way to get books onto a reading device, or to
 * keep the place in them. Four: the Kindle (the address it has at Amazon, so that "Send to Kindle" on a book can mail it),
 * the catalogue for reading apps (OPDS), the progress sync of KOReader and the sync of a Kobo. The last three are
 * connections with a secret of their own. A reading app takes it as the password of the account's name; a Kobo can be
 * given an address and nothing else, so for it the secret is part of the address (`api_endpoint` in its settings file).
 *
 * The secret of a connection is shown once, right after it was made, with the address and (but for a Kobo) the name to
 * enter next to it; afterwards only its first characters. Deleting a connection asks first and says that it cannot be
 * taken back.
 */
import { BookOpen, Check, Plus, RefreshCw, Rss, Send, Tablet, TabletSmartphone, Trash2, type LucideIcon } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ereadersApi, type EreaderLink, type EreadersState } from '../api/client'
import { iso } from '../lib/say'
import { forgetEreaders } from '../state/ereaders'
import { Button, Card, CopyLink, Feedback, Input, useAction } from '../pages/settings/ui'
import { useAuth } from '../state/auth'
import { useConfirm } from './useConfirm'

type RowProps = { id: string; icon: LucideIcon; title: string; beta?: string; text: string; status: string; action: string; open: boolean; onToggle: () => void; children: ReactNode }

/** One way to get books onto a reading device: what it is, how far it is set up, and the button that opens its setup. */
function Row({ id, icon: Icon, title, beta, text, status, action, open, onToggle, children }: RowProps) {
  const { t } = useTranslation()
  return (
    <li className="rounded-xl border border-line bg-sheet-2/50 text-sm" data-testid={`ereader-${id}`}>
      <div className="flex items-center gap-3 px-4 py-3">
        <Icon size={17} className="shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="font-semibold">{title}</span>
          {beta && (
            <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-[11px] font-semibold text-ink-2" data-testid={`ereader-${id}-beta`}>
              {beta}
            </span>
          )}
          <span className="block text-xs text-muted">{text}</span>
          <span className="block text-xs text-ink-2" data-testid={`ereader-${id}-status`}>
            {status}
          </span>
        </span>
        <Button small onClick={onToggle} label={`${open ? t('ereaders.close') : action}: ${title}`}>
          {open ? t('ereaders.close') : action}
        </Button>
      </div>
      {open && <div className="space-y-3 border-t border-line px-4 py-4">{children}</div>}
    </li>
  )
}

/** The address of the Kindle, a test mail, and what the person has to do at Amazon. */
function KindleSetup({ state, onChange }: { state: EreadersState; onChange: () => void }) {
  const { t } = useTranslation()
  const { kindle } = state
  const [address, setAddress] = useState(kindle.address)
  const action = useAction()
  const test = useAction()
  return (
    <>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          void action.run(async () => {
            await ereadersApi.setKindle(address.trim())
            onChange()
          }, t('ereaders.kindle.saved'))
        }}
      >
        <Input label={t('ereaders.kindle.address')} value={address} onChange={setAddress} maxLength={255} mono placeholder={t('ereaders.kindle.placeholder')} hint={t('ereaders.kindle.hint')} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" primary busy={action.busy}>
            {t('ereaders.kindle.save')}
          </Button>
          {kindle.mail_ready && kindle.address && (
            <Button busy={test.busy} onClick={() => void test.run(() => ereadersApi.testKindle(), t('ereaders.kindle.tested'))}>
              <Send size={15} /> {t('ereaders.kindle.test')}
            </Button>
          )}
        </div>
      </form>
      <Feedback problem={action.problem} done={action.done} />
      <Feedback problem={test.problem} done={test.done} />
      {kindle.mail_ready ? (
        <p className="text-xs text-ink-2" data-testid="kindle-mail">
          {t('ereaders.kindle.mail', { sender: kindle.sender, mb: kindle.max_megabytes })}
        </p>
      ) : (
        <p className="text-xs text-ink-2" data-testid="kindle-web">
          {t('ereaders.kindle.noMail')}
        </p>
      )}
      <p className="text-xs text-muted">{t('ereaders.kindle.only')}</p>
    </>
  )
}

/** What each kind of connection is opened at: the catalogue under `/opds`, the sync of KOReader under `/kosync`, the sync
 * of a Kobo under `/kobo/<secret>` (the secret is added where the address is shown). */
const ADDRESS = { opds: '/opds', kosync: '/kosync', kobo: '/kobo' } as const
type ConnectionKind = keyof typeof ADDRESS

/** The connections of one kind (the catalogue for reading apps, the sync of KOReader), and the form that makes another. */
function ConnectionSetup({ kind, state, onChange }: { kind: ConnectionKind; state: EreadersState; onChange: () => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const [making, setMaking] = useState(false)
  const [name, setName] = useState('')
  const [shown, setShown] = useState<{ name: string; username: string; secret: string } | null>(null)
  const action = useAction()
  const { ask, dialog } = useConfirm()
  const links = state.links.filter((link) => link.kind === kind)
  const moment = (value: string) => new Date(value).toLocaleString(i18n.language, { dateStyle: 'short', timeStyle: 'short' })
  const catalogue = `${window.location.origin}${ADDRESS[kind]}${kind === 'kobo' && shown ? `/${shown.secret}` : ''}`
  const k = `ereaders.${kind}`
  // The page is seen over https, but nexbooks answers as if it were http: a proxy it was not told about. The Kobo would be
  // handed addresses with http, and they hold its secret.
  const behindUnknownProxy = window.location.protocol === 'https:' && (state.kobo_base ?? '').startsWith('http:')
  return (
    <>
      <p className="text-xs text-ink-2">{t(`${k}.how`)}</p>
      {kind === 'kobo' && (
        <p className="text-xs text-ink-2" data-testid="ereader-kobo-beta-hint">
          {t('ereaders.kobo.betaHint')}
        </p>
      )}
      {kind === 'kobo' && behindUnknownProxy && (
        <p className="rounded-xl border border-bad/40 bg-bad/5 p-3 text-xs font-semibold text-bad" role="alert" data-testid="ereader-proxy-warning">
          {t('ereaders.kobo.proxyWarning', { seen: state.kobo_base })}
        </p>
      )}
      {shown && (
        <div className="space-y-2 rounded-xl border border-accent/40 bg-accent-soft/50 p-4" data-testid="ereader-shown">
          <p className="font-semibold">{t(`${k}.shownOnce`, { name: shown.name })}</p>
          <div className="space-y-1">
            <span className="text-xs text-muted">{t(`${k}.address`)}</span>
            <CopyLink value={catalogue} label={t(`${k}.address`)} />
          </div>
          {kind === 'kobo' ? (
            <div className="space-y-1">
              <span className="text-xs text-muted">{t('ereaders.kobo.line')}</span>
              <CopyLink value={`api_endpoint=${catalogue}`} label={t('ereaders.kobo.line')} />
            </div>
          ) : (
            <>
              <div className="space-y-1">
                <span className="text-xs text-muted">{t(`${k}.user`)}</span>
                <CopyLink value={shown.username} label={t(`${k}.user`)} />
              </div>
              <div className="space-y-1">
                <span className="text-xs text-muted">{t(`${k}.password`)}</span>
                <CopyLink value={shown.secret} label={t(`${k}.password`)} />
              </div>
            </>
          )}
          <p className="text-xs text-ink-2">{t(`${k}.passwordHint`)}</p>
          <Button small onClick={() => setShown(null)}>
            {t(`${k}.done`)}
          </Button>
        </div>
      )}
      {links.length > 0 && (
        <ul className="space-y-2" data-testid="ereader-links">
          {links.map((link: EreaderLink) => (
            <li key={link.id} className="flex items-center gap-3 rounded-xl border border-line bg-sheet px-4 py-3 text-sm" data-testid="ereader-link">
              <BookOpen size={16} className="shrink-0 text-accent" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="font-semibold break-words">{link.name}</span> <code className="font-mono text-xs text-muted">{link.prefix}…</code>
                <span className="block text-xs text-muted">
                  {link.blocked ? (
                    <span className="font-semibold text-bad" data-testid="ereader-link-blocked">
                      {t(`${k}.blocked`)}
                    </span>
                  ) : link.last_used_at ? (
                    t(`${k}.used`, { when: moment(link.last_used_at) })
                  ) : (
                    t(`${k}.unused`)
                  )}
                </span>
              </span>
              {!link.blocked && (
                <button
                  type="button"
                  onClick={() =>
                    ask({
                      title: t('confirm.ereaderLink.title'),
                      text: t('confirm.ereaderLink.text', { name: iso(link.name) }),
                      confirm: t('confirm.ereaderLink.confirm'),
                      run: async () => {
                        await ereadersApi.remove(link.id)
                        onChange()
                      },
                    })
                  }
                  className="rounded-full p-1.5 text-muted hover:bg-sheet-2 hover:text-ink"
                  aria-label={t(`${k}.deleteNamed`, { name: link.name })}
                >
                  <Trash2 size={15} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {shown ? null : !making ? (
        <div>
          <Button onClick={() => setMaking(true)}>
            <Plus size={16} /> {t(`${k}.new`)}
          </Button>
        </div>
      ) : (
        <form
          className="space-y-3 rounded-xl border border-dashed border-line p-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!name.trim()) return
            void action.run(async () => {
              const made = await ereadersApi.make(name.trim(), kind)
              setShown({ name: made.link.name, username: made.username || me?.name || '', secret: made.secret })
              setMaking(false)
              setName('')
              onChange()
            })
          }}
        >
          <Input label={t(`${k}.name`)} value={name} onChange={setName} maxLength={100} autoFocus placeholder={t(`${k}.namePlaceholder`)} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" primary busy={action.busy} disabled={!name.trim()}>
              {t(`${k}.make`)}
            </Button>
            <Button onClick={() => setMaking(false)}>{t('common.cancel')}</Button>
          </div>
        </form>
      )}
      <Feedback problem={action.problem} values={action.values} />
      {dialog}
    </>
  )
}

/** The rows, in the order the page shows them. */
const ROWS = ['kindle', 'opds', 'kosync', 'kobo'] as const
type RowId = (typeof ROWS)[number]

export function EReaders() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [state, setState] = useState<EreadersState | null>(null)
  const [open, setOpen] = useState<RowId | null>(null)
  const load = useCallback(
    () =>
      ereadersApi.state().then(
        (found) => {
          setState(found && typeof found.allowed === 'boolean' && found.kindle ? found : null)
          // The page of a book asks again for what the person set here.
          forgetEreaders()
        },
        () => setState(null),
      ),
    [],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (!state) return null
  if (!state.allowed) {
    return (
      <Card id="ereaders" icon={Tablet} title={t('ereaders.title')} text={t('ereaders.text')}>
        <p className="text-sm text-ink-2" data-testid="ereaders-off">
          {me?.role === 'operator' ? t('ereaders.offForOperator') : t('ereaders.off')}{' '}
          {me?.role === 'operator' && (
            <Link to="/settings?tab=api" className="font-semibold text-accent underline decoration-accent/40 underline-offset-4">
              {t('ereaders.offOperator')}
            </Link>
          )}
        </p>
        {(['opds', 'kosync', 'kobo'] as const).map(
          (kind) => state.links.some((link) => link.kind === kind) && <ConnectionSetup key={kind} kind={kind} state={state} onChange={() => void load()} />,
        )}
      </Card>
    )
  }
  const count = (kind: ConnectionKind) => state.links.filter((link) => link.kind === kind).length
  const rows: Record<RowId, { icon: LucideIcon; status: string; action: string; body: ReactNode }> = {
    kindle: {
      icon: Send,
      status: state.kindle.address ? state.kindle.address : t('ereaders.kindle.none'),
      action: state.kindle.address ? t('ereaders.change') : t('ereaders.setup'),
      body: <KindleSetup state={state} onChange={() => void load()} />,
    },
    opds: {
      icon: Rss,
      status: count('opds') > 0 ? t('ereaders.opds.count', { count: count('opds') }) : t('ereaders.opds.none'),
      action: t('ereaders.connect'),
      body: <ConnectionSetup kind="opds" state={state} onChange={() => void load()} />,
    },
    kosync: {
      icon: RefreshCw,
      status: count('kosync') > 0 ? t('ereaders.kosync.count', { count: count('kosync') }) : t('ereaders.kosync.none'),
      action: t('ereaders.connect'),
      body: <ConnectionSetup kind="kosync" state={state} onChange={() => void load()} />,
    },
    kobo: {
      icon: TabletSmartphone,
      status: count('kobo') > 0 ? t('ereaders.kobo.count', { count: count('kobo') }) : t('ereaders.kobo.none'),
      action: t('ereaders.connect'),
      body: <ConnectionSetup kind="kobo" state={state} onChange={() => void load()} />,
    },
  }
  return (
    <Card id="ereaders" icon={Tablet} title={t('ereaders.title')} text={t('ereaders.text')}>
      <ul className="space-y-2" data-testid="ereader-rows">
        {ROWS.map((id) => (
          <Row
            key={id}
            id={id}
            icon={rows[id].icon}
            title={t(`ereaders.${id}.title`)}
            beta={id === 'kobo' ? t('ereaders.kobo.beta') : undefined}
            text={t(`ereaders.${id}.text`)}
            status={rows[id].status}
            action={rows[id].action}
            open={open === id}
            onToggle={() => setOpen(open === id ? null : id)}
          >
            {rows[id].body}
          </Row>
        ))}
      </ul>
      <p className="flex items-center gap-1.5 text-xs text-muted">
        <Check size={13} aria-hidden /> {t('ereaders.safe')}
      </p>
    </Card>
  )
}
