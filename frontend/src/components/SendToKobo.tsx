/**
 * "To the Kobo" on the card of an e-book that has an EPUB without copy protection: puts the book on the person's list for
 * the Kobo, and takes it off again. The Kobos of the account fetch the list at their next sync (nexbooks never pushes
 * anything to a device), so the button only says what will happen and, while the person has no Kobo connected, where to
 * set one up. Taking a book off asks first: the Kobo deletes it from the device at its next sync.
 *
 * Nothing is shown while the operator has switched e-readers off, or while the server has not answered.
 */
import { TabletSmartphone } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, ereadersApi, type FileOut } from '../api/client'
import { errorText } from '../lib/errors'
import { sendableFile } from '../lib/kindle'
import { iso } from '../lib/say'
import { forgetEreaders, useEreaders } from '../state/ereaders'
import { useConfirm } from './useConfirm'

export function SendToKobo({ editionId, title, files }: { editionId: number; title: string; files: FileOut[] }) {
  const { t } = useTranslation()
  const state = useEreaders()
  // Null until the person has pressed the button: the list the server told is the truth until then.
  const [listed, setListed] = useState<boolean | null>(null)
  const [said, setSaid] = useState<{ text: string; tone: 'ok' | 'bad'; setup?: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const { ask, dialog } = useConfirm()
  if (!sendableFile(files) || !state?.allowed) return null
  const on = listed ?? Boolean(state.kobo?.includes(editionId))
  const connected = state.links.some((link) => link.kind === 'kobo' && !link.blocked)

  const fail = (error: unknown) =>
    setSaid({ text: errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}), tone: 'bad' })

  const add = async () => {
    setSaid(null)
    setBusy(true)
    try {
      await ereadersApi.listForKobo(editionId)
      setListed(true)
      forgetEreaders()
      setSaid(connected ? { text: t('kobo.added'), tone: 'ok' } : { text: t('kobo.noConnection'), tone: 'bad', setup: true })
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }

  const take = () =>
    ask({
      title: t('confirm.koboRemove.title'),
      text: t('confirm.koboRemove.text', { title: iso(title) }),
      confirm: t('confirm.koboRemove.confirm'),
      run: async () => {
        setSaid(null)
        try {
          await ereadersApi.unlistForKobo(editionId)
          setListed(false)
          forgetEreaders()
          setSaid({ text: t('kobo.removed'), tone: 'ok' })
        } catch (error) {
          fail(error)
        }
      },
    })

  return (
    <>
      <button type="button" className="tw-upgrade" onClick={() => (on ? take() : void add())} disabled={busy} data-testid="send-to-kobo" data-listed={on}>
        <TabletSmartphone size={14} aria-hidden />
        {on ? t('kobo.remove') : t('kobo.button')}
      </button>
      {said && (
        <div className={said.tone === 'bad' ? 'tw-unproven' : 'tw-pos'} role="status" data-testid="kobo-said">
          {said.text}
          {said.setup && (
            <>
              {' '}
              <Link to="/account?tab=devices" className="underline" data-testid="kobo-setup">
                {t('kobo.setUp')}
              </Link>
            </>
          )}
        </div>
      )}
      {dialog}
    </>
  )
}
