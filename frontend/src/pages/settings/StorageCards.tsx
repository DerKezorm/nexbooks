/**
 * Settings, Backup: the storage card. What the data folder holds (the database and the backups), the free room, and
 * a warning when the data folder lies on a network share (SQLite can lose data there). The books are not counted:
 * they lie in the folders of the libraries, which nexbooks only reads.
 */
import { AlertTriangle, HardDrive } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { formatBytes } from '../../lib/bytes'
import { Card } from './ui'

type Where = { kind: string; fstype: string; network: boolean }
type Overview = {
  database: { bytes: number; where: Where }
  backups: { bytes: number; files: number; complete: boolean }
  free_bytes: number
  total_bytes: number
  data: { where: Where }
}

export function StorageCard() {
  const { t, i18n } = useTranslation()
  const [found, setFound] = useState<Overview | null>(null)
  useEffect(() => {
    api<Overview>('/api/v1/storage').then(setFound, () => setFound(null))
  }, [])
  if (!found) return null
  const size = (bytes: number) => formatBytes(bytes, i18n.language)
  const network = found.database.where.network || found.data.where.network
  return (
    <Card id="storage" icon={HardDrive} title={t('storage.title')} text={t('storage.text')}>
      {network && (
        <div className="flex items-start gap-3 rounded-xl border border-warn/40 bg-warn-soft px-4 py-3 text-sm" role="alert" data-testid="storage-network">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          <span>
            <span className="font-semibold">{t('storage.network')}</span>
            <span className="block text-xs text-ink-2">{t('storage.networkText')}</span>
          </span>
        </div>
      )}
      <dl className="grid grid-cols-[12rem_1fr] gap-y-2 text-sm" data-testid="storage-numbers">
        <dt className="text-muted">{t('storage.database')}</dt>
        <dd className="font-semibold">{size(found.database.bytes)}</dd>
        <dt className="text-muted">{t('storage.backups')}</dt>
        <dd className="font-semibold">
          {size(found.backups.bytes)}
          {!found.backups.complete && ` ${t('storage.partial')}`}
        </dd>
        <dt className="text-muted">{t('storage.free')}</dt>
        <dd className="font-semibold">{t('storage.freeOf', { free: size(found.free_bytes), total: size(found.total_bytes) })}</dd>
      </dl>
      <p className="text-xs text-muted">{t('storage.booksNote')}</p>
    </Card>
  )
}
