/**
 * The twins of the page of a book: the e-book and the recording side by side with the
 * sign of equality between them; a missing one as a dashed card that says so. Further editions (shortened, a radio play,
 * another language) stand below as chips. The e-book card says where the reader stands ("Kapitel 14 von 38 · 37 %", with the
 * bar of the design) and opens the reader ("Lesen" or "Weiterlesen"); the recording's card says how far the person has
 * listened ("2 Std 14 Min von 14 Std 3 Min · 16 %") and opens the player ("Hören" or "Weiterhören"). The two places
 * are the person's own and stand apart: the one never moves the other. Both offer the files for download.
 */
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { downloadUrl, type EditionOut, type FileOut, type HistoryOut, type ProgressOut, type ReleaseKind, type WorkOut, type WorkProcure } from '../api/client'
import { languageName } from '../lib/books'
import { formatBytes } from '../lib/bytes'
import { leadEditions, useEditionLabel } from '../lib/editionText'
import { usePlayer, usePlayerLive } from '../state/player'
import { useProcure } from '../state/procure'
import { AudioIcon, BookIcon } from './BookCard'
import { FinishRow } from './Finished'
import { SendToKindle } from './SendToKindle'
import { SendToKobo } from './SendToKobo'
import { StateLine } from './procure/StateLine'
import { DownloadIcon, MoveIcon, UploadIcon } from './icons'

function Files({ edition }: { edition: EditionOut }) {
  const { t, i18n } = useTranslation()
  const files = edition.files
  if (files.length === 0) return null
  // An e-book has a file for each format; a recording with many parts is a list, not a row of buttons.
  if (edition.kind === 'audio' && files.length > 3) {
    return (
      <details className="tw-parts">
        <summary>{t('work.twin.files', { count: files.length })}</summary>
        <ul>
          {files.map((file) => (
            <li key={file.id}>
              <a className="tw-file" href={downloadUrl(file.id)} download>
                <DownloadIcon width={13} />
                {t('work.twin.part', { n: file.part })} · {file.format.toUpperCase()} · {formatBytes(file.size, i18n.language, 1000)}
              </a>
            </li>
          ))}
        </ul>
      </details>
    )
  }
  return (
    <div className="tw-files">
      {files.map((file: FileOut) => (
        <a key={file.id} className="tw-file" href={downloadUrl(file.id)} download aria-label={t('work.twin.download', { format: file.format.toUpperCase() })} title={formatBytes(file.size, i18n.language, 1000)}>
          <DownloadIcon width={13} />
          {file.format.toUpperCase()}
        </a>
      ))}
    </div>
  )
}

/** Where the person stands in a recording: the time heard of the whole, as the design says it. While the player holds this
 * recording, its place is the one of the player (it moves on while it plays). */
function Heard({ edition, progress }: { edition: EditionOut; progress?: ProgressOut }) {
  const { t } = useTranslation()
  const { duration } = useEditionLabel()
  const { book, engine } = usePlayerLive()
  const live = book?.edition.id === edition.id && engine.position > 0 ? engine.position : null
  const position = live ?? progress?.position ?? null
  if (position === null) return <div className="tw-pos">{t('work.twin.notStarted')}</div>
  const total = edition.duration || book?.total || 0
  const fraction = total > 0 ? Math.min(1, position / total) : (progress?.fraction ?? 0)
  const percent = Math.round(fraction * 100)
  return (
    <>
      <div className="tw-pos" data-testid="twin-position">
        <b>{duration(position)}</b> {t('work.twin.timeOf', { total: duration(total), percent })}
      </div>
      <div className="bar2" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <i style={{ width: `${percent}%` }} />
      </div>
    </>
  )
}

function ListenButton({ edition, progress }: { edition: EditionOut; progress?: ProgressOut }) {
  const { t } = useTranslation()
  const player = usePlayer()
  const { book, engine } = usePlayerLive()
  const begun = Boolean(progress) || (book?.edition.id === edition.id && engine.position > 0)
  return (
    <button type="button" className="go" onClick={() => void player.open(edition.id, { expand: true, play: true })} data-testid="listen">
      {begun ? t('work.twin.listenOn') : t('work.twin.listen')}
    </button>
  )
}

/** Where the person stands in an e-book, as the card says it. */
function Where({ progress }: { progress: ProgressOut }) {
  const { t } = useTranslation()
  const percent = Math.round(progress.fraction * 100)
  if (progress.chapter > 0 && progress.chapter_count > 0) {
    return (
      <>
        <b>{t(progress.unit === 'page' ? 'work.twin.pageN' : 'work.twin.chapterN', { n: progress.chapter })}</b> {t('work.twin.rest', { total: progress.chapter_count, percent })}
      </>
    )
  }
  return <b>{t('work.twin.onlyPercent', { percent })}</b>
}

type Variant = 'unabridged' | 'abridged' | 'dramatized'

