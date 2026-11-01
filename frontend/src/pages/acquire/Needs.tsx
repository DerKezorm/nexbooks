/** "Braucht dich": the downloads nexbooks cannot take further by itself, each with
 * what is wrong in words, what nexbooks did, and the ways out; the questions of the check after the download (the running time, the bundle, files it does not recognise) and the release it turned down for its language; the notes
 * the filing left this person (a place carried over by its share only, a folder the program shares with others that
 * nexbooks did not tidy); and the jobs in its category in the download
 * program that nobody asked for, which nexbooks leaves alone until a person says what to do. */
import { ArrowRight } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { editionsApi, procureApi, type BundleUnit, type ForeignJobOut, type NoteOut, type ProblemOut, type WishOut } from '../../api/client'
import { useConfirm } from '../../components/useConfirm'
import { ago } from '../../lib/ago'
import { languageName } from '../../lib/books'
import { unclearCase } from '../../lib/languageCase'
import { DownloadLook } from '../../components/review/DownloadLook'
import { leftText } from '../../lib/procure'
import { iso } from '../../lib/say'
import { AssignDialog, CancelDialog, LibraryDialog, MappingDialog } from './dialogs'
import { useDo } from './hooks'
import { MiniCover } from './bits'

const TONE: Record<string, 'warn' | 'bad' | 'quiet'> = {
  path_not_found: 'bad',
  no_write: 'bad',
  client_error: 'bad',
  paused: 'warn',
  no_space: 'bad',
  place_failed: 'bad',
  gone_from_client: 'warn',
  language: 'warn',
  runtime: 'warn',
  bundle: 'warn',
  files_unassigned: 'warn',
  language_unclear: 'warn',
  language_unknown: 'warn',
  edition: 'warn',
  partial: 'warn',
  pen_name: 'warn',
  same_edition: 'warn',
  upgrade_position: 'warn',
  upgrade_replaced: 'warn',
  folder_kept: 'quiet',
  works_joined: 'warn',
  duplicate: 'warn',
  kindle_failed: 'warn',
}
const KNOWN = ['path_not_found', 'no_write', 'client_error', 'no_space', 'place_failed', 'gone_from_client', 'paused', 'language', 'runtime', 'bundle', 'files_unassigned', 'language_unclear', 'language_unknown', 'edition', 'partial', 'pen_name', 'same_edition']
/** Problems "Erneut prüfen" helps with. */
const RECHECK = ['path_not_found', 'no_write', 'client_error', 'no_space', 'place_failed', 'gone_from_client']
/** The questions of the check: the person answers or blocks the release. */
const QUESTIONS = ['runtime', 'bundle', 'files_unassigned', 'language_unclear', 'language_unknown', 'edition', 'partial', 'pen_name', 'same_edition']
/** The questions the person answers with "it is the book" (the files go in as the book wished). */
const TAKE = ['files_unassigned', 'edition', 'partial', 'pen_name']
/** The problems the person may block the release for (the questions, and a job the program holds paused). */
const BLOCKABLE = [...QUESTIONS, 'paused']

/** The key a reason of the program is worded by: its label in capitals ("TOO LARGE" is TOO_LARGE; "PROPAGATING 3 min" is PROPAGATING). */
function whyKey(label: string): string {
  return (label.toUpperCase().match(/^[A-Z_]+(?: [A-Z_]+)*/)?.[0] ?? '').replace(/ /g, '_')
}

/** A book "Releases ansehen" opens for. */
export type ReleasesOf = Pick<ProblemOut, 'book' | 'kind'>

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** The last part of a folder, as a button can name it. */
const folderName = (folder: string) => folder.split('/').pop() ?? folder

