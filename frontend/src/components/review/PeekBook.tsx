/**
 * A look into a book from the review list: the first pages of the file in the reader's own frame, so that a person who has to
 * say which book a file is can read its title page and its first lines. **Nothing is saved**: no place, no bookmark, no mark that
 * the book was opened (the frame is told where to open and nothing it says is sent to the server). The file is read the usual way
 * (`/files/{id}/content`), so the rights are those of the library.
 */
import { ChevronLeft, ChevronRight, SkipBack } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, lookContentUrl, procureApi, readingApi } from '../../api/client'
import { reducedMotion } from '../../lib/motion'
import { chooseFile, lookOf, MAX_BOOK_MB, PAGE_COLOURS, type BookFormat } from '../../lib/readFrame'
import { storedTheme } from '../../lib/theme'
import { Dialog } from '../Dialog'
import { useBookFrame, type FrameFile } from '../reader/useBookFrame'

const ACCENT = '#8e3b46'
const START = { locator: null, fraction: null }

function Frame({ file, title }: { file: FrameFile; title: string }) {
  const { t } = useTranslation()
  const dark = storedTheme() === 'dark'
  // One object for as long as the colours stay: the frame is told about a new look every time this one changes.
  const look = useMemo(() => ({ ...lookOf({}, dark, ACCENT, !reducedMotion()), accent: ACCENT }), [dark])
  const colours = PAGE_COLOURS[look.theme]
  const [missing, setMissing] = useState(false)
  const { frameRef, state, actions, src, sandbox, attempt } = useBookFrame(file, START, look, {
    onNotice: (kind) => {
      if (kind === 'missing') setMissing(true)
    },
    onKey: (key) => {
      if (key.ctrl || key.alt || key.meta) return
      if (key.key === 'ArrowRight' || key.key === 'PageDown') actions.turn('next')
      else if (key.key === 'ArrowLeft' || key.key === 'PageUp') actions.turn('prev')
    },
  })
  const failed = state.phase === 'failed'
  const percent = Math.round((state.place?.fraction ?? 0) * 100)
  return (
    <div className="peek-book" data-testid="peek-book" data-phase={state.phase}>
      <div
        className="peek-page"
        style={{ '--rd-ground': colours.ground, '--rd-ink': colours.ink, '--rd-muted': colours.muted, '--rd-line': colours.line, '--rd-accent': ACCENT } as React.CSSProperties}
      >
        <iframe key={`${file.id}-${attempt}`} ref={frameRef} className="rd-frame" src={src} sandbox={sandbox} title={t('review.peek.frame', { title })} data-testid="peek-frame" hidden={failed} />
        {state.phase === 'loading' && (
          <p className="rd-loading" role="status" data-testid="peek-loading">
            {t('reader.loading')}
          </p>
        )}
        {failed && (
          <div className="rd-failed" role="alert" data-testid="peek-failed" data-reason={state.reason}>
            <p>{t(`reader.failed.${state.reason ?? 'broken'}`, { limit: MAX_BOOK_MB })}</p>
            {state.reason === 'network' && (
              <div className="rd-failed-go">
                <button type="button" className="peek-btn" onClick={actions.retry}>
                  {t('reader.failed.retry')}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {missing && !failed && (
        <p className="peek-where" role="status" data-testid="peek-missing">
          {t('reader.notice.missing')}
        </p>
      )}
      <div className="peek-bar">
        <button type="button" className="peek-btn" onClick={() => actions.gotoFraction(0)} disabled={state.phase !== 'open'}>
          <SkipBack size={15} aria-hidden /> {t('review.peek.start')}
        </button>
        <button type="button" className="peek-btn" onClick={() => actions.turn('prev')} disabled={state.phase !== 'open'}>
          <ChevronLeft size={16} aria-hidden /> {t('review.peek.back')}
        </button>
        <button type="button" className="peek-btn" onClick={() => actions.turn('next')} disabled={state.phase !== 'open'} data-testid="peek-next">
          {t('review.peek.next')} <ChevronRight size={16} aria-hidden />
        </button>
        <span className="peek-where" data-testid="peek-where">
          {state.phase === 'open' ? t('review.peek.where', { percent }) : ''}
        </span>
      </div>
    </div>
  )
}

type OpenFile = { id: number; format: string; size: number; protected: boolean }

/**
 * The dialog: asks for the files, picks the one the reader opens and lets the person switch between the formats there are. They
 * are the files of an edition of the library (`editionId`), or the files of a download that waits for an answer (`downloadId`).
 */
export function PeekBook({ editionId, downloadId, title, onClose }: { editionId?: number; downloadId?: number; title: string; onClose: () => void }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<OpenFile[] | null>(null)
  const [failure, setFailure] = useState<'notFound' | 'network' | null>(null)
  const [format, setFormat] = useState<string | null>(null)

  useEffect(() => {
    let gone = false
    const asked: Promise<OpenFile[]> =
      downloadId !== undefined ? procureApi.look(downloadId).then((look) => look.files) : readingApi.edition(editionId ?? 0).then((read) => read.edition.files)
    asked.then(
      (found) => !gone && setFiles(found),
      (error) => !gone && setFailure(error instanceof ApiError && (error.status === 404 || error.status === 409) ? 'notFound' : 'network'),
    )
    return () => {
      gone = true
    }
  }, [editionId, downloadId])

  const chosen = files ? chooseFile(files, format) : null
  const readable = (files ?? []).filter((file) => !file.protected && ['epub', 'azw3', 'mobi', 'pdf'].includes(file.format))
  return (
    <Dialog title={t('review.peek.readTitle')} sub={t('review.peek.readSub', { title })} onClose={onClose} wide>
      <div data-testid="peek-dialog">
        {failure && (
          <p className="empty" role="alert">
            {t(`reader.failed.${failure}`)}
          </p>
        )}
        {!failure && !chosen && <p className="text-sm text-muted">{t('reader.loading')}</p>}
        {chosen && 'none' in chosen && (
          <p className="empty" data-testid="peek-none">
            {t(chosen.none === 'protected' ? 'reader.failed.protected' : 'review.peek.noPreview')}
          </p>
        )}
        {chosen && 'file' in chosen && (
          <>
            {readable.length > 1 && (
              <div className="seg mb-3" role="group" aria-label={t('review.peek.format')}>
                {readable.map((file) => (
                  <button key={file.id} type="button" className={file.id === chosen.file.id ? 'on' : ''} aria-pressed={file.id === chosen.file.id} onClick={() => setFormat(file.format)}>
                    {file.format.toUpperCase()}
                  </button>
                ))}
              </div>
            )}
            <Frame
              file={{ id: chosen.file.id, size: chosen.file.size, format: chosen.file.format as BookFormat, url: downloadId !== undefined ? lookContentUrl(downloadId, chosen.file.id) : undefined }}
              title={title}
            />
            <p className="mt-3 text-xs text-muted">{t('review.peek.nothingSaved')}</p>
          </>
        )}
      </div>
    </Dialog>
  )
}
