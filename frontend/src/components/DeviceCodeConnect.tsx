/**
 * "Connect a device with a code" (My account, Devices and apps): for a device that has no camera, like an Apple TV. The
 * device shows a code; the person types it here, sees how the device is named and is asked whether to connect it to this
 * account. Only that answer lets the device fetch a token, for this account; the code alone gives nothing. After the
 * answer the page asks the server every two seconds whether the device fetched it and then says "Connected: <device>";
 * until then the answer can be taken back, after a question.
 *
 * The device's name was typed by whoever made the request, so it proves nothing about the device: the page says so in a
 * warning next to the question, and shows it as plain text.
 */
import { Check } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, devicesApi, type PairRequestPreview, type PairRequestState } from '../api/client'
import { Button, Feedback, Input } from '../pages/settings/ui'
import { Dialog } from './Dialog'
import { useConfirm } from './useConfirm'

/** How often the page looks whether the device fetched its token. */
const POLL_MS = 2000

type Problem = { code: string; values: Record<string, unknown> }

const problemOf = (error: unknown): Problem =>
  error instanceof ApiError ? { code: error.code, values: error.values } : { code: 'internal_error', values: {} }

/** `onClose`: the window closes (the page lists the devices again, whatever happened inside: there is nothing to tell it). */
export function DeviceCodeConnect({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  // One click, one request: a second click or Enter before the first has been answered does nothing, also before the page
  // has drawn the waiting button (the state is not yet there for the second one).
  const sending = useRef(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [asked, setAsked] = useState<PairRequestPreview | null>(null)
  // How long ago the device asked, read once when the answer comes (the clock is no business of the drawing).
  const [minutesAgo, setMinutesAgo] = useState(0)
  const [state, setState] = useState<PairRequestState | null>(null)
  const { ask, dialog } = useConfirm()
  const connected = state?.state === 'connected'
  const expired = state?.state === 'expired'
  const waiting = state !== null && !connected && !expired

  useEffect(() => {
    if (asked === null || !waiting) return
    const timer = window.setInterval(() => {
      devicesApi.requestState(asked.id).then(setState, (error: unknown) => {
        // Gone (404) is as good as run out: nothing is left for the device to fetch.
        if (error instanceof ApiError && error.status === 404) setState({ state: 'expired', device: null })
      })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [asked, waiting])

  const lookUp = async () => {
    if (typed.trim() === '' || sending.current) return
    sending.current = true
    setBusy(true)
    setProblem(null)
    try {
      const found = await devicesApi.lookUpRequest(typed.trim())
      setMinutesAgo(Math.max(0, Math.floor((Date.now() - new Date(found.requested_at).getTime()) / 60000)))
      setAsked(found)
    } catch (error) {
      setProblem(problemOf(error))
    } finally {
      sending.current = false
      setBusy(false)
    }
  }
  const connect = async () => {
    if (asked === null || sending.current) return
    sending.current = true
    setBusy(true)
    setProblem(null)
    try {
      setState(await devicesApi.confirmRequest(asked.id, typed.trim()))
    } catch (error) {
      setProblem(problemOf(error))
    } finally {
      sending.current = false
      setBusy(false)
    }
  }
  const again = () => {
    setTyped('')
    setAsked(null)
    setState(null)
    setProblem(null)
  }

  let body
  let actions
  if (connected) {
    body = (
      <div className="space-y-3" data-testid="device-connected">
        <p role="status" className="flex items-center gap-2 text-base font-semibold text-accent">
          <Check size={18} aria-hidden /> {t('deviceCode.connected', { name: state?.device?.name ?? asked?.device_name ?? '' })}
        </p>
        <p className="text-sm text-ink-2">{t('deviceCode.connectedHint')}</p>
      </div>
    )
    actions = (
      <Button primary onClick={() => onClose()}>
        {t('common.done')}
      </Button>
    )
  } else if (expired) {
    body = (
      <p role="status" className="text-sm text-ink-2" data-testid="device-expired">
        {t('deviceCode.expired')}
      </p>
    )
    actions = (
      <>
        <Button primary onClick={again}>
          {t('deviceCode.other')}
        </Button>
        <Button onClick={() => onClose()}>{t('common.close')}</Button>
      </>
    )
  } else if (waiting && asked !== null) {
    body = (
      <p role="status" className="text-sm text-ink-2" data-testid="device-waiting">
        {t('deviceCode.waiting', { name: asked.device_name })}
      </p>
    )
    actions = (
      <>
        <Button
          onClick={() =>
            ask({
              title: t('confirm.deviceRequest.title'),
              text: t('confirm.deviceRequest.text'),
              confirm: t('confirm.deviceRequest.confirm'),
              danger: false,
              run: async () => {
                await devicesApi.withdrawRequest(asked.id)
                onClose()
              },
            })
          }
        >
          {t('deviceCode.withdraw')}
        </Button>
        <Button onClick={() => onClose()}>{t('common.close')}</Button>
      </>
    )
  } else if (asked !== null) {
    body = (
      <div className="space-y-4" data-testid="device-ask">
        <div>
          <p className="text-sm text-muted">{t('deviceCode.device')}</p>
          {/* Text from outside, and nothing the page vouches for: in quotation marks, the size of the text around it, one
              line that is cut off (never a heading, never over the words below it). */}
          <p
            className="overflow-hidden text-base whitespace-nowrap text-ellipsis text-ink"
            dir="auto"
            title={asked.device_name}
            data-testid="device-name"
          >
            {t('deviceCode.nameQuoted', { name: asked.device_name })}
          </p>
          <p className="text-xs text-muted">{minutesAgo < 1 ? t('deviceCode.askedNow') : t('deviceCode.askedAgo', { count: minutesAgo })}</p>
        </div>
        <p className="text-base font-semibold">{t('deviceCode.ask')}</p>
        <p className="text-sm text-ink-2">{t('deviceCode.what')}</p>
        <p role="note" className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-ink-2" data-testid="device-warning">
          {t('deviceCode.warning')}
        </p>
        <Feedback problem={problem?.code ?? null} values={problem?.values} />
      </div>
    )
    actions = (
      <>
        <Button primary busy={busy} onClick={() => void connect()}>
          {t('deviceCode.connect')}
        </Button>
        <Button onClick={() => onClose()}>{t('common.cancel')}</Button>
      </>
    )
  } else {
    body = (
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          void lookUp()
        }}
      >
        <Input
          label={t('deviceCode.field')}
          value={typed}
          onChange={setTyped}
          placeholder={t('deviceCode.placeholder')}
          maxLength={32}
          autoFocus
          mono
        />
        <p className="text-sm text-ink-2" data-testid="device-first-warning">
          {t('deviceCode.warningFirst')}
        </p>
        <Feedback problem={problem?.code ?? null} values={problem?.values} />
      </form>
    )
    actions = (
      <>
        <Button primary busy={busy} disabled={typed.trim() === ''} onClick={() => void lookUp()}>
          {t('deviceCode.next')}
        </Button>
        <Button onClick={() => onClose()}>{t('common.cancel')}</Button>
      </>
    )
  }

  return (
    <Dialog title={t('deviceCode.title')} sub={asked === null ? t('deviceCode.sub') : undefined} onClose={onClose}>
      <div className="space-y-5">
        <div className="min-w-0">{body}</div>
        <div className="flex flex-wrap justify-end gap-2">{actions}</div>
      </div>
      {dialog}
    </Dialog>
  )
}
