/**
 * "Mit anderem Autor zusammenführen": on the page of an author, two names that are one person. The dialog looks for the
 * other author by name among the authors the person sees, then asks which of the two names stays and what happens. Nothing
 * is sent before the click on the last button (`POST /people/{id}/merge`), and the server checks the right again. A
 * split on the page of the author takes it back.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, authorsApi, worksApi, type AuthorCard, type PersonOut } from '../api/client'
import { errorText } from '../lib/errors'
import { iso } from '../lib/say'
import { Dialog } from './Dialog'

/** How long the search waits for the next letter (milliseconds). */
export const MERGE_SEARCH_MS = 250

const BUTTON = 'inline-flex h-10 items-center justify-center rounded-full px-4 text-sm font-semibold transition disabled:opacity-50'
const GHOST = `${BUTTON} border border-line text-ink-2 hover:bg-sheet-2`
const MAIN = `${BUTTON} bg-accent text-accent-ink hover:brightness-105`

export function MergeAuthorDialog({ person, onClose, onMerged }: { person: PersonOut; onClose: () => void; onMerged: (kept: PersonOut, other: string) => void }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<AuthorCard[] | null>(null)
  const [other, setOther] = useState<AuthorCard | null>(null)
  const [keep, setKeep] = useState<'this' | 'other'>('this')
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // One click, one request.
  const sent = useRef(false)

  useEffect(() => {
    const wanted = query.trim()
    if (wanted.length < 2) {
      setFound(null)
      return
    }
    const control = new AbortController()
    const timer = window.setTimeout(() => {
      authorsApi.list({ q: wanted, sort: 'name', per_page: 12 }, control.signal).then(
        (page) => {
          setFound(page.items.filter((item) => item.id !== person.id))
          setFailure(null)
        },
        (error: unknown) => {
          if (control.signal.aborted) return
          setFailure(error instanceof ApiError ? error.code : 'internal_error')
        },
      )
    }, MERGE_SEARCH_MS)
    return () => {
      window.clearTimeout(timer)
      control.abort()
    }
  }, [query, person.id])

  const join = async () => {
    if (!other || sent.current) return
    sent.current = true
    setBusy(true)
    setFailure(null)
    try {
      onMerged(await worksApi.mergePerson(person.id, other.id, keep), keep === 'this' ? other.name : person.name)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.code : 'internal_error')
      sent.current = false
      setBusy(false)
    }
  }

  return (
    <Dialog title={t('person.mergeTitle')} sub={other ? undefined : t('person.mergeSub', { name: person.name })} onClose={onClose} above>
      <div className="space-y-4 text-sm" data-testid="merge-author">
        {failure && (
          <p role="alert" className="text-bad">
            {errorText(failure)}
          </p>
        )}
        {!other ? (
          <>
            <label className="block font-semibold">
              {t('person.mergeSearch')}
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} className="input mt-1" autoComplete="off" data-autofocus="" />
            </label>
            {found === null ? (
              <p className="text-muted">{t('person.mergeHint')}</p>
            ) : found.length === 0 ? (
              <p className="text-muted" data-testid="merge-none">
                {t('person.mergeNone')}
              </p>
            ) : (
              <ul className="space-y-1" data-testid="merge-found">
                {found.map((item) => (
                  <li key={item.id}>
                    <button type="button" className="flex w-full items-baseline justify-between gap-3 rounded-xl border border-line px-3 py-2 text-left hover:bg-sheet-2" onClick={() => setOther(item)}>
                      <span className="font-semibold" dir="auto">
                        {item.name}
                      </span>
                      <span className="text-muted">{t('person.mergeWorks', { count: item.works })}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex justify-end">
              <button type="button" className={GHOST} onClick={onClose}>
                {t('common.cancel')}
              </button>
            </div>
          </>
        ) : (
          <>
            <fieldset className="space-y-2">
              <legend className="font-semibold">{t('person.mergeWhich')}</legend>
              {(['this', 'other'] as const).map((side) => {
                const name = side === 'this' ? person.name : other.name
                return (
                  <label key={side} className="flex items-center gap-2 rounded-xl border border-line px-3 py-2">
                    <input type="radio" name="keep" value={side} checked={keep === side} onChange={() => setKeep(side)} />
                    <span dir="auto">{t('person.mergeKeep', { name: iso(name) })}</span>
                  </label>
                )
              })}
            </fieldset>
            <p className="text-ink-2">{t('person.mergeText', { name: iso(person.name), other: iso(other.name) })}</p>
            <div className="flex justify-end gap-2">
              <button type="button" className={GHOST} onClick={() => setOther(null)} disabled={busy}>
                {t('person.mergeBack')}
              </button>
              <button type="button" className={MAIN} onClick={() => void join()} disabled={busy}>
                {busy ? '…' : t('person.mergeConfirm')}
              </button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}
