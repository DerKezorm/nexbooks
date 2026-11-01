/**
 * "Hochladen": the files of a book dropped in or chosen, also on a phone; sent one by
 * one with how far they are, then filed the same way as a download (the check, the scheme, never over anything). The
 * dialog follows the filing and says what came of it. Stopping while files are sent asks first: what came is deleted.
 */
import { BookOpen, CheckCircle2, FolderOpen, Headphones, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, procureApi, uploadsApi, type ReleaseKind, type UploadOut } from '../../api/client'
import { Dialog } from '../Dialog'
import { useConfirm } from '../useConfirm'
import { formatBytes } from '../../lib/bytes'
import { errorText } from '../../lib/errors'
import { iso } from '../../lib/say'
import { ACCEPT, EBOOK_BYTES, FILE_BYTES, kindOfName } from '../../lib/uploads'

const POLL_MS = 1500
const POLL_TRIES = 120

type Picked = { file: File; path: string }
type Stage = 'pick' | 'sending' | 'filing' | 'done' | 'needs' | 'rejected' | 'failed'

function pathOf(file: File): string {
  const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath
  // A folder chosen whole: its own name is left off, the folders below it are kept (``CD 1/01.mp3``).
  return relative && relative.includes('/') ? relative.split('/').slice(1).join('/') || file.name : file.name
}

