/**
 * A look into the files of a download that waits for an answer, under "Needs you": read the first pages of the e-book, hear
 * into the recording, and see what the files say about themselves. The same three things as on the list of books the matching
 * is unsure about, for files that still lie in the download folder. Nothing is saved, and nothing is filed by looking.
 */
import { BookOpen, FileText, Headphones } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type FileFacts } from '../../api/client'
import { Dialog } from '../Dialog'
import { FilePanel } from './FileFacts'
import { PeekBook } from './PeekBook'
import { PeekListen } from './PeekListen'

export function DownloadLook({ downloadId, kind, title }: { downloadId: number; kind: 'ebook' | 'audio'; title: string }) {
  const { t } = useTranslation()
  const [reading, setReading] = useState(false)
  const [listening, setListening] = useState(false)
  const [factsOpen, setFactsOpen] = useState(false)
  const [facts, setFacts] = useState<FileFacts | null>(null)
  const [gone, setGone] = useState(false)

  useEffect(() => {
    if (!factsOpen) return
    let stale = false
    procureApi.look(downloadId).then(
      (look) => {
        if (stale) return
        setGone(false)
        setFacts({ entry_id: look.download_id, edition_id: 0, kind: look.kind, folder: look.folder, files: look.files, says: look.says, cover_url: null, proposals: [] })
      },
      (error) => !stale && setGone(error instanceof ApiError && (error.status === 404 || error.status === 409)),
    )
    return () => {
      stale = true
    }
  }, [factsOpen, downloadId])

  return (
    <div data-testid="download-look">
      <div className="review-look" data-testid="review-look">
        {kind === 'ebook' ? (
          <button type="button" className="peek-btn" onClick={() => setReading(true)} data-testid="look-read">
            <BookOpen size={15} aria-hidden /> {t('review.peek.read')}
          </button>
        ) : (
          <button type="button" className="peek-btn" aria-expanded={listening} onClick={() => setListening((now) => !now)} data-testid="look-listen">
            <Headphones size={15} aria-hidden /> {t('review.peek.listen')}
          </button>
        )}
        <button type="button" className="peek-btn" aria-expanded={factsOpen} onClick={() => setFactsOpen((now) => !now)} data-testid="look-facts">
          <FileText size={15} aria-hidden /> {t('review.facts.open')}
        </button>
      </div>
      {factsOpen && gone && (
        <p className="small-note" role="alert" data-testid="look-gone">
          {t('acquire.needs.files.gone')}
        </p>
      )}
      {listening && (
        <Dialog title={title} sub={t('review.peek.listen')} onClose={() => setListening(false)} above>
          <PeekListen downloadId={downloadId} title={title} />
        </Dialog>
      )}
      {factsOpen && facts && !gone && (
        <div className="mt-3" data-testid="look-facts-panel">
          <FilePanel facts={facts} top={t('acquire.needs.files.top')} />
        </div>
      )}
      {reading && <PeekBook downloadId={downloadId} title={title} onClose={() => setReading(false)} />}
    </div>
  )
}
