/**
 * "Send to Kindle" on the card of an e-book that has an EPUB without copy protection. What it does depends on what the
 * server can do:
 *
 * - the server can mail and the person has set the address of the Kindle: the book goes by mail in the background;
 * - the server can mail and there is no address yet: it says where to set it;
 * - the server cannot mail: the book is downloaded and the page "Send to Kindle" of Amazon opens in a new tab, where the
 *   person drops the file (a short note says so). The tab is opened first, in the click itself, or the browser blocks it.
 *
 * Nothing is shown while the operator has switched e-readers off, or while the server has not answered.
 */
import { Send } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, downloadUrl, ereadersApi, type FileOut } from '../api/client'
import { errorText } from '../lib/errors'
import { AMAZON_SEND, sendableFile } from '../lib/kindle'
import { useEreaders } from '../state/ereaders'

export function SendToKindle({ files }: { files: FileOut[] }) {
  const { t } = useTranslation()
  const state = useEreaders()
  const [said, setSaid] = useState<{ text: string; tone: 'ok' | 'bad'; setup?: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const file = sendableFile(files)
  if (!file || !state?.allowed) return null

  const send = async () => {
    setSaid(null)
    if (!state.kindle.mail_ready) {
      // By hand: Amazon's page first (the click opens it), then the file.
      window.open(AMAZON_SEND, '_blank', 'noopener,noreferrer')
      const link = document.createElement('a')
      link.href = downloadUrl(file.id)
      link.download = ''
      document.body.appendChild(link)
      link.click()
      link.remove()
      setSaid({ text: t('kindle.byHand'), tone: 'ok' })
      return
    }
    if (!state.kindle.address) {
      setSaid({ text: t('kindle.noAddress'), tone: 'bad', setup: true })
      return
    }
    setBusy(true)
    try {
      await ereadersApi.sendToKindle(file.id)
      setSaid({ text: t('kindle.sent'), tone: 'ok' })
    } catch (error) {
      setSaid({ text: errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}), tone: 'bad' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" className="tw-upgrade" onClick={() => void send()} disabled={busy} data-testid="send-to-kindle">
        <Send size={14} aria-hidden />
        {t('kindle.button')}
      </button>
      {said && (
        <div className={said.tone === 'bad' ? 'tw-unproven' : 'tw-pos'} role="status" data-testid="kindle-said">
          {said.text}
          {said.setup && (
            <>
              {' '}
              <Link to="/account?tab=devices" className="underline" data-testid="kindle-setup">
                {t('kindle.setUp')}
              </Link>
            </>
          )}
        </div>
      )}
    </>
  )
}
