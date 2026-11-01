/**
 * "Beschaffen": the fourth area of the head, for who may ask for books in a
 * library. Five lists under the tabs Gesucht, Läuft, Braucht dich, Verlauf and Sperrliste (the address says which:
 * `?reiter=`), notes above them when something keeps the work from going on (no indexer, the automatic work switched off,
 * a download program that does not answer), and the state of the automatic work at the top right.
 */
import { AlertTriangle, Pause } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useSearchParams } from 'react-router-dom'

import { procureApi } from '../api/client'
import { ReleasesDialog } from '../components/procure/ReleasesDialog'
import { PageHead } from '../components/PageHead'
import { useAuth } from '../state/auth'
import { useProcure } from '../state/procure'
import { Blocklist, History } from './acquire/History'
import { Needs, type ReleasesOf } from './acquire/Needs'
import { Running } from './acquire/Running'
import { Wanted } from './acquire/Wanted'
import { useLoaded } from './acquire/hooks'

const TABS = ['wanted', 'running', 'needs', 'history', 'blocklist'] as const
type Tab = (typeof TABS)[number]

export function AcquirePage() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const operator = me?.role === 'operator'
  const procure = useProcure()
  const [params] = useSearchParams()
  const asked = params.get('reiter')
  const tab: Tab = (TABS as readonly string[]).includes(asked ?? '') ? (asked as Tab) : 'wanted'
  const problems = useLoaded(() => procureApi.problems(), [])
  const [releases, setReleases] = useState<ReleasesOf | null>(null)
  const { reload } = procure

  // The page reads the lists again at once after an action; the provider does it for the numbers and the first two lists.
  const changed = useCallback(async () => {
    await Promise.all([reload(), problems.reload()])
  }, [reload, problems])
  useEffect(() => {
    void reload()
  }, [reload, tab])

  const counts = procure.counts
  // A person who may not ask for books has no such area; one who just lost the right goes back.
  if (counts !== null && !counts.allowed) return <Navigate to="/" replace />
  if (counts === null) return null

  const needs = problems.value ? problems.value.problems.length + problems.value.foreign.length + (problems.value.notes?.length ?? 0) : counts.needs
  const link = (id: Tab, label: string, n?: number, hot?: boolean) => (
    <Link key={id} to={id === 'wanted' ? '/acquire' : `/acquire?reiter=${id}`} className={tab === id ? 'on' : ''} aria-current={tab === id ? 'page' : undefined} data-sub={id}>
      {label}
      {n ? <small className={hot ? 'hot' : ''}>{n}</small> : null}
    </Link>
  )
  const seeking = procure.wishes.filter((wish) => wish.state !== 'loading').length

  return (
    <div className="view acq" data-testid="acquire">
      <PageHead title={t('acquire.title')} text={t('acquire.lead')}>
        <div className="bes-meta">
          <span className={`autochip ${counts.automatic ? 'on' : ''}`} data-testid="autochip">
            <i />
            {counts.automatic ? t('acquire.autoOn') : t('acquire.autoOff')}
          </span>
        </div>
      </PageHead>
      <nav className="bes-tabs" aria-label={t('acquire.tabs.aria')}>
        {link('wanted', t('acquire.tabs.wanted'), seeking)}
        {link('running', t('acquire.tabs.running'), procure.running.length)}
        {link('needs', t('acquire.tabs.needs'), needs, true)}
        {link('history', t('acquire.tabs.history'))}
        {link('blocklist', t('acquire.tabs.blocklist'), counts.blocklist)}
      </nav>
      {counts.indexers === 0 && (
        <div className="note warn" data-testid="note-no-indexer">
          <AlertTriangle aria-hidden className="ic" />
          <p>
            <b>{t('acquire.notes.noIndexer.title')}</b> {t('acquire.notes.noIndexer.text')}
          </p>
          {operator && (
            <Link className="nlink" to="/settings?tab=procure&sub=indexers">
              {t('acquire.notes.noIndexer.link')}
            </Link>
          )}
        </div>
      )}
      {counts.indexers > 0 && !counts.automatic && (
        <div className="note" data-testid="note-manual">
          <Pause aria-hidden className="ic" />
          <p>
            <b>{t('acquire.notes.manual.title')}</b> {t('acquire.notes.manual.text')}
          </p>
          {operator && (
            <Link className="nlink" to="/settings?tab=procure&sub=automation">
              {t('acquire.notes.manual.link')}
            </Link>
          )}
        </div>
      )}
      {counts.clients_down.map((client) => (
        <div className="note bad" key={client.id} data-testid="note-down">
          <AlertTriangle aria-hidden className="ic" />
          <p>
            <b>{t('acquire.notes.down.title', { name: client.name })}</b> {t('acquire.notes.down.text')}
          </p>
          {operator && (
            <Link className="nlink" to="/settings?tab=procure&sub=clients">
              {t('acquire.notes.down.link')}
            </Link>
          )}
        </div>
      ))}
      {tab === 'wanted' && <Wanted wishes={procure.wishes} counts={counts} onChanged={changed} />}
      {tab === 'running' && <Running downloads={procure.running} onChanged={changed} />}
      {tab === 'needs' && <Needs problems={problems.value?.problems ?? []} foreign={problems.value?.foreign ?? []} notes={problems.value?.notes ?? []} onChanged={changed} onReleases={setReleases} />}
      {tab === 'history' && <History />}
      {tab === 'blocklist' && <Blocklist onChanged={changed} />}
      {releases && (
        <ReleasesDialog
          workId={releases.book.work_id ?? undefined}
          book={releases.book.work_id == null ? (releases.book.book ?? undefined) : undefined}
          title={releases.book.title}
          author={releases.book.authors[0] ?? ''}
          kind={releases.kind}
          onClose={() => setReleases(null)}
          onLoaded={() => void changed()}
        />
      )}
    </div>
  )
}