/** A recording whose reading nothing proves (the running time does not, the file does not say): said quietly, and a person
 * with manage may set it by hand (a choice that can be taken back). */
function Unproven({ edition, canSet, onVariant }: { edition: EditionOut; canSet: boolean; onVariant?: (edition: EditionOut, variant: Variant | null) => Promise<void> }) {
  const { t } = useTranslation()
  const byHand = edition.corrected.includes('variant')
  if (!edition.variant_uncertain && !byHand) return null
  return (
    <div className="tw-unproven" data-testid="variant-unproven">
      {edition.variant_uncertain && <span title={t('work.twin.unprovenHelp')}>{t('work.twin.unproven')}</span>}
      {canSet && onVariant && edition.variant_uncertain && (
        <select
          className="tw-pick"
          aria-label={t('work.twin.unprovenPick')}
          value=""
          onChange={(event) => event.target.value && void onVariant(edition, event.target.value as Variant)}
          data-testid="variant-pick"
        >
          <option value="">{t('work.twin.unprovenNone')}</option>
          {(['unabridged', 'abridged', 'dramatized'] as const).map((variant) => (
            <option key={variant} value={variant}>
              {t(`work.variant.${variant}`)}
            </option>
          ))}
        </select>
      )}
      {canSet && onVariant && byHand && (
        <button type="button" className="tw-takeback" onClick={() => void onVariant(edition, null)} data-testid="variant-takeback">
          {t('work.twin.unprovenTakeBack')}
        </button>
      )}
    </div>
  )
}

/** A language nexbooks only worked out, which a person with manage may confirm in one step. */
function LanguageGuess({ edition, canSet, onLanguage }: { edition: EditionOut; canSet: boolean; onLanguage?: (edition: EditionOut) => Promise<void> }) {
  const { t, i18n } = useTranslation()
  if (!canSet || !onLanguage || !edition.language_guess || !edition.language) return null
  return (
    <div className="tw-unproven" data-testid="language-guess">
      <button type="button" className="tw-takeback" onClick={() => void onLanguage(edition)} data-testid="language-confirm">
        {t('work.twin.languageConfirm', { language: languageName(edition.language, i18n.language) })}
      </button>
    </div>
  )
}

type Finishing = { entries: HistoryOut[]; onChange: () => void; onSaid?: (text: string) => void }

/** What the person may look for: a missing kind and an upgrade open "Releases ansehen". */
type Procuring = { status: WorkProcure; onReleases: (kind: ReleaseKind) => void; onAcquire: (kind: ReleaseKind) => void; workId: number; onUpload?: (kind: ReleaseKind) => void }

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  )
}