export function UploadDialog({ workId, kind, title, onClose }: { workId?: number; kind?: ReleaseKind; title?: string; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const [libraries, setLibraries] = useState<{ id: number; name: string; kind: string }[] | null>(null)
  const [library, setLibrary] = useState<number | null>(null)
  const [picked, setPicked] = useState<Picked[]>([])
  const [stage, setStage] = useState<Stage>('pick')
  const [sent, setSent] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  const [result, setResult] = useState<UploadOut | null>(null)
  const [over, setOver] = useState(false)
  const upload = useRef<number | null>(null)
  const stop = useRef<AbortController | null>(null)
  const files = useRef<HTMLInputElement>(null)
  const folder = useRef<HTMLInputElement>(null)
  const confirm = useConfirm()

  useEffect(() => {
    let live = true
    procureApi.targets().then(
      (found) => {
        if (!live) return
        const fitting = found.filter((entry) => !kind || entry.kind === 'both' || entry.kind === kind)
        setLibraries(fitting)
        // A single library is the one; with several, the person chooses (nothing is picked for them).
        setLibrary(fitting.length === 1 ? fitting[0].id : null)
      },
      () => live && setLibraries([]),
    )
    return () => {
      live = false
    }
  }, [kind])

  const total = picked.reduce((sum, item) => sum + item.file.size, 0)
  const kinds = new Set(picked.map((item) => kindOfName(item.path)).filter((entry) => entry === 'ebook' || entry === 'audio'))
  const refused = picked.filter((item) => !kindOfName(item.path) || item.file.size > (kindOfName(item.path) === 'ebook' || kindOfName(item.path) === 'beside' ? EBOOK_BYTES : FILE_BYTES) || item.file.size === 0)
  const mixed = kinds.size > 1
  const wrongKind = kind != null && kinds.size === 1 && !kinds.has(kind)
  const ready = picked.length > 0 && refused.length === 0 && !mixed && !wrongKind && library !== null

  const add = (list: FileList | null) => {
    if (!list) return
    const more = Array.from(list).map((file) => ({ file, path: pathOf(file) }))
    setPicked((before) => [...before, ...more.filter((item) => !before.some((other) => other.path === item.path))])
    setProblem(null)
  }
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    add(event.dataTransfer.files)
  }

  const follow = async (id: number) => {
    for (let attempt = 0; attempt < POLL_TRIES; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS))
      let found: UploadOut
      try {
        found = await uploadsApi.read(id)
      } catch {
        continue
      }
      setResult(found)
      if (found.phase === 'done') return setStage('done')
      if (found.phase === 'problem') return setStage('needs')
      if (found.phase === 'rejected' || found.phase === 'removed') return setStage('rejected')
    }
    setStage('needs')
  }

  const go = async () => {
    if (!ready || library === null) return
    setStage('sending')
    setSent(0)
    setProblem(null)
    const controller = new AbortController()
    stop.current = controller
    try {
      const begun = await uploadsApi.begin({ library_id: library, ...(workId != null ? { work_id: workId } : {}), ...(kind ? { kind } : {}) })
      upload.current = begun.id
      let done = 0
      for (const item of picked) {
        await uploadsApi.send(begun.id, item.path, item.file, (part) => setSent(done + part), controller.signal)
        done += item.file.size
        setSent(done)
      }
      await uploadsApi.finish(begun.id)
      upload.current = null
      setStage('filing')
      await follow(begun.id)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'cancelled') return
      setProblem(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
      setStage('failed')
      if (upload.current !== null) {
        await uploadsApi.cancel(upload.current).catch(() => undefined)
        upload.current = null
      }
    }
  }

  const abort = async () => {
    stop.current?.abort()
    if (upload.current !== null) {
      await uploadsApi.cancel(upload.current).catch(() => undefined)
      upload.current = null
    }
    setStage('pick')
    setSent(0)
  }
  const close = () => {
    if (stage !== 'sending') return onClose()
    confirm.ask({
      title: t('upload.stop.title'),
      text: t('upload.stop.text'),
      confirm: t('upload.stop.confirm'),
      run: async () => {
        await abort()
        onClose()
      },
    })
  }

  const percent = total > 0 ? Math.min(100, Math.round((sent / total) * 100)) : 0
  const size = (bytes: number) => formatBytes(bytes, i18n.language)
  return (
    <Dialog title={t('upload.title')} sub={title} onClose={close} className="acq upl" wide>
      {stage === 'pick' || stage === 'failed' ? (
        <>
          <p className="dlg-p">{t(workId != null ? 'upload.leadWork' : 'upload.lead', { kind: kind ? t(`procure.releases.kind.${kind}`) : '' })}</p>
          <div
            className={`upl-drop ${over ? 'over' : ''}`}
            onDragOver={(event) => {
              event.preventDefault()
              setOver(true)
            }}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
            data-testid="upload-drop"
          >
            <UploadCloud aria-hidden className="ic big" />
            <p>{t('upload.drop')}</p>
            <div className="upl-pick">
              <button type="button" className="bbtn primary" onClick={() => files.current?.click()} data-act="pick-files">
                {t('upload.chooseFiles')}
              </button>
              <button type="button" className="bbtn" onClick={() => folder.current?.click()} data-act="pick-folder">
                <FolderOpen aria-hidden className="ic" />
                {t('upload.chooseFolder')}
              </button>
            </div>
            <small>{t('upload.formats')}</small>
            <input ref={files} type="file" multiple accept={ACCEPT} hidden onChange={(event) => (add(event.target.files), (event.target.value = ''))} data-testid="upload-input" aria-label={t('upload.chooseFiles')} />
            <input ref={folder} type="file" multiple hidden onChange={(event) => (add(event.target.files), (event.target.value = ''))} {...{ webkitdirectory: '' }} aria-label={t('upload.chooseFolder')} />
          </div>
          {picked.length > 0 && (
            <ul className="upl-files" data-testid="upload-files">
              {picked.map((item) => {
                const what = kindOfName(item.path)
                const bad = refused.includes(item)
                return (
                  <li key={item.path} className={bad ? 'bad' : ''}>
                    {what === 'audio' ? <Headphones aria-hidden className="ic" /> : <BookOpen aria-hidden className="ic" />}
                    <span dir="auto">{item.path}</span>
                    <small>{bad ? t(!what ? 'upload.notABook' : item.file.size === 0 ? 'upload.empty' : 'upload.tooLarge') : size(item.file.size)}</small>
                    <button type="button" className="lnk" onClick={() => setPicked((before) => before.filter((other) => other !== item))} aria-label={t('upload.remove', { name: item.path })}>
                      {t('upload.removeShort')}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {mixed && <p className="small-note bad">{t('upload.mixed')}</p>}
          {wrongKind && <p className="small-note bad">{t('upload.wrongKind', { kind: t(`procure.releases.kind.${kind}`) })}</p>}
          {libraries && libraries.length === 0 && <p className="small-note bad">{t('upload.noLibrary')}</p>}
          {libraries && libraries.length > 1 && (
            <div className="opts" role="radiogroup" aria-label={t('upload.where')}>
              {libraries.map((entry) => (
                <label className="opt" key={entry.id}>
                  <input type="radio" name="upload-library" checked={library === entry.id} onChange={() => setLibrary(entry.id)} />
                  <span>
                    <b dir="auto">{iso(entry.name)}</b>
                  </span>
                </label>
              ))}
            </div>
          )}
          {problem && (
            <p role="alert" className="mt-3 text-sm text-bad">
              {problem}
            </p>
          )}
          <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
            <button type="button" className="bbtn" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="button" className="bbtn primary" disabled={!ready} onClick={() => void go()} data-act="upload">
              {t('upload.go', { count: picked.length })}
            </button>
          </div>
        </>
      ) : stage === 'sending' || stage === 'filing' ? (
        <div className="upl-run" data-testid="upload-progress" data-stage={stage}>
          <div className={`pg ${stage === 'filing' ? 'stripe' : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={stage === 'filing' ? 100 : percent}>
            <i style={{ width: `${stage === 'filing' ? 100 : percent}%` }} />
          </div>
          <p className="pgrow">
            <span>{stage === 'filing' ? t('upload.filing') : t('upload.sending', { sent: size(sent), total: size(total) })}</span>
            {stage === 'sending' && <span>{percent} %</span>}
          </p>
          {stage === 'sending' && (
            <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 16 }}>
              <button type="button" className="bbtn" onClick={close} data-act="stop">
                {t('upload.stop.button')}
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="upl-end" data-testid="upload-result" data-stage={stage}>
          {stage === 'done' && (
            <p className="upl-ok">
              <CheckCircle2 aria-hidden className="ic" />
              {t('upload.done')}
            </p>
          )}
          {stage === 'needs' && <p>{t('upload.needs')}</p>}
          {stage === 'rejected' && <p>{t(`upload.rejected.${result?.problem ?? ''}`, { defaultValue: t('upload.rejected.other') })}</p>}
          <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
            {stage === 'done' && result?.work_id != null && (
              <Link className="bbtn primary" to={`/work/${result.work_id}`} onClick={onClose} data-act="open-book">
                {t('upload.open')}
              </Link>
            )}
            {stage === 'needs' && (
              <Link className="bbtn primary" to="/acquire?reiter=needs" onClick={onClose} data-act="open-needs">
                {t('upload.openNeeds')}
              </Link>
            )}
            <button type="button" className="bbtn" onClick={onClose}>
              {t('upload.close')}
            </button>
          </div>
        </div>
      )}
      {confirm.dialog}
    </Dialog>
  )
}
