/**
 * The reader's page, `/read/:editionId`: one edition of a book on the whole screen (no head of the app around it). It asks for
 * the edition, where the person is in it and their bookmarks, picks the file to read (EPUB, else AZW3 or MOBI, else PDF; a
 * copy-protected one is never opened), and hands over to the reader, which opens the book where it was left. Back leads to
 * the page of the book; a reload opens at the saved place.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, readingApi, type BookmarkOut, type EditionToRead, type ProgressOut } from '../api/client'
import { Reader } from '../components/reader/Reader'
import { chooseFile, PAGE_COLOURS, type BookFormat } from '../lib/readFrame'

type Loaded = { read: EditionToRead; progress: ProgressOut | null; bookmarks: BookmarkOut[] }

export function ReaderPage() {
  const { editionId } = useParams()
  const [search] = useSearchParams()
  return <Loader key={editionId} id={Number(editionId)} wanted={search.get('format')} />
}

/** Closing the reader: back to where it was opened from when that is the app (the page of the book, as it was), else to the page of the book. */
function useLeave(workId: number | null) {
  const navigate = useNavigate()
  return useCallback(() => {
    const index = (window.history.state as { idx?: number } | null)?.idx ?? 0
    if (index > 0) navigate(-1)
    else navigate(workId ? `/work/${workId}` : '/', { replace: true })
  }, [navigate, workId])
}

function Message({ children, onBack, back, extra }: { children: string; onBack: () => void; back: string; extra?: React.ReactNode }) {
  const light = PAGE_COLOURS.light
  return (
    <div className="reader bare" data-testid="reader" style={{ '--rd-ground': light.ground, '--rd-ink': light.ink, '--rd-muted': light.muted, '--rd-line': light.line, '--rd-accent': '#8e3b46' } as React.CSSProperties}>
      <div className="rd-failed" role="alert" data-testid="reader-failed">
        <p>{children}</p>
        <div className="rd-failed-go">
          {extra}
          <button type="button" className="rd-quiet" onClick={onBack}>
            {back}
          </button>
        </div>
      </div>
    </div>
  )
}

function Loader({ id, wanted }: { id: number; wanted: string | null }) {
  const { t } = useTranslation()
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [failure, setFailure] = useState<'notFound' | 'network' | null>(null)
  const leave = useLeave(loaded?.read.work.id ?? null)

  useEffect(() => {
    if (!Number.isInteger(id) || id < 1) {
      setFailure('notFound')
      return
    }
    let gone = false
    Promise.all([readingApi.edition(id), readingApi.progress(id), readingApi.bookmarks(id)]).then(
      ([read, progress, bookmarks]) => {
        if (!gone) setLoaded({ read, progress, bookmarks })
      },
      (error) => {
        if (!gone) setFailure(error instanceof ApiError && (error.status === 404 || error.status === 422) ? 'notFound' : 'network')
      },
    )
    return () => {
      gone = true
    }
  }, [id])

  if (failure) {
    return (
      <Message onBack={leave} back={t('reader.failed.toBook')}>
        {t(`reader.failed.${failure}`)}
      </Message>
    )
  }
  if (!loaded) {
    return (
      <div className="reader bare" data-testid="reader" data-phase="loading">
        <p className="rd-loading" role="status">
          {t('reader.loading')}
        </p>
      </div>
    )
  }
  const { read, progress, bookmarks } = loaded
  const files = read.edition.kind === 'ebook' ? read.edition.files : []
  const chosen = chooseFile(files, wanted)
  if ('none' in chosen) {
    const download = chosen.none === 'protected' ? files.filter((file) => file.protected) : []
    return (
      <Message
        onBack={leave}
        back={t('reader.failed.toBook')}
        extra={download.map((file) => (
          <a key={file.id} className="go" href={`/api/v1/files/${file.id}/download`} download>
            {t('reader.failed.download')} · {file.format.toUpperCase()}
          </a>
        ))}
      >
        {t(chosen.none === 'protected' ? 'reader.failed.protected' : 'reader.failed.nothing')}
      </Message>
    )
  }
  return (
    <Reader
      editionId={id}
      work={read.work}
      edition={read.edition}
      file={{ id: chosen.file.id, size: chosen.file.size, format: chosen.file.format as BookFormat }}
      progress={progress}
      bookmarks={bookmarks}
      onBack={leave}
    />
  )
}