function Card({ edition, kind, progress, canSet = false, onVariant, onLanguage, finishing, procuring, onMove }: { edition: EditionOut | undefined; kind: 'ebook' | 'audio'; progress?: ProgressOut; canSet?: boolean; onVariant?: (edition: EditionOut, variant: Variant | null) => Promise<void>; onLanguage?: (edition: EditionOut) => Promise<void>; finishing?: Finishing; procuring?: Procuring; onMove?: (edition: EditionOut) => void }) {
  const { t } = useTranslation()
  const { label } = useEditionLabel()
  const navigate = useNavigate()
  const Icon = kind === 'ebook' ? BookIcon : AudioIcon
  const may = procuring?.status.kinds.find((entry) => entry.kind === kind)
  const procure = useProcure()
  const asked = procuring && procure.allowed ? procure.kindsOf({ workId: procuring.workId }).find((state) => state.kind === kind) : undefined
  if (!edition) {
    return (
      <div className="twin missing" data-testid="twin-missing" data-kind={kind}>
        <div className="tw-h">
          <span className="tw-ic">
            <Icon />
          </span>
          <div>
            <h4>{t(kind === 'ebook' ? 'work.twin.noEbook' : 'work.twin.noAudio')}</h4>
            <span className="tw-sub">{t('work.twin.missing')}</span>
          </div>
        </div>
        {asked && <div className="bes-host acq"><StateLine state={asked} /></div>}
        {procuring && may && !asked && (
          <button type="button" className="go primary" onClick={() => procuring.onAcquire(kind)} data-testid="acquire">
            <DownloadIcon width={15} />
            {t('acquire.entry.getKind', { kind: t(`procure.releases.kind.${kind}`) })}
          </button>
        )}
        {procuring && may && (
          <button type="button" className="go" onClick={() => procuring.onReleases(kind)} data-testid="releases">
            <SearchIcon />
            {t('procure.releases.open')}
          </button>
        )}
        {procuring?.onUpload && may && !asked && (
          <button type="button" className="go" onClick={() => procuring.onUpload?.(kind)} data-testid="upload">
            <UploadIcon width={15} />
            {t('upload.button')}
          </button>
        )}
      </div>
    )
  }
  const text = label(edition)
  return (
    <div className="twin" data-testid="twin" data-kind={kind}>
      <div className="tw-h">
        <span className="tw-ic">
          <Icon />
        </span>
        <div>
          <h4>{text.main}</h4>
          <span className="tw-sub">{text.sub}</span>
        </div>
      </div>
      <Files edition={edition} />
      {kind === 'ebook' && <SendToKindle files={edition.files} />}
      {kind === 'ebook' && <SendToKobo editionId={edition.id} title={edition.title} files={edition.files} />}
      {asked && <div className="bes-host acq"><StateLine state={asked} /></div>}
      {procuring && may?.upgrade && !asked && (
        <button type="button" className="tw-upgrade" onClick={() => procuring.onAcquire(kind)} data-testid="acquire-upgrade">
          <DownloadIcon width={14} />
          {t('acquire.entry.upgrade')}
        </button>
      )}
      {procuring && may?.upgrade && (
        <button type="button" className="tw-upgrade" onClick={() => procuring.onReleases(kind)} data-testid="releases-upgrade">
          <SearchIcon />
          {t('procure.releases.upgrade')}
        </button>
      )}
      {/* Another library the person looks after takes this kind. */}
      {onMove && edition.can_move && (
        <button type="button" className="tw-upgrade" onClick={() => onMove(edition)} data-testid="move-edition">
          <MoveIcon width={14} />
          {t('move.button')}
        </button>
      )}
      {kind === 'audio' && <Unproven edition={edition} canSet={canSet} onVariant={onVariant} />}
      <LanguageGuess edition={edition} canSet={canSet} onLanguage={onLanguage} />
      {kind === 'audio' ? (
        <Heard edition={edition} progress={progress} />
      ) : progress ? (
        <>
          <div className="tw-pos" data-testid="twin-position">
            <Where progress={progress} />
          </div>
          <div className="bar2" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress.fraction * 100)}>
            <i style={{ width: `${Math.round(progress.fraction * 100)}%` }} />
          </div>
        </>
      ) : (
        <div className="tw-pos">{t('work.twin.notStarted')}</div>
      )}
      {kind === 'ebook' ? (
        <button type="button" className="go" onClick={() => navigate(`/read/${edition.id}`)} data-testid="read">
          {progress ? t('work.twin.continue') : t('work.twin.read')}
        </button>
      ) : (
        <ListenButton edition={edition} progress={progress} />
      )}
      {finishing && <FinishRow editionId={edition.id} entries={finishing.entries.filter((entry) => entry.edition_id === edition.id)} onChange={finishing.onChange} onSaid={finishing.onSaid} />}
    </div>
  )
}

export function Twins({ work, places = [], canSet, onVariant, onLanguage, history, onHistory, onSaid, procuring, onMove }: { work: WorkOut; places?: ProgressOut[]; canSet?: (libraryId: number) => boolean; onVariant?: (edition: EditionOut, variant: Variant | null) => Promise<void>; onLanguage?: (edition: EditionOut) => Promise<void>; history?: HistoryOut[]; onHistory?: () => void; onSaid?: (text: string) => void; procuring?: Procuring; onMove?: (edition: EditionOut) => void }) {
  const { t } = useTranslation()
  const { label } = useEditionLabel()
  const { ebook, audio, others } = leadEditions(work)
  const placeOf = (edition: EditionOut | undefined) => (edition ? places.find((place) => place.edition_id === edition.id) : undefined)
  const finishing: Finishing | undefined = history && onHistory ? { entries: history, onChange: onHistory, onSaid } : undefined
  return (
    <>
      <div className="twins">
        <Card edition={ebook} kind="ebook" progress={placeOf(ebook)} canSet={ebook ? (canSet?.(ebook.library_id) ?? false) : false} onLanguage={onLanguage} finishing={finishing} procuring={procuring} onMove={onMove} />
        <div className="tw-link">
          <span title={t('work.twin.link')}>⇄</span>
        </div>
        <Card edition={audio} kind="audio" progress={placeOf(audio)} canSet={audio ? (canSet?.(audio.library_id) ?? false) : false} onVariant={onVariant} onLanguage={onLanguage} finishing={finishing} procuring={procuring} onMove={onMove} />
      </div>
      {others.length > 0 && (
        <div className="more-fass" data-testid="more-editions">
          {others.map((edition) => {
            const text = label(edition)
            const Icon = text.icon === 'book' ? BookIcon : AudioIcon
            return (
              <span key={edition.id}>
                <Icon />
                {text.main} · {text.sub.split(' · ')[0]}
              </span>
            )
          })}
        </div>
      )}
      {(work.duplicates ?? []).length > 0 && (
        <p className="tw-dups" data-testid="duplicates">
          {t('work.twin.duplicates', { count: work.duplicates.length })}
        </p>
      )}
    </>
  )
}
