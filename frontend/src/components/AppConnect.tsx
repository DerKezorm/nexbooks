/**
 * "Connect the iPhone app" (My account, Devices and apps): a QR code for the camera of the phone and, beside it, the code to
 * type in the app by hand. The page asks the server every two seconds whether the app took it and then says "Connected:
 * <device>". A code nobody used is withdrawn when the window goes (a screenshot of a code left lying around is worth
 * nothing then); "Withdraw" does the same on purpose and asks first. The secret of the code is never written into this
 * page: the QR code is drawn by the server and the page shows only its picture and the short code. On the phone itself
 * (where a QR code cannot be scanned) a button opens the link of the code in the app, from the page's memory, and
 * "Copy code" puts the short code on the clipboard; if the address is one the phone cannot reach, the dialog says so.
 */
import { Check } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, devicesApi, type AppPairing, type AppPairingState } from '../api/client'
import { onApplePhone, reachesOnlyThisDevice } from '../lib/appLink'
import { copyText } from '../lib/copy'
import { Button, Feedback } from '../pages/settings/ui'
import { Dialog } from './Dialog'
import { useConfirm } from './useConfirm'

/** How often the page looks whether the app took the code. */
const POLL_MS = 2000
/** What a code lives (the server says the same, ten minutes); the server's answer decides, this only counts down. */
const LIFETIME_MS = 10 * 60 * 1000

