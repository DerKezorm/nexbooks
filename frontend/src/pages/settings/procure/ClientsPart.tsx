/**
 * Settings, Fetching, Download programs (the operator's): SABnzbd, NZBGet, qBittorrent, Transmission and Deluge, a card for
 * each with its state and version, a dialog that tests the connection step by step (reachable, version, login, the category
 * `nexbooks` made and read back), and the path mappings the operator confirmed.
 */
import { Check, FolderTree, Plug, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { procureApi, type ClientForm, type ClientInfo, type ClientKind, type ClientTest, type PathMapping } from '../../../api/client'
import { Dialog } from '../../../components/Dialog'
import { useConfirm } from '../../../components/useConfirm'
import { iso } from '../../../lib/say'
import { Button, Card, Feedback, Input, useAction } from '../ui'
import { CardGrid, Chip, Code, ItemCard, Plaque, SectionLine, type Tone } from './bits'
import { keyFollows, problemText } from './helpers'

/** The programs, how each one is told who is asking, and the protocol it speaks. */
const CLIENT_KINDS: { kind: ClientKind; name: string; protocol: 'usenet' | 'torrent'; auth: 'key' | 'user' | 'password'; placeholder: string }[] = [
  { kind: 'sabnzbd', name: 'SABnzbd', protocol: 'usenet', auth: 'key', placeholder: 'http://sabnzbd.example.com:8080' },
  { kind: 'nzbget', name: 'NZBGet', protocol: 'usenet', auth: 'user', placeholder: 'http://nzbget.example.com:6789' },
  { kind: 'qbittorrent', name: 'qBittorrent', protocol: 'torrent', auth: 'user', placeholder: 'http://qbittorrent.example.com:8081' },
  { kind: 'transmission', name: 'Transmission', protocol: 'torrent', auth: 'user', placeholder: 'http://transmission.example.com:9091' },
  { kind: 'deluge', name: 'Deluge', protocol: 'torrent', auth: 'password', placeholder: 'http://deluge.example.com:8112' },
]

const TONE: Record<ClientInfo['state'], Tone> = { ready: 'ok', unreachable: 'bad', login: 'bad', other: 'bad' }

export function ClientsPart() {
  const { t } = useTranslation()
  const [clients, setClients] = useState<ClientInfo[] | null>(null)
  const [mappings, setMappings] = useState<PathMapping[]>([])
  const [dialog, setDialog] = useState<{ edit: ClientInfo | null } | null>(null)
  const load = useCallback(async () => {
    const [list, maps] = await Promise.all([procureApi.clients(), procureApi.mappings()])
    setClients(list)
    setMappings(maps)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
  }, [load])
  if (!clients) return <p className="text-sm text-muted">{t('procure.loading')}</p>
  return (
    <div className="space-y-6">
      <SectionLine title={t('procure.client.heading')} text={t('procure.client.text')} />
      <CardGrid>
        {clients.map((client) => (
          <ClientCard key={client.id} client={client} onEdit={() => setDialog({ edit: client })} onChange={load} />
        ))}
        <button type="button" data-testid="client-add" onClick={() => setDialog({ edit: null })} className="card flex min-h-40 flex-col items-center justify-center gap-1 border-dashed p-4 text-center text-ink-2 hover:bg-sheet-2">
          <span className="mb-1 grid h-10 w-10 place-items-center rounded-full bg-accent-soft text-accent">
            <Plus size={20} aria-hidden />
          </span>
          <b className="font-display">{t('procure.client.add')}</b>
          <small className="text-muted">{t('procure.client.addHint')}</small>
        </button>
      </CardGrid>
      <MappingsCard mappings={mappings} onChange={load} />
      {dialog && (
        <ClientDialog
          client={dialog.edit}
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

function ClientCard({ client, onEdit, onChange }: { client: ClientInfo; onEdit: () => void; onChange: () => Promise<void> }) {
  const { t } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [testing, setTesting] = useState(false)
  const test = async () => {
    setTesting(true)
    try {
      const found = await procureApi.testClient(client.id)
      await run(async () => undefined, t(found.has_categories ? 'procure.client.tested' : 'procure.client.testedFolder', { name: iso(client.name), version: found.version, category: client.category }))
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
      title: t('procure.client.remove.title', { name: iso(client.name) }),
      text: t('procure.client.remove.text', { name: iso(client.name) }),
      confirm: t('procure.client.remove.confirm'),
      run: async () => {
        await procureApi.removeClient(client.id)
        await onChange()
      },
    })
  return (
    <ItemCard dim={!client.enabled} testid={`client-${client.id}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold [overflow-wrap:anywhere]">{iso(client.name)}</h3>
          <p className="mt-1">
            <Chip>{t(`procure.client.${client.protocol}`)}</Chip>
          </p>
        </div>
        <Plaque tone={TONE[client.state]} testid={`client-state-${client.id}`}>
          {t(`procure.client.state.${client.state}`)}
        </Plaque>
      </div>
      <Code>{client.url}</Code>
      {client.state === 'ready' ? (
        <p className="text-sm text-ink-2" data-testid={`client-line-${client.id}`}>
          {t(client.kind === 'transmission' ? 'procure.client.lineFolder' : client.kind === 'nzbget' ? 'procure.client.lineNzbget' : 'procure.client.line', { version: client.version, category: client.category })}
        </p>
      ) : (
        <p className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad" data-testid={`client-note-${client.id}`}>
          {t('procure.client.unreached', { name: iso(client.name) })}
        </p>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2">
        <Button small busy={testing} onClick={() => void test()}>
          {t('procure.test')}
        </Button>
        <Button small onClick={onEdit}>
          {t('procure.edit')}
        </Button>
        <Button small label={t('procure.client.removeLabel')} className="w-8 !px-0" disabled={busy} onClick={remove}>
          <Trash2 size={14} aria-hidden />
        </Button>
      </div>
      <Feedback problem={problem} done={done} values={values} />
      {dialog}
    </ItemCard>
  )
}

function ClientDialog({ client, onClose, onDone }: { client: ClientInfo | null; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const edit = client !== null
  const [kind, setKind] = useState<ClientKind>(client?.kind ?? 'sabnzbd')
  const [label, setLabel] = useState(client?.name ?? 'SABnzbd')
  const [url, setUrl] = useState(client?.url ?? '')
  const [user, setUser] = useState(client?.username ?? '')
  const [secret, setSecret] = useState('')
  const [category, setCategory] = useState(client?.category ?? 'nexbooks')
  const [tested, setTested] = useState(edit)
  const [testing, setTesting] = useState(false)
  const [steps, setSteps] = useState<{ good: boolean; text: string; note?: string }[] | null>(null)
  const [local, setLocal] = useState<string | null>(null)
  const { busy, problem, values, run } = useAction()
  const info = CLIENT_KINDS.find((item) => item.kind === kind) ?? CLIENT_KINDS[0]
  const connection = (change: () => void) => {
    change()
    setTested(false)
    setSteps(null)
  }
  // The saved key or password is sent only to the place it was saved for: another address needs it again.
  const needKey = edit && !secret && kind === client.kind && !keyFollows(client.url, url.trim())
  const test = async () => {
    if (!url.trim()) return setSteps([{ good: false, text: t('procure.client.dialog.needAddress') }])
    if (needKey) return setSteps([{ good: false, text: t('procure.keyAgain') }])
    setTesting(true)
    setSteps(null)
    try {
      const found: ClientTest = await procureApi.testClientAddress({ client_id: client?.id, kind, url: url.trim(), username: user, secret: secret || undefined, category: category.trim() || 'nexbooks' })
      // Say only what happened: a category is "made" only where nexbooks made it; NZBGet had it already (nexbooks never
      // writes there) and Transmission has none.
      const categoryStep = !found.has_categories
        ? { good: true, text: t('procure.client.dialog.noCategory'), note: t('procure.client.dialog.noCategoryNote', { category: found.category }) }
        : found.category_existed
          ? { good: true, text: t('procure.client.dialog.categoryThere', { category: found.category }), note: t('procure.client.dialog.readBack') }
          : { good: true, text: t('procure.client.dialog.category', { category: found.category }), note: t('procure.client.dialog.readBack') }
      setSteps([
        { good: true, text: t('procure.client.dialog.reachable'), note: url.trim().replace(/^https?:\/\//, '') },
        { good: true, text: t('procure.client.dialog.version', { version: found.version }), note: t('procure.client.dialog.supported') },
        { good: true, text: t('procure.client.dialog.login') },
        categoryStep,
      ])
      setTested(true)
    } catch (error) {
      setSteps([{ good: false, text: problemText(error) }])
    } finally {
      setTesting(false)
    }
  }
  const save = () => {
    setLocal(null)
    if (!label.trim() || !url.trim()) return setLocal(t('procure.client.dialog.needNameAddress'))
    if (needKey) return setLocal(t('procure.keyAgain'))
    if (!tested) return setLocal(t('procure.client.dialog.needTest'))
    const form: ClientForm = { kind, name: label.trim(), url: url.trim(), username: info.auth === 'user' ? user : '', category: category.trim() || 'nexbooks', ...(secret ? { secret } : {}) }
    void run(async () => {
      if (client) await procureApi.changeClient(client.id, form)
      else await procureApi.addClient(form)
      onDone()
    })
  }
  return (
    <Dialog title={edit ? t('procure.client.dialog.editTitle', { name: iso(client.name) }) : t('procure.client.dialog.title')} broad onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="font-semibold">{t('procure.client.dialog.program')}</span>
            <select
              value={kind}
              onChange={(event) => {
                const next = event.target.value as ClientKind
                connection(() => {
                  setKind(next)
                  setLabel(CLIENT_KINDS.find((item) => item.kind === next)?.name ?? '')
                  setSecret('')
                })
              }}
              className="mt-1 block h-11 w-full rounded-xl border border-line bg-sheet px-3 text-ink"
            >
              {CLIENT_KINDS.map((item) => (
                <option key={item.kind} value={item.kind}>
                  {item.name} ({t(`procure.client.${item.protocol}`)})
                </option>
              ))}
            </select>
          </label>
          <Input label={t('procure.name')} value={label} onChange={setLabel} />
          <Input label={t('procure.address')} value={url} onChange={(value) => connection(() => setUrl(value))} placeholder={info.placeholder} className="sm:col-span-2" mono />
          {info.auth === 'user' && <Input label={t('procure.client.dialog.user')} value={user} onChange={(value) => connection(() => setUser(value))} />}
          <Input
            label={info.auth === 'key' ? t('procure.apiKey') : t('procure.client.dialog.password')}
            type="password"
            value={secret}
            onChange={(value) => connection(() => setSecret(value))}
            placeholder={edit && client.secret_set && !needKey ? t('procure.keyKept') : info.auth === 'key' ? t('procure.client.dialog.keyPlaceholder') : ''}
            hint={needKey ? t('procure.keyAgain') : undefined}
            className={info.auth === 'user' ? '' : 'sm:col-span-2'}
            mono={info.auth === 'key'}
          />
          <Input
            label={t(kind === 'transmission' ? 'procure.client.dialog.folderLabel' : 'procure.client.dialog.categoryLabel')}
            value={category}
            onChange={(value) => connection(() => setCategory(value))}
            hint={t(kind === 'transmission' ? 'procure.client.dialog.folderHint' : kind === 'nzbget' ? 'procure.client.dialog.categoryHintNzbget' : 'procure.client.dialog.categoryHint')}
          />
        </div>
        <div className="space-y-2">
          <Button small onClick={() => void test()} busy={testing}>
            <Plug size={14} aria-hidden /> {t('procure.test')}
          </Button>
          <ul aria-live="polite" data-testid="client-checks" className="space-y-1 text-sm">
            {steps?.map((step) => (
              <li key={step.text} className={`flex flex-wrap items-baseline gap-2 ${step.good ? 'text-ok' : 'text-bad'}`}>
                {step.good && <Check size={14} aria-hidden />}
                <b>{step.text}</b>
                {step.note && <small className="text-muted">{step.note}</small>}
              </li>
            ))}
          </ul>
        </div>
        {local && (
          <p role="alert" className="text-sm text-bad" data-testid="client-local-problem">
            {local}
          </p>
        )}
        <Feedback problem={problem} values={values} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button type="submit" primary busy={busy}>
            {edit ? t('common.save') : t('procure.client.dialog.create')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function MappingsCard({ mappings, onChange }: { mappings: PathMapping[]; onChange: () => Promise<void> }) {
  const { t } = useTranslation()
  const { ask, dialog } = useConfirm()
  return (
    <Card id="mappings" icon={FolderTree} title={t('procure.client.mappings.title')} text={t('procure.client.mappings.text')}>
      {mappings.length === 0 ? (
        <p className="text-sm text-muted">{t('procure.client.mappings.none')}</p>
      ) : (
        <ul className="space-y-2">
          {mappings.map((mapping) => (
            <li key={mapping.id} data-testid={`mapping-${mapping.id}`} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
              <Chip>{iso(mapping.client)}</Chip>
              <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                <div>
                  <small className="text-muted">{t('procure.client.mappings.reports')}</small>
                  <Code>{mapping.remote}</Code>
                </div>
                <span aria-hidden className="hidden text-muted sm:block">
                  →
                </span>
                <div>
                  <small className="text-muted">{t('procure.client.mappings.sees')}</small>
                  <Code>{mapping.local}</Code>
                </div>
              </div>
              <Button
                small
                label={t('procure.client.mappings.removeLabel')}
                className="w-8 !px-0"
                onClick={() =>
                  ask({
                    title: t('procure.client.mappings.remove.title'),
                    text: t('procure.client.mappings.remove.text'),
                    confirm: t('procure.client.mappings.remove.confirm'),
                    run: async () => {
                      await procureApi.removeMapping(mapping.id)
                      await onChange()
                    },
                  })
                }
              >
                <Trash2 size={14} aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {dialog}
    </Card>
  )
}
