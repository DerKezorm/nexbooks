/**
 * The own device tokens on the account page, under Devices and apps: a phone, a later native app or a script that
 * speaks for the account with a token of its own (`Authorization: Bearer nbd_...`). List, make, take off. The token
 * is shown once, right after it was made; afterwards only its first characters. One device can bring another: the
 * iPhone app may confirm the code of a television. Such a device says through which device it came, and the question
 * before removing a device names what goes with it (the server revokes them together).
 */
import { Plus, QrCode, Smartphone, Trash2, Tv } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { devicesApi, type PairedDevice } from '../api/client'
import { copyText } from '../lib/copy'
import { iso } from '../lib/say'
import { Button, Card, Feedback, Input, useAction } from '../pages/settings/ui'
import { AppConnect } from './AppConnect'
import { DeviceCodeConnect } from './DeviceCodeConnect'
import { useConfirm } from './useConfirm'

export function Devices() {
  const { t, i18n } = useTranslation()
  const [list, setList] = useState<PairedDevice[] | null>(null)
  const [making, setMaking] = useState(false)
  const [name, setName] = useState('')
  const [shown, setShown] = useState<{ name: string; token: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [typing, setTyping] = useState(false)
  const action = useAction()
  const { ask, dialog } = useConfirm()
  const load = useCallback(() => devicesApi.list().then((found) => setList(Array.isArray(found) ? found : []), () => setList([])), [])
  useEffect(() => {
    void load()
  }, [load])
  const moment = (value: string) => new Date(value).toLocaleString(i18n.language, { dateStyle: 'short', timeStyle: 'short' })
  return (
    <Card id="devices" icon={Smartphone} title={t('devices.title')} text={t('devices.text')}>
      {shown && (
        <div className="rounded-xl border border-accent/40 bg-accent-soft/50 p-4 text-sm" data-testid="device-token-shown">
          <p className="font-semibold">{t('devices.shownOnce', { name: shown.name })}</p>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-sheet px-2 py-1.5 font-mono text-[11px] leading-5 whitespace-pre">{shown.token}</pre>
          <div className="mt-3 flex gap-2">
            <Button small onClick={() => void copyText(shown.token).then((done) => done && setCopied(true))}>
              {copied ? t('common.copied') : t('common.copy')}
            </Button>
            <Button
              small
              onClick={() => {
                setShown(null)
                setCopied(false)
              }}
            >
              {t('devices.done')}
            </Button>
          </div>
        </div>
      )}
      {list && list.length > 0 && (
        <ul className="space-y-2" data-testid="device-tokens">
          {list.map((device) => (
            <li key={device.id} className="flex items-center gap-3 rounded-xl border border-line bg-sheet-2/50 px-4 py-3 text-sm">
              {device.via ? <Tv size={17} className="shrink-0 text-accent" aria-hidden /> : <Smartphone size={17} className="shrink-0 text-accent" aria-hidden />}
              <span className="min-w-0 flex-1">
                <span className="font-semibold break-words">{device.name}</span> <code className="font-mono text-xs text-muted">{device.prefix}…</code>
                {device.via && (
                  <span className="block text-xs break-words text-muted" data-testid="device-via">
                    {t('devices.via', { name: iso(device.via.name) })}
                  </span>
                )}
                <span className="block text-xs text-muted">
                  {device.last_used_at ? t('devices.used', { when: moment(device.last_used_at) }) : t('devices.unused')}
                </span>
              </span>
              <button
                type="button"
                onClick={() => {
                  const brought = list.filter((other) => other.via?.id === device.id).map((other) => iso(other.name))
                  ask({
                    title: t('confirm.device.title', { name: iso(device.name) }),
                    text: brought.length
                      ? t('confirm.device.textBrought', { count: brought.length, names: brought.join(', ') })
                      : t('confirm.device.text'),
                    confirm: t('confirm.device.confirm'),
                    run: async () => {
                      await devicesApi.revoke(device.id)
                      await load()
                    },
                  })
                }}
                className="rounded-full p-1.5 text-muted hover:bg-sheet hover:text-ink"
                aria-label={t('devices.revokeNamed', { name: device.name })}
              >
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {shown ? null : !making ? (
        <div className="flex flex-wrap gap-2">
          <Button primary onClick={() => setConnecting(true)}>
            <QrCode size={16} /> {t('appConnect.button')}
          </Button>
          <Button onClick={() => setTyping(true)}>
            <Tv size={16} /> {t('deviceCode.button')}
          </Button>
          <Button onClick={() => setMaking(true)}>
            <Plus size={16} /> {t('devices.new')}
          </Button>
        </div>
      ) : (
        <form
          className="space-y-3 rounded-xl border border-dashed border-line p-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!name.trim()) return
            void action.run(async () => {
              const made = await devicesApi.make(name.trim())
              setShown({ name: made.device.name, token: made.token })
              setCopied(false)
              setMaking(false)
              setName('')
              await load()
            })
          }}
        >
          <Input label={t('devices.name')} value={name} onChange={setName} maxLength={80} autoFocus placeholder={t('devices.namePlaceholder')} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" primary busy={action.busy} disabled={!name.trim()}>
              {t('devices.make')}
            </Button>
            <Button onClick={() => setMaking(false)}>{t('common.cancel')}</Button>
          </div>
        </form>
      )}
      <Feedback problem={action.problem} />
      {connecting && (
        <AppConnect
          onClose={() => {
            setConnecting(false)
            void load()
          }}
        />
      )}
      {typing && (
        <DeviceCodeConnect
          onClose={() => {
            setTyping(false)
            void load()
          }}
        />
      )}
      {dialog}
    </Card>
  )
}
