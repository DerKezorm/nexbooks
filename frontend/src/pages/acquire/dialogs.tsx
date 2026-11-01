/** The three dialogs of the page "Beschaffen" that ask more than "really?": cancelling a download (block the release and
 * look for another, or only cancel), confirming the path mapping nexbooks found, and choosing another library for a
 * finished download. */
import { ArrowRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type MappingOptions, type ProcureBook, type ReleaseKind, type WishOut } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { errorText } from '../../lib/errors'
import { iso } from '../../lib/say'
import { useDo } from './hooks'

type Item = { id: number; book: ProcureBook; kind: ReleaseKind }

/** "Download abbrechen?": three ways out. The job leaves the program with its partial files in both. */
export function CancelDialog({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const doing = useDo()
  const go = async (block: boolean) => {
    if (await doing.run(() => procureApi.cancel(item.id, block))) {
      onDone()
      onClose()
    }
  }
  return (
    <Dialog title={t('acquire.running.cancel.title')} onClose={onClose} className="acq">
      <p className="text-sm text-ink-2" dir="auto">
        {t('acquire.running.cancel.text', { title: item.book.title })}
      </p>
      {doing.problem && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
        <button type="button" data-autofocus className="bbtn" onClick={onClose}>
          {t('acquire.running.cancel.leave')}
        </button>
        <button type="button" className="bbtn" disabled={doing.busy} onClick={() => void go(false)} data-act="cancel-only">
          {t('acquire.running.cancel.only')}
        </button>
        <button type="button" className="bbtn danger" disabled={doing.busy} onClick={() => void go(true)} data-act="cancel-block">
          {t('acquire.running.cancel.block')}
        </button>
      </div>
    </Dialog>
  )
}

function Pair({ a, b, la, lb }: { a: string; b: string; la: string; lb: string }) {
  return (
    <div className="pair">
      <div>
        <small>{la}</small>
        <code>{iso(a)}</code>
      </div>
      <span className="pa">
        <ArrowRight aria-hidden className="ic" />
      </span>
      <div>
        <small>{lb}</small>
        <code>{iso(b)}</code>
      </div>
    </div>
  )
}

/** "Pfadzuordnung bestätigen": only what nexbooks found itself, by the name of the job, is offered. */
export function MappingDialog({ id, client, onClose, onDone }: { id: number; client: string; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [options, setOptions] = useState<MappingOptions | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [pick, setPick] = useState(0)
  const doing = useDo()
  useEffect(() => {
    let live = true
    procureApi.mappingOptions(id).then(
      (found) => live && setOptions(found),
      (error) => live && setFailure(error instanceof ApiError ? error.code : 'internal_error'),
    )
    return () => {
      live = false
    }
  }, [id])
  const pairs = options ? (options.search === 'proposed' && options.proposal ? [{ ...options.proposal, place: '' }] : options.search === 'twice' ? options.choices : []) : []
  const chosen = pairs[pick]
  const confirm = async () => {
    if (!chosen) return
    if (await doing.run(() => procureApi.confirmMapping(id, { remote: chosen.remote, local: chosen.local }))) {
      onDone()
      onClose()
    }
  }
  return (
    <Dialog title={t('acquire.needs.mapping.title')} onClose={onClose} className="acq" wide>
      {failure && (
        <p role="alert" className="text-sm text-bad">
          {errorText(failure)}
        </p>
      )}
      {!options && !failure && <p className="text-sm text-muted">{t('acquire.dialog.loading')}</p>}
      {options && (
        <>
          <p className="dlg-p" dir="auto">
            {options.search === 'proposed' && chosen && t('acquire.needs.mapping.proposed', { client, remote: options.reported ?? '' })}
            {options.search === 'twice' && t('acquire.needs.mapping.twice', { client })}
            {options.search === 'cut' && t('acquire.needs.mapping.cut')}
            {options.search === 'nowhere' && t('acquire.needs.mapping.nowhere', { remote: options.reported ?? '' })}
            {options.search === 'no_path' && t('acquire.needs.mapping.noPath', { client })}
          </p>
          {options.search === 'proposed' && chosen && (
            <>
              <Pair a={chosen.remote} b={chosen.local} la={t('acquire.needs.mapping.client', { client })} lb={t('acquire.needs.mapping.here')} />
              <p className="dlg-p muted">{t('acquire.needs.mapping.note')}</p>
            </>
          )}
          {options.search === 'twice' && (
            <div className="opts" role="radiogroup" aria-label={t('acquire.needs.mapping.pick')}>
              {options.choices.map((choice, index) => (
                <label className="opt" key={choice.place}>
                  <input type="radio" name="mapping" checked={pick === index} onChange={() => setPick(index)} />
                  <span>
                    <b dir="auto">{iso(choice.place)}</b>
                    <small>{t('acquire.needs.mapping.would', { remote: choice.remote, local: choice.local })}</small>
                  </span>
                </label>
              ))}
            </div>
          )}
          {options.roots.length > 0 && options.search !== 'proposed' && options.search !== 'twice' && (
            <p className="dlg-p muted">{t('acquire.needs.mapping.roots', { roots: options.roots.join(', ') })}</p>
          )}
        </>
      )}
      {doing.problem && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
        <button type="button" className="bbtn" onClick={onClose}>
          {t('common.cancel')}
        </button>
        {chosen && (
          <button type="button" className="bbtn primary" disabled={doing.busy} onClick={() => void confirm()} data-act="confirm-mapping">
            {t('acquire.needs.mapping.confirm')}
          </button>
        )}
      </div>
    </Dialog>
  )
}

/** "Andere Bibliothek wählen": only libraries nexbooks may write into are worth choosing; the server decides. */
export function LibraryDialog({ item, current, onClose, onDone }: { item: Item; current: number | null; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [libraries, setLibraries] = useState<{ id: number; name: string; kind: string }[]>([])
  const [pick, setPick] = useState<number | null>(null)
  const doing = useDo()
  useEffect(() => {
    let live = true
    procureApi.targets().then(
      (found) => live && setLibraries(found.filter((entry) => entry.kind === 'both' || entry.kind === item.kind)),
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [item.kind])
  const go = async () => {
    if (pick !== null && (await doing.run(() => procureApi.chooseLibrary(item.id, pick)))) {
      onDone()
      onClose()
    }
  }
  return (
    <Dialog title={t('acquire.needs.library.title')} onClose={onClose} className="acq">
      <p className="dlg-p">{t('acquire.needs.library.text', { kind: t(`procure.releases.kind.${item.kind}`) })}</p>
      <div className="opts" role="radiogroup" aria-label={t('acquire.needs.library.title')}>
        {libraries.map((library) => (
          <label className="opt" key={library.id}>
            <input type="radio" name="library" checked={pick === library.id} onChange={() => setPick(library.id)} />
            <span>
              <b dir="auto">{iso(library.name)}</b>
              {library.id === current && <small>{t('acquire.needs.library.current')}</small>}
            </span>
          </label>
        ))}
      </div>
      {doing.problem && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
        <button type="button" className="bbtn" onClick={onClose}>
          {t('common.cancel')}
        </button>
        <button type="button" className="bbtn primary" disabled={pick === null || doing.busy} onClick={() => void go()} data-act="choose-library">
          {t('acquire.needs.library.go')}
        </button>
      </div>
    </Dialog>
  )
}

/** "Einem Buch zuordnen" (the operator): a job nobody asked for becomes the download of an open wish; nexbooks follows it
 * from the next round and files it like any other. */
export function AssignDialog({ job, onClose, onDone }: { job: { id: number; name: string }; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [wishes, setWishes] = useState<WishOut[] | null>(null)
  const [pick, setPick] = useState<number | null>(null)
  const doing = useDo()
  useEffect(() => {
    let live = true
    procureApi.wishes().then(
      (found) => {
        if (!live) return
        const open = found.filter((wish) => wish.state !== 'loading')
        setWishes(open)
        setPick(open[0]?.id ?? null)
      },
      () => live && setWishes([]),
    )
    return () => {
      live = false
    }
  }, [])
  const go = async () => {
    if (pick !== null && (await doing.run(() => procureApi.assignForeign(job.id, pick)))) {
      onDone()
      onClose()
    }
  }
  return (
    <Dialog title={t('acquire.needs.foreign.assign.title')} onClose={onClose} className="acq">
      <p className="dlg-p">
        {t('acquire.needs.foreign.assign.text')} <code dir="auto">{job.name}</code>
      </p>
      {wishes && wishes.length === 0 && <p className="muted">{t('acquire.needs.foreign.assign.none')}</p>}
      <div className="opts" role="radiogroup" aria-label={t('acquire.needs.foreign.assign.title')}>
        {(wishes ?? []).map((wish) => (
          <label className="opt" key={wish.id}>
            <input type="radio" name="assign" checked={pick === wish.id} onChange={() => setPick(wish.id)} />
            <span>
              <b dir="auto">{wish.book.title}</b>
              <small dir="auto">
                {wish.book.authors[0] ? `${wish.book.authors[0]} · ` : ''}
                {t(`procure.releases.kind.${wish.kind}`)}
              </small>
            </span>
          </label>
        ))}
      </div>
      {doing.problem && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="dlg-f" style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', paddingTop: 16 }}>
        <button type="button" className="bbtn" onClick={onClose}>
          {t('common.cancel')}
        </button>
        <button type="button" className="bbtn primary" disabled={pick === null || doing.busy} onClick={() => void go()} data-act="assign-go">
          {t('acquire.needs.foreign.assign.go')}
        </button>
      </div>
    </Dialog>
  )
}
