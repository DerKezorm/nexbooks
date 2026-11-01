/** "Läuft": the downloads that run, with the program, the progress, the time left
 * and, once a book is filed and its torrent seeds, how far the seeding is. Cancelling asks first and offers to block the
 * release and look for another. */
import { Magnet, Newspaper, Upload } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { procureApi, type RunningOut, type RunningPhase } from '../../api/client'
import { useConfirm } from '../../components/useConfirm'
import { sizeText } from '../../lib/releases'
import { iso } from '../../lib/say'
import { leftText } from '../../lib/procure'
import { CancelDialog } from './dialogs'
import { useDo } from './hooks'
import { BookLink, KindChip, MiniCover } from './bits'

const TONE: Record<RunningPhase, string> = { sending: 'quiet', unsure: 'quiet', queued: 'quiet', downloading: 'info', paused: 'warn', unpacking: 'info', waiting: 'info', checking: 'info', filing: 'info', seeding: 'ok' }
const STRIPE: RunningPhase[] = ['sending', 'unsure', 'unpacking', 'waiting', 'checking', 'filing']

export function Running({ downloads, onChanged }: { downloads: RunningOut[]; onChanged: () => Promise<void> }) {
  const { t, i18n } = useTranslation()
  const [mine, setMine] = useState(false)
  const [cancelling, setCancelling] = useState<RunningOut | null>(null)
  const confirm = useConfirm()
  const doing = useDo(onChanged)
  const shown = downloads.filter((item) => !mine || item.mine)

  const stopSeeding = (item: RunningOut) =>
    confirm.ask({
      title: t('acquire.running.stop.title'),
      text: t('acquire.running.stop.text'),
      confirm: t('acquire.running.stop.confirm'),
      danger: false,
      run: async () => {
        await procureApi.stopSeeding(item.id)
        await onChanged()
      },
    })

  if (downloads.length === 0) {
    return (
      <p className="empty" data-testid="running-empty">
        {t('acquire.running.empty')}
      </p>
    )
  }
  return (
    <>
      <div className="bes-top">
        <p className="bes-count">{t('acquire.running.count', { count: shown.length })}</p>
        <div className="seg small bes-seg" role="group" aria-label={t('acquire.scope.aria')}>
          <button type="button" className={mine ? '' : 'on'} onClick={() => setMine(false)} data-mine="0">
            {t('acquire.scope.all')}
          </button>
          <button type="button" className={mine ? 'on' : ''} onClick={() => setMine(true)} data-mine="1">
            {t('acquire.scope.mine')}
          </button>
        </div>
      </div>
      {doing.problem && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="rqs" data-testid="running">
        {shown.map((item) => {
          const seeding = item.phase === 'seeding'
          const stripe = STRIPE.includes(item.phase)
          const bar = seeding ? Math.min(100, item.goal_ratio ? ((item.seed_ratio ?? 0) / item.goal_ratio) * 100 : 100) : stripe ? 100 : (item.progress ?? 0)
          const percent = item.phase === 'queued' || item.phase === 'sending' || item.phase === 'unsure' ? t('acquire.running.waits') : item.progress != null ? `${Math.round(item.progress)} %` : ''
          const eta = item.phase === 'downloading' && item.remaining_seconds ? t('acquire.running.left', { time: leftText(item.remaining_seconds, t('acquire.running.h'), t('acquire.running.min'), t('acquire.running.sec')) }) : ''
          const ratio = (value: number | null) => (value == null ? '·' : value.toLocaleString(i18n.language, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
          return (
            <article className="rq dl" key={item.id} data-download={item.id} data-phase={item.phase}>
              <MiniCover book={item.book} />
              <div className="rq-main">
                <h3 dir="auto">
                  <BookLink book={item.book} />
                </h3>
                <p className="rq-by" dir="auto">
                  {item.book.authors.join(', ')}
                </p>
                <p className="rel" dir="auto">
                  {item.release}
                </p>
                <div className="chips">
                  <KindChip kind={item.kind} language={item.language} />
                </div>
              </div>
              <div className="rq-prog">
                <div className={`pg ${stripe ? 'stripe' : ''} ${seeding ? 'seed' : ''} ${item.phase === 'paused' ? 'warn' : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(bar)}>
                  <i style={{ width: `${bar}%` }} />
                </div>
                <div className="pgrow">
                  <span className={`phase p-${TONE[item.phase]}`}>{t(`acquire.running.phase.${item.phase}`)}</span>
                  {seeding ? (
                    <span>{t('acquire.running.ratio', { ratio: ratio(item.seed_ratio), goal: item.goal_ratio ? ratio(item.goal_ratio) : '·' })}</span>
                  ) : (
                    <>
                      <span data-pct>{percent}</span>
                      <span data-eta>{eta}</span>
                    </>
                  )}
                </div>
                <p className="meta">
                  <span>
                    {item.protocol === 'torrent' ? <Magnet aria-hidden className="ic" /> : item.protocol === 'upload' ? <Upload aria-hidden className="ic" /> : <Newspaper aria-hidden className="ic" />}
                    {item.protocol === 'upload' ? t('acquire.running.uploaded') : iso(item.client)}
                  </span>
                  <span>{iso(item.indexer)}</span>
                  {item.size != null && <span>{sizeText(item.size, i18n.language)}</span>}
                  {item.phase === 'downloading' && item.speed != null && (
                    <span data-speed>{t('acquire.running.speed', { speed: (item.speed / 1_000_000).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) })}</span>
                  )}
                  {item.stalled && <span>{t('acquire.running.stalled')}</span>}
                </p>
                {item.phase === 'waiting' && <p className="stepnote">{t('acquire.running.step.waiting', { library: item.library?.name ?? '' })}</p>}
                {item.phase === 'checking' && <p className="stepnote">{t(item.kind === 'audio' ? 'acquire.running.step.checkingAudio' : 'acquire.running.step.checkingEbook')}</p>}
                {item.phase === 'filing' && <p className="stepnote">{t('acquire.running.step.filing', { library: item.library?.name ?? '' })}</p>}
                {item.phase === 'unpacking' && <p className="stepnote">{t('acquire.running.step.unpacking')}</p>}
                {item.phase === 'unsure' && <p className="stepnote">{t('acquire.running.step.unsure')}</p>}
              </div>
              <div className="rq-act">
                {seeding ? (
                  <button type="button" className="bbtn small" onClick={() => stopSeeding(item)} data-act="stopseed">
                    {t('acquire.running.stop.button')}
                  </button>
                ) : (
                  <button type="button" className="bbtn small" onClick={() => setCancelling(item)} data-act="cancel">
                    {t('acquire.running.cancel.button')}
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </div>
      {cancelling && <CancelDialog item={cancelling} onClose={() => setCancelling(null)} onDone={() => void onChanged()} />}
      {confirm.dialog}
    </>
  )
}