export function Needs({ problems, foreign, notes = [], onChanged, onReleases }: { problems: ProblemOut[]; foreign: ForeignJobOut[]; notes?: NoteOut[]; looks?: WishOut[]; onChanged: () => Promise<void>; onReleases?: (book: ReleasesOf) => void }) {
  const { t, i18n } = useTranslation()
  const [mapping, setMapping] = useState<ProblemOut | null>(null)
  const [library, setLibrary] = useState<ProblemOut | null>(null)
  const [cancelling, setCancelling] = useState<ProblemOut | null>(null)
  const [assigning, setAssigning] = useState<ForeignJobOut | null>(null)
  const [picked, setPicked] = useState<Record<number, string>>({})
  const confirm = useConfirm()
  const doing = useDo(onChanged)
  const hours = (seconds: number | null) => (seconds == null ? '·' : leftText(seconds, t('acquire.running.h'), t('acquire.running.min'), t('acquire.running.sec')))
  /** The article before the name of a format as it is said: "an EPUB", "a MOBI" (English), "ein EPUB" (German). */
  const article = (name: string, capital = false) => t(`acquire.needs.article.${capital ? (/^[AEIOU]/i.test(name) ? 'An' : 'A') : /^[AEIOU]/i.test(name) ? 'an' : 'a'}`)
  const fmt = (code: string | undefined) => (code ? t(`procure.formats.name.${code}`, { defaultValue: code.toUpperCase() }) : '')

  const ignore = (job: ForeignJobOut) =>
    confirm.ask({
      title: t('acquire.needs.foreign.ignore.title'),
      text: t('acquire.needs.foreign.ignore.text'),
      confirm: t('acquire.needs.foreign.ignore.confirm'),
      danger: false,
      run: async () => {
        await procureApi.ignoreForeign(job.id)
        await onChanged()
      },
    })
  const removeJob = (job: ForeignJobOut) =>
    confirm.ask({
      title: t('acquire.needs.foreign.remove.title'),
      text: t('acquire.needs.foreign.remove.text', { client: job.client }),
      confirm: t('acquire.needs.foreign.remove.confirm'),
      run: async () => {
        await procureApi.removeForeign(job.id)
        await onChanged()
      },
    })
  /** "Sperren und weitersuchen" for a question: the files go, the release is blocked, the wish searches on. */
  const block = (problem: ProblemOut) =>
    confirm.ask({
      title: t(`acquire.needs.block.${problem.code}.title`),
      text: t(`acquire.needs.block.${problem.code}.text`),
      confirm: t(`acquire.needs.block.${problem.code}.confirm`),
      run: async () => {
        // A job paused in its program is cancelled there and blocked; the answer to a question blocks the same way.
        await (problem.code === 'paused' ? procureApi.cancel(problem.id, true) : procureApi.decide(problem.id, 'block'))
        await onChanged()
      },
    })
  /** "Trennen" for an edition the scan put into another book: it becomes a book of its own again (asks first). */
  const split = (note: NoteOut) =>
    confirm.ask({
      title: t('acquire.needs.joined.title', { title: note.detail.title ?? '' }),
      text: t('acquire.needs.joined.text', { title: note.detail.title ?? '' }),
      confirm: t('acquire.needs.joined.confirm'),
      danger: false,
      run: async () => {
        if (note.edition_id != null) await editionsApi.split(note.edition_id)
        await procureApi.acknowledgeNote(note.id)
        await onChanged()
      },
    })
  const dropCopy = (note: NoteOut, folder: string) =>
    confirm.ask({
      title: t('acquire.needs.drop.title', { name: folderName(folder) }),
      text: t('acquire.needs.drop.text', { folder }),
      confirm: t('acquire.needs.drop.confirm'),
      run: async () => {
        await procureApi.dropCopy(note.id, folder)
        await onChanged()
      },
    })
  const restore = (note: NoteOut) =>
    confirm.ask({
      title: t('acquire.needs.restore.title', { old: fmt(note.detail.from) }),
      text: t('acquire.needs.restore.text', { old: fmt(note.detail.from), now: fmt(note.detail.to) }),
      confirm: t('acquire.needs.restore.confirm', { old: fmt(note.detail.from) }),
      danger: false,
      run: async () => {
        await procureApi.restoreNote(note.id)
        await onChanged()
      },
    })

  if (problems.length === 0 && foreign.length === 0 && notes.length === 0) {
    return (
      <p className="empty" data-testid="needs-empty">
        {t('acquire.needs.empty')}
      </p>
    )
  }
  return (
    <>
      {doing.problem && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {doing.problem}
        </p>
      )}
      <div className="pbs" data-testid="needs">
        {problems.map((problem) => {
          const code = KNOWN.includes(problem.code) ? problem.code : 'client_error'
          const tone = TONE[code] ?? 'warn'
          const values = problem.values
          const proposal = values.proposal as { remote: string; local: string } | null | undefined
          const units = (Array.isArray(values.units) ? values.units : []) as BundleUnit[]
          const choice = picked[problem.id] ?? units.find((unit) => unit.volume != null && unit.volume === values.wanted)?.unit ?? units[0]?.unit
          const chosen = units.find((unit) => unit.unit === choice)
          const words = {
            library: problem.library?.name ?? '',
            client: problem.client,
            file: languageName(String(values.file ?? ''), i18n.language) || String(values.file ?? ''),
            wanted: languageName(String(values.wanted ?? ''), i18n.language) || String(values.wanted ?? ''),
            text: values.text === 'mixed' || values.text === 'other' ? '' : languageName(String(values.text ?? ''), i18n.language) || String(values.text ?? ''),
            name: languageName(String(values.name ?? ''), i18n.language) || String(values.name ?? ''),
            folder: languageName(String(values.folder ?? ''), i18n.language) || String(values.folder ?? ''),
            loaded: hours(number(values.loaded)),
            full: hours(number(values.unabridged)),
            count: units.length,
            volume: values.wanted != null ? String(values.wanted) : '',
            title: String(values.title ?? ''),
            have: fmt(typeof values.have === 'string' ? values.have : undefined),
            found: fmt(typeof values.found === 'string' ? values.found : undefined),
            reason: t(`acquire.needs.placeReason.${String(values.reason ?? 'failed')}`, { defaultValue: t('acquire.needs.placeReason.failed') }),
          }
          // A question of the language says what happened in sentences of its own (what was searched, what each witness says).
          const unclear = code === 'language_unclear' ? unclearCase(values) : null
          const unclearWords = unclear
            ? {
                ...words,
                language: languageName(unclear.language, i18n.language) || unclear.language,
                witness: unclear.witness ? t(`acquire.needs.unclear.witness.${unclear.witness}`) : '',
                nameLine: values.name ? t('acquire.needs.unclear.nameSays', words) : t('acquire.needs.unclear.nameSilent'),
              }
            : words
          const head = code === 'runtime' && values.reason === 'too_long' ? 'runtimeLong' : code === 'runtime' && values.reason === 'unproven' ? 'runtimeUnproven' : code === 'runtime' && values.reason === 'shorter' ? 'runtimeShorter' : code === 'runtime' && values.reason === 'dramatized' ? 'runtimeDramatized' : code === 'runtime' && values.reason === 'genre' ? 'runtimeGenre' : code
          const leftOut = (Array.isArray(values.left_out) ? (values.left_out as { title?: string }[]) : []).map((item) => item.title || t('acquire.needs.unassigned.noTitle'))
          const longest = Math.max(number(values.loaded) ?? 0, number(values.unabridged) ?? 0, number(values.abridged) ?? 0, number(values.estimate) ?? 0, 1)
          const bar = (seconds: number | null) => `${Math.round(((seconds ?? 0) / longest) * 100)}%`
          return (
            <article className={`pb t-${tone}`} key={problem.id} data-problem={problem.id} data-code={problem.code}>
              <MiniCover book={problem.book} />
              <div className="pb-main">
                <div className="pb-top">
                  <span className={`rchip big ${tone}`}>{t(`acquire.needs.label.${head}`)}</span>
                  <span className="pb-book" dir="auto">
                    {problem.book.title}
                    {problem.book.authors[0] ? ` · ${problem.book.authors[0]}` : ''} · {t(`procure.releases.kind.${problem.kind}`)}
                  </span>
                  <span className="pb-when">{ago(problem.at, i18n.language, t('acquire.justNow'))}</span>
                </div>
                <h3>{unclear ? t(`acquire.needs.unclear.case.${unclear.kind}.head`, unclearWords) : t(`acquire.needs.head.${head}`, words)}</h3>
                <p className="pb-text">{unclear ? t(`acquire.needs.unclear.case.${unclear.kind}.text`, unclearWords) : t(`acquire.needs.text.${code === 'bundle' && values.whole === true ? 'bundleWhole' : code === 'bundle' && values.wanted == null ? 'bundleAny' : head}`, words)}</p>
                {code === 'paused' && (
                  <div className="small-note" data-testid="paused-why">
                    {Array.isArray(values.why) && values.why.length > 0 ? (
                      <>
                        {t('acquire.needs.pausedWhy.lead')}
                        <ul>
                          {values.why.map((label) => {
                            const key = whyKey(String(label))
                            return <li key={String(label)} dir="auto">{key && i18n.exists(`acquire.needs.pausedWhy.${key}`) ? t(`acquire.needs.pausedWhy.${key}`) : t('acquire.needs.pausedWhy.other', { label: String(label) })}</li>
                          })}
                        </ul>
                      </>
                    ) : (
                      t('acquire.needs.pausedWhy.none')
                    )}
                  </div>
                )}
                {leftOut.length > 0 && <p className="pb-text" data-left-out dir="auto">{t('acquire.needs.leftOut', { names: leftOut.join(', ') })}</p>}
                {code === 'path_not_found' && (
                  <>
                    {proposal && (
                      <div className="pair">
                        <div>
                          <small>{t('acquire.needs.mapping.client', { client: problem.client })}</small>
                          <code>{iso(proposal.remote)}</code>
                        </div>
                        <span className="pa">
                          <ArrowRight aria-hidden className="ic" />
                        </span>
                        <div>
                          <small>{t('acquire.needs.mapping.here')}</small>
                          <code>{iso(proposal.local)}</code>
                        </div>
                      </div>
                    )}
                    {/* What to do, in one sentence: the operator checks what nexbooks found and confirms it. */}
                    {problem.can_map && (values.search === 'proposed' || values.search === 'twice') && (
                      <p className="pb-text" data-testid="mapping-todo">
                        <b>{t(`acquire.needs.mapping.todo.${values.search === 'twice' ? 'twice' : 'proposed'}`)}</b>
                      </p>
                    )}
                    {!problem.can_map && <p className="small-note">{t('acquire.needs.askOperator')}</p>}
                  </>
                )}
                {code === 'runtime' && (
                  <div className="rt" data-testid="runtime-bars">
                    <div>
                      <small>{t('acquire.needs.runtime.loaded')}</small>
                      <i style={{ width: bar(number(values.loaded)) }} />
                      <b>{hours(number(values.loaded))}</b>
                    </div>
                    {number(values.abridged) != null && (
                      <div>
                        <small>{t('acquire.needs.runtime.abridged')}</small>
                        <i className="ref" style={{ width: bar(number(values.abridged)) }} />
                        <b>{hours(number(values.abridged))}</b>
                      </div>
                    )}
                    {number(values.unabridged) != null && (
                      <div>
                        <small>{t('acquire.needs.runtime.unabridged')}</small>
                        <i className="ref" style={{ width: bar(number(values.unabridged)) }} />
                        <b>{hours(number(values.unabridged))}</b>
                      </div>
                    )}
                    {/* No catalogue names an unabridged time: a rough one from the pages of the book, else the word that none is known. */}
                    {number(values.unabridged) == null && number(values.estimate) != null && (
                      <div data-testid="runtime-estimate">
                        <small>{t('acquire.needs.runtime.estimated', { pages: number(values.pages) ?? 0 })}</small>
                        <i className="ref" style={{ width: bar(number(values.estimate)) }} />
                        <b>{t('acquire.needs.runtime.about', { time: hours(number(values.estimate)) })}</b>
                      </div>
                    )}
                    {number(values.unabridged) == null && number(values.abridged) == null && number(values.estimate) == null && (
                      <div className="unknown" data-testid="runtime-unknown">
                        <small>{t('acquire.needs.runtime.unknown')}</small>
                      </div>
                    )}
                  </div>
                )}
                {code === 'bundle' && units.length > 0 && (
                  <div className="vols" role="radiogroup" aria-label={t('acquire.needs.bundle.pick')}>
                    {units.map((unit, index) => (
                      <label className={`vol ${unit.unit === choice ? 'on' : ''}`} key={unit.unit}>
                        <input type="radio" name={`vol-${problem.id}`} value={unit.unit} checked={unit.unit === choice} onChange={() => setPicked((before) => ({ ...before, [problem.id]: unit.unit }))} />
                        <span>
                          <b>{unit.volume != null ? t('acquire.needs.bundle.volume', { volume: unit.volume }) : t('acquire.needs.bundle.book', { number: index + 1 })}</b>
                          <small dir="auto">
                            {unit.title}
                            {unit.authors[0] ? ` · ${unit.authors[0]}` : ''}
                          </small>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
                {code === 'files_unassigned' && (
                  <p className="small-note" dir="auto">
                    {t('acquire.needs.unassigned.says', { title: String(values.title ?? '') || t('acquire.needs.unassigned.noTitle'), authors: (Array.isArray(values.authors) ? values.authors : []).join(', ') || t('acquire.needs.unassigned.noAuthor') })}
                  </p>
                )}
                {(code === 'edition' || code === 'partial') && values.title ? (
                  <p className="small-note" dir="auto" data-testid="special-says">
                    {t('acquire.needs.special.says', { title: String(values.title) })}
                  </p>
                ) : null}
                {code === 'pen_name' && (
                  <p className="small-note" dir="auto" data-testid="pen-name-says">
                    {t('acquire.needs.penName.says', { found: (Array.isArray(values.found) ? values.found : []).join(', ') || t('acquire.needs.unassigned.noAuthor'), wanted: (Array.isArray(values.wanted) ? values.wanted : []).join(', ') || t('acquire.needs.unassigned.noAuthor') })}
                  </p>
                )}
                {code === 'language_unclear' && (
                  <p className="small-note" data-testid="language-unclear">
                    {values.file ? <code>{problem.kind === 'audio' ? 'language' : 'dc:language'} = {String(values.file)}</code> : null}
                    {values.text === 'mixed' || values.text === 'other' ? ` ${t(`acquire.needs.unclear.${values.text}`)}` : values.text ? ` ${t('acquire.needs.unclear.text', words)}` : ''}
                    {values.name ? ` ${t('acquire.needs.unclear.name', words)}` : ''}
                    {values.folder ? ` ${t('acquire.needs.unclear.folder', words)}` : ''}
                  </p>
                )}
                <p className="rel" dir="auto">
                  {problem.release}
                </p>
                {QUESTIONS.includes(code) && <DownloadLook downloadId={problem.id} kind={problem.kind} title={problem.book.title} />}
                {code === 'language' && values.file ? (
                  <p className="small-note">
                    {t('acquire.needs.language.inFile')} <code>dc:language = {String(values.file)}</code>
                  </p>
                ) : null}
                <div className="pb-act">
                  {code === 'path_not_found' && problem.can_map && (
                    <button type="button" className="bbtn small primary" onClick={() => setMapping(problem)} data-act="mapping">
                      {t('acquire.needs.mapping.open')}
                    </button>
                  )}
                  {code === 'language' && (
                    <>
                      <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'acknowledge'))} data-act="ok">
                        {t('acquire.needs.ok')}
                      </button>
                      {onReleases && (problem.book.work_id != null || problem.book.book) && (
                        <button type="button" className="bbtn small" onClick={() => onReleases(problem)} data-act="releases">
                          {t('procure.releases.open')}
                        </button>
                      )}
                    </>
                  )}
                  {code === 'runtime' && (
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'keep'))} data-act="keep">
                      {t(values.reason === 'too_long' ? 'acquire.needs.runtime.keepLong' : values.reason === 'dramatized' ? 'acquire.needs.runtime.keepDramatized' : values.reason === 'unproven' || values.reason === 'shorter' || values.reason === 'genre' ? 'acquire.needs.runtime.keepUnproven' : 'acquire.needs.runtime.keep')}
                    </button>
                  )}
                  {code === 'bundle' && values.whole === true && (
                    <button type="button" className="bbtn small" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'whole'))} data-act="whole">
                      {t('acquire.needs.bundle.whole')}
                    </button>
                  )}
                  {code === 'bundle' && choice && (
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'pick', choice))} data-act="pick">
                      {chosen?.volume != null ? t('acquire.needs.bundle.keep', { volume: chosen.volume }) : t('acquire.needs.bundle.keepThis')}
                    </button>
                  )}
                  {TAKE.includes(code) && (
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'take'))} data-act="take">
                      {t(code === 'files_unassigned' ? 'acquire.needs.unassigned.take' : code === 'pen_name' ? 'acquire.needs.penName.take' : 'acquire.needs.special.take')}
                    </button>
                  )}
                  {code === 'same_edition' && (
                    <>
                      <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'take'))} data-act="take">
                        {t('acquire.needs.sameEdition.replace')}
                      </button>
                      <button type="button" className="bbtn small" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'keep'))} data-act="keep">
                        {t('acquire.needs.sameEdition.keep')}
                      </button>
                    </>
                  )}
                  {(code === 'language_unclear' || code === 'language_unknown') && (
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.decide(problem.id, 'take'))} data-act="take">
                      {t('acquire.needs.unclear.take', words)}
                    </button>
                  )}
                  {BLOCKABLE.includes(code) && (
                    <button type="button" className={`bbtn small ${code === 'paused' ? 'primary' : ''}`} disabled={doing.busy} onClick={() => block(problem)} data-act="block">
                      {t(`acquire.needs.block.${code}.button`)}
                    </button>
                  )}
                  {RECHECK.includes(code) && (
                    <button type="button" className={`bbtn small ${code === 'no_write' ? 'primary' : ''}`} disabled={doing.busy} onClick={() => void doing.run(() => procureApi.recheck(problem.id))} data-act="recheck">
                      {t('acquire.needs.recheck')}
                    </button>
                  )}
                  {code === 'no_write' && problem.protocol !== 'upload' && (
                    <button type="button" className="bbtn small" onClick={() => setLibrary(problem)} data-act="library">
                      {t('acquire.needs.library.open')}
                    </button>
                  )}
                  {(RECHECK.includes(code) || code === 'paused') && (
                    <button type="button" className="bbtn small" onClick={() => setCancelling(problem)} data-act="cancel">
                      {t('acquire.needs.cancel')}
                    </button>
                  )}
                </div>
              </div>
            </article>
          )
        })}
        {notes.map((note) => {
          if (note.code === 'works_joined' || note.code === 'upgrade_replaced') {
            const joined = note.code === 'works_joined'
            const words = { title: note.detail.title ?? '', into: note.detail.into ?? '', old: fmt(note.detail.from), now: fmt(note.detail.to) }
            return (
              <article className="pb t-warn" key={`n${note.id}`} data-note={note.id} data-code={note.code}>
                <MiniCover book={note.book} />
                <div className="pb-main">
                  <div className="pb-top">
                    <span className="rchip big warn">{t(`acquire.needs.label.${note.code}`)}</span>
                    <span className="pb-book" dir="auto">
                      {note.book.title}
                      {note.book.authors[0] ? ` · ${note.book.authors[0]}` : ''}
                    </span>
                    <span className="pb-when">{ago(note.at, i18n.language, t('acquire.justNow'))}</span>
                  </div>
                  <h3 dir="auto">{t(`acquire.needs.head.${note.code}`, words)}</h3>
                  <p className="pb-text">{t(`acquire.needs.text.${note.code}`, words)}</p>
                  {note.release && (
                    <p className="rel" dir="auto">
                      {note.release}
                    </p>
                  )}
                  <div className="pb-act">
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.acknowledgeNote(note.id))} data-act="ok">
                      {t('acquire.needs.ok')}
                    </button>
                    {joined && note.edition_id != null && (
                      <button type="button" className="bbtn small" onClick={() => split(note)} data-act="split">
                        {t('acquire.needs.joined.split')}
                      </button>
                    )}
                    {!joined && note.can_restore && (
                      <button type="button" className="bbtn small" onClick={() => restore(note)} data-act="restore">
                        {t('acquire.needs.restore.button', words)}
                      </button>
                    )}
                  </div>
                </div>
              </article>
            )
          }
          if (note.code === 'duplicate') {
            const choice = note.detail.why === 'same_format' && note.detail.here && note.detail.there && note.detail.here !== note.detail.there
            const words = { title: note.detail.title ?? '', other: note.detail.other_title ?? '', here: note.detail.here ?? '', there: note.detail.there ?? '' }
            return (
              <article className="pb t-warn" key={`n${note.id}`} data-note={note.id} data-code={note.code} data-why={note.detail.why}>
                <MiniCover book={note.book} />
                <div className="pb-main">
                  <div className="pb-top">
                    <span className="rchip big warn">{t('acquire.needs.label.duplicate')}</span>
                    <span className="pb-book" dir="auto">
                      {note.book.title}
                      {note.book.authors[0] ? ` · ${note.book.authors[0]}` : ''}
                    </span>
                    <span className="pb-when">{ago(note.at, i18n.language, t('acquire.justNow'))}</span>
                  </div>
                  <h3 dir="auto">{t(`acquire.needs.duplicate.${note.detail.why ?? 'same_files'}`, words)}</h3>
                  <p className="pb-text">{t(choice ? 'acquire.needs.text.duplicateChoice' : 'acquire.needs.text.duplicate')}</p>
                  <p className="rel" dir="auto">
                    {words.here}
                    {words.there && words.there !== words.here ? ` · ${words.there}` : ''}
                  </p>
                  <div className="pb-act">
                    {choice &&
                      [words.here, words.there].map((folder) => (
                        <button key={folder} type="button" className="bbtn small" disabled={doing.busy} onClick={() => dropCopy(note, folder)} data-act="drop" dir="auto">
                          {t('acquire.needs.drop.button', { name: folderName(folder) })}
                        </button>
                      ))}
                    {!choice && (
                      <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.acknowledgeNote(note.id))} data-act="ok">
                        {t('acquire.needs.ok')}
                      </button>
                    )}
                  </div>
                </div>
              </article>
            )
          }
          if (note.code === 'kindle_failed') {
            const reason = ['mail', 'no_address', 'off', 'not_sendable', 'unreadable'].includes(String(note.detail.reason)) ? String(note.detail.reason) : 'mail'
            return (
              <article className="pb t-warn" key={`n${note.id}`} data-note={note.id} data-code={note.code} data-reason={reason}>
                <MiniCover book={note.book} />
                <div className="pb-main">
                  <div className="pb-top">
                    <span className="rchip big warn">{t('acquire.needs.label.kindle_failed')}</span>
                    <span className="pb-book" dir="auto">
                      {note.book.title}
                      {note.book.authors[0] ? ` · ${note.book.authors[0]}` : ''}
                    </span>
                    <span className="pb-when">{ago(note.at, i18n.language, t('acquire.justNow'))}</span>
                  </div>
                  <h3>{t('acquire.needs.head.kindle_failed')}</h3>
                  <p className="pb-text">{t(`acquire.needs.text.kindle_failed_${reason}`)}</p>
                  <div className="pb-act">
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.acknowledgeNote(note.id))} data-act="ok">
                      {t('acquire.needs.ok')}
                    </button>
                  </div>
                </div>
              </article>
            )
          }
          if (note.code === 'folder_kept') {
            const client = note.detail.client ?? ''
            return (
              <article className="pb t-quiet" key={`n${note.id}`} data-note={note.id} data-code={note.code}>
                <MiniCover book={note.book} />
                <div className="pb-main">
                  <div className="pb-top">
                    <span className="rchip big">{t('acquire.needs.label.folder_kept')}</span>
                    <span className="pb-book" dir="auto">
                      {note.book.title}
                      {note.book.authors[0] ? ` · ${note.book.authors[0]}` : ''}
                    </span>
                    <span className="pb-when">{ago(note.at, i18n.language, t('acquire.justNow'))}</span>
                  </div>
                  <h3>{t('acquire.needs.head.folder_kept', { client })}</h3>
                  <p className="pb-text">{t('acquire.needs.text.folder_kept', { client })}</p>
                  {note.release && (
                    <p className="rel" dir="auto">
                      {note.release}
                    </p>
                  )}
                  <div className="pb-act">
                    <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.acknowledgeNote(note.id))} data-act="ok">
                      {t('acquire.needs.ok')}
                    </button>
                  </div>
                </div>
              </article>
            )
          }
          const fraction = Math.round((note.detail.fraction ?? 0) * 100)
          const words = { old: fmt(note.detail.from), now: fmt(note.detail.to), oldArticle: article(fmt(note.detail.from)), OldArticle: article(fmt(note.detail.from), true), nowArticle: article(fmt(note.detail.to)), page: note.detail.page ?? '', pages: note.detail.pages ?? '', percent: fraction }
          const paged = note.detail.page != null
          return (
            <article className="pb t-warn" key={`n${note.id}`} data-note={note.id} data-code={note.code}>
              <MiniCover book={note.book} />
              <div className="pb-main">
                <div className="pb-top">
                  <span className="rchip big warn">{t('acquire.needs.label.upgrade_position')}</span>
                  <span className="pb-book" dir="auto">
                    {note.book.title}
                    {note.book.authors[0] ? ` · ${note.book.authors[0]}` : ''} · {t('procure.releases.kind.ebook')}
                  </span>
                  <span className="pb-when">{ago(note.at, i18n.language, t('acquire.justNow'))}</span>
                </div>
                <h3>{t('acquire.needs.head.upgrade_position', words)}</h3>
                <p className="pb-text">{t(paged ? 'acquire.needs.text.upgrade_position_page' : 'acquire.needs.text.upgrade_position', words)}</p>
                <div className="rt">
                  <div>
                    <small>{t('acquire.needs.position.before', words)}</small>
                    <i className="ref" style={{ width: `${fraction}%` }} />
                    <b>{paged && note.detail.pages ? t('acquire.needs.position.page', words) : `${fraction} %`}</b>
                  </div>
                  <div>
                    <small>{t('acquire.needs.position.now', words)}</small>
                    <i style={{ width: `${fraction}%` }} />
                    <b>{t('acquire.needs.position.about', words)}</b>
                  </div>
                </div>
                {note.release && (
                  <p className="rel" dir="auto">
                    {note.release}
                  </p>
                )}
                <div className="pb-act">
                  <button type="button" className="bbtn small primary" disabled={doing.busy} onClick={() => void doing.run(() => procureApi.acknowledgeNote(note.id))} data-act="ok">
                    {t('acquire.needs.ok')}
                  </button>
                  {note.can_restore && (
                    <button type="button" className="bbtn small" onClick={() => restore(note)} data-act="restore">
                      {t('acquire.needs.restore.button', words)}
                    </button>
                  )}
                </div>
              </div>
            </article>
          )
        })}
        {foreign.map((job) => (
          <article className="pb t-quiet" key={`f${job.id}`} data-foreign={job.id}>
            <div className="mcv unk">
              <span>?</span>
            </div>
            <div className="pb-main">
              <div className="pb-top">
                <span className="rchip big">{t('acquire.needs.label.foreign')}</span>
                <span className="pb-when">{ago(job.first_seen_at, i18n.language, t('acquire.justNow'))}</span>
              </div>
              <h3>{t('acquire.needs.head.foreign', { client: job.client })}</h3>
              <p className="pb-text">{t('acquire.needs.text.foreign')}</p>
              <p className="rel" dir="auto">
                {job.name}
              </p>
              <div className="pb-act">
                <button type="button" className="bbtn small primary" onClick={() => setAssigning(job)} data-act="assign">
                  {t('acquire.needs.foreign.assign.button')}
                </button>
                <button type="button" className="bbtn small" onClick={() => ignore(job)} data-act="ignore">
                  {t('acquire.needs.foreign.ignore.button')}
                </button>
                <button type="button" className="bbtn small" onClick={() => removeJob(job)} data-act="remove-job">
                  {t('acquire.needs.foreign.remove.button')}
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {mapping && <MappingDialog id={mapping.id} client={mapping.client} onClose={() => setMapping(null)} onDone={() => void onChanged()} />}
      {library && <LibraryDialog item={library} current={library.library?.id ?? null} onClose={() => setLibrary(null)} onDone={() => void onChanged()} />}
      {cancelling && <CancelDialog item={cancelling} onClose={() => setCancelling(null)} onDone={() => void onChanged()} />}
      {assigning && <AssignDialog job={assigning} onClose={() => setAssigning(null)} onDone={() => void onChanged()} />}
      {confirm.dialog}
    </>
  )
}