export function AppConnect({ onClose }: { onClose: (connected: boolean) => void }) {
  const { t } = useTranslation()
  const [pairing, setPairing] = useState<AppPairing | null>(null)
  const [state, setState] = useState<AppPairingState | null>(null)
  const [problem, setProblem] = useState<{ code: string; values: Record<string, unknown> } | null>(null)
  const [round, setRound] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [deadline, setDeadline] = useState(0)
  const [copied, setCopied] = useState<'done' | 'failed' | null>(null)
  const open = useRef<string | null>(null)
  // The page is about to hand over to the app ("Open in the app"): going into the background then is no reason to withdraw.
  const handingOver = useRef(false)
  const phone = useRef(onApplePhone()).current
  const { ask, dialog } = useConfirm()
  const connected = state?.state === 'connected'

  useEffect(() => {
    let alive = true
    // A code of the round before that nobody took (it ran out, or the server said no) does not stay.
    const before = open.current
    open.current = null
    if (before !== null) void devicesApi.unpair(before).catch(() => undefined)
    setPairing(null)
    setState(null)
    setProblem(null)
    setCopied(null)
    handingOver.current = false
    devicesApi.pair(window.location.origin).then(
      (made) => {
        if (!alive) {
          // The window went while the code was being made: it must not stay behind.
          void devicesApi.unpair(made.id).catch(() => undefined)
          return
        }
        open.current = made.id
        setPairing(made)
        setNow(Date.now())
        setDeadline(Date.now() + LIFETIME_MS)
      },
      (error: unknown) => {
        if (alive) setProblem(error instanceof ApiError ? { code: error.code, values: error.values } : { code: 'internal_error', values: {} })
      },
    )
    return () => {
      alive = false
    }
  }, [round])

  useEffect(() => {
    if (pairing === null || connected || state?.state === 'expired') return
    const timer = window.setInterval(() => {
      setNow(Date.now())
      devicesApi.pairing(pairing.id).then(setState, (error: unknown) => {
        // Gone (404) is as good as run out: the code is not there to scan any more.
        if (error instanceof ApiError && error.status === 404) setState({ state: 'expired', device: null })
      })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [pairing, connected, state?.state])

  useEffect(() => {
    if (connected) open.current = null
  }, [connected])

  // A code nobody took does not stay: not when the window closes, and not when the page does.
  useEffect(() => {
    const leave = () => {
      const id = open.current
      // Opening the app can hide this page for a moment (the phone switches over); the code must still be there when the
      // app asks for it. It goes when the window closes (below) or runs out.
      if (id !== null && !handingOver.current) void devicesApi.unpair(id, true).catch(() => undefined)
    }
    window.addEventListener('pagehide', leave)
    return () => {
      window.removeEventListener('pagehide', leave)
      const id = open.current
      if (id !== null) void devicesApi.unpair(id).catch(() => undefined)
    }
  }, [])

  const copyCode = () => {
    if (pairing === null) return
    void copyText(pairing.code).then((done) => setCopied(done ? 'done' : 'failed'))
  }
  const openInApp = () => {
    if (pairing === null || pairing.link === '') return
    handingOver.current = true
    // A link made for the moment and thrown away again: the page's markup never holds it (it carries the secret).
    const opener = document.createElement('a')
    opener.href = pairing.link
    opener.rel = 'noopener'
    document.body.appendChild(opener)
    opener.click()
    opener.remove()
  }

  const minutes = pairing === null ? 0 : Math.max(0, Math.ceil((deadline - now) / 60000))
  const expired = !connected && pairing !== null && (state?.state === 'expired' || minutes === 0)

  let body
  if (problem) {
    body = <Feedback problem={problem.code} values={problem.values} />
  } else if (pairing === null) {
    body = (
      <p role="status" className="text-sm text-muted">
        {t('appConnect.making')}
      </p>
    )
  } else if (connected) {
    body = (
      <div className="space-y-3" data-testid="app-connected">
        <p role="status" className="flex items-center gap-2 text-base font-semibold text-accent">
          <Check size={18} aria-hidden /> {t('appConnect.connected', { name: state?.device?.name ?? '' })}
        </p>
        <p className="text-sm text-ink-2">{t('appConnect.connectedHint')}</p>
      </div>
    )
  } else if (expired) {
    body = (
      <p role="status" className="text-sm text-ink-2" data-testid="app-expired">
        {t('appConnect.expired')}
      </p>
    )
  } else {
    body = (
      <div className="space-y-4">
        <div className="grid items-center gap-5 sm:grid-cols-[14rem_1fr]">
          <img
            src={'data:image/svg+xml;utf8,' + encodeURIComponent(pairing.qr_svg)}
            alt={t('appConnect.qr')}
            className="mx-auto h-56 w-56 rounded-xl bg-white p-2"
          />
          <div className="space-y-2 text-center sm:text-left">
            <p className="text-sm text-ink-2">{t('appConnect.orType')}</p>
            <p className="font-mono text-2xl font-semibold tracking-[0.2em] select-all" data-testid="app-code">
              {pairing.code}
            </p>
            <p className="text-sm text-muted" role="status" data-testid="app-left">
              {t('appConnect.left', { count: minutes })}
            </p>
            <div className="flex flex-wrap justify-center gap-2 pt-1 sm:justify-start">
              {phone && pairing.link !== '' && (
                <Button primary onClick={openInApp}>
                  {t('appConnect.openApp')}
                </Button>
              )}
              <Button onClick={copyCode}>{t('appConnect.copyCode')}</Button>
            </div>
            {copied !== null && (
              <p role="status" className="text-xs text-muted" data-testid="app-copied">
                {copied === 'done' ? t('appConnect.copied') : t('appConnect.copyFailed')}
              </p>
            )}
            {phone && pairing.link !== '' && <p className="text-xs text-muted">{t('appConnect.openHint')}</p>}
          </div>
        </div>
        <p className="text-xs break-words text-muted">{t('appConnect.address', { address: pairing.address })}</p>
        {reachesOnlyThisDevice(pairing.address) && (
          <p role="note" className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-ink-2" data-testid="app-local">
            {t('appConnect.local')}
          </p>
        )}
        <p className="text-xs text-muted">{t('appConnect.closeHint')}</p>
      </div>
    )
  }

  return (
    <Dialog title={t('appConnect.title')} sub={connected || expired || problem ? undefined : t('appConnect.sub')} onClose={() => onClose(connected)}>
      <div className="space-y-5">
        <div className="min-w-0">{body}</div>
        <div className="flex flex-wrap justify-end gap-2">
          {(expired || problem) && (
            <Button primary onClick={() => setRound((value) => value + 1)}>
              {t('appConnect.again')}
            </Button>
          )}
          {!connected && !expired && !problem && pairing !== null && (
            <Button
              onClick={() =>
                ask({
                  title: t('confirm.pairing.title'),
                  text: t('confirm.pairing.text'),
                  confirm: t('confirm.pairing.confirm'),
                  danger: false,
                  run: async () => {
                    const id = pairing.id
                    await devicesApi.unpair(id)
                    open.current = null
                    onClose(false)
                  },
                })
              }
            >
              {t('appConnect.withdraw')}
            </Button>
          )}
          <Button primary={connected} onClick={() => onClose(connected)}>
            {connected ? t('common.done') : t('common.close')}
          </Button>
        </div>
      </div>
      {dialog}
    </Dialog>
  )
}
