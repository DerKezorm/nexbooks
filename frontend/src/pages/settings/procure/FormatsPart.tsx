/**
 * Settings, Fetching, Formats and places (the operator's): for each kind one ranking with a tick for every format (the top
 * line is taken first, a format that is not ticked is never fetched) and a goal, the languages, the version of an
 * audiobook, the size of a release, the library the books go into (with a check that nexbooks may write there) and the
 * naming with a preview. A change is saved at once.
 */
import { AlertTriangle, ArrowDown, ArrowUp, BookOpen, Check, FolderTree, GripVertical, Headphones, Info, Languages, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, procureApi, type FormatsConfig, type FormatsOut, type LibraryCheck, type LibraryKind } from '../../../api/client'
import { errorText } from '../../../lib/errors'
import { Button, Card, Feedback, Segment, Toggle, useAction } from '../ui'
import { shortWhy } from '../../../components/procure/writeWhy'
import { Code } from './bits'
import { TrashCard } from './TrashCard'
import { useLanguageNames } from './helpers'

type Kind = 'ebook' | 'audio'

/** The variables of a naming scheme, as the page writes them in the language of the page. */
const VARIABLES = ['Author', 'Title', 'Series', 'Volume', 'Year'] as const

export function FormatsPart() {
  const { t } = useTranslation()
  const [out, setOut] = useState<FormatsOut | null>(null)
  const { problem, values, done, run } = useAction()
  /** What the server has confirmed last, and the saves that are on their way (see `commit`). */
  const saved = useRef<FormatsOut | null>(null)
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const waiting = useRef(0)
  useEffect(() => {
    procureApi.formats().then((found) => {
      saved.current = found
      setOut(found)
    }, () => undefined)
  }, [])
  /**
   * Saves the whole choice; shows the new one at once and goes back to the saved one when the server says no. Saves go
   * **one after the other** in the order of the clicks: a slow answer can never overtake a later one, so the last click is
   * the one that stands (the server holds what was sent last, and the page shows what the server confirmed last).
   */
  const commit = useCallback(
    (next: FormatsConfig): Promise<boolean> => {
      setOut((now) => (now ? { ...now, ...next } : now))
      waiting.current += 1
      const job = queue.current.then(async () => {
        const ok = await run(async () => {
          const found = await procureApi.saveFormats(next)
          saved.current = found
          if (waiting.current === 1) setOut(found)
        }, t('settings.saved'))
        waiting.current -= 1
        if (!ok && waiting.current === 0 && saved.current) setOut(saved.current)
        return ok
      })
      queue.current = job
      return job
    },
    [run, t],
  )
  if (!out) return <p className="text-sm text-muted">{t('procure.loading')}</p>
  const config: FormatsConfig = { ebook: out.ebook, audio: out.audio, target: out.target, languages: out.languages, original: out.original, variant: out.variant, abridged: out.abridged, dramatized: out.dramatized, size: out.size, library: out.library, schema: out.schema, trash_days: out.trash_days }
  const change = (part: Partial<FormatsConfig>) => commit({ ...config, ...part })
  return (
    <div className="space-y-6">
      <div className="pt-2">
        <h2 className="font-display text-xl font-semibold">{t('procure.formats.heading')}</h2>
        <p className="text-sm text-ink-2">{t('procure.formats.text')}</p>
        <div className="mt-2" data-testid="formats-feedback">
          <Feedback problem={problem} done={done} values={values} />
        </div>
      </div>
      <div className="space-y-6">
        <RankCard kind="ebook" out={out} change={change} />
        <RankCard kind="audio" out={out} change={change} />
        <LanguagesCard out={out} change={change} />
        <VariantCard out={out} change={change} />
        <SizeCard out={out} change={change} />
        <PlaceCard out={out} change={change} />
        <NamingCard out={out} change={change} />
        <TrashCard days={out.trash_days ?? 30} onDays={(days) => change({ trash_days: days })} />
      </div>
    </div>
  )
}

type CardProps = { out: FormatsOut; change: (part: Partial<FormatsConfig>) => Promise<boolean> }

function RankCard({ kind, out, change }: CardProps & { kind: Kind }) {
  const { t } = useTranslation()
  const list = out[kind]
  const target = out.target[kind]
  const drag = useRef<number | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const move = (from: number, to: number) => {
    if (to < 0 || to >= list.length || from === to) return
    const next = [...list]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    void change({ [kind]: next } as Partial<FormatsConfig>)
  }
  const tick = (index: number, on: boolean) => {
    if (!on && list.filter((item) => item.on).length === 1) return setNote(t('procure.formats.keepOne'))
    setNote(null)
    const next = list.map((item, at) => (at === index ? { ...item, on } : item))
    // The goal stays a format that is allowed: when it is the one just unticked, the top ticked line takes over.
    const goal = next.find((item) => item.f === target && item.on) ? target : (next.find((item) => item.on)?.f ?? target)
    void change({ [kind]: next, target: { ...out.target, [kind]: goal } } as Partial<FormatsConfig>)
  }
  const goalAt = list.findIndex((item) => item.f === target)
  const allowed = list.filter((item) => item.on)
  return (
    <Card id={`formats-${kind}`} icon={kind === 'ebook' ? BookOpen : Headphones} title={t(`procure.formats.${kind}.title`)} text={t(`procure.formats.${kind}.text`)}>
      <ol aria-label={t(`procure.formats.${kind}.list`)} data-testid={`rank-${kind}`} className="space-y-1.5">
        {list.map((item, index) => (
          <li key={item.f} className="contents">
            <div
              draggable
              data-testid={`rank-${kind}-${item.f}`}
              onDragStart={() => (drag.current = index)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (drag.current !== null) move(drag.current, index)
                drag.current = null
              }}
              className={`flex flex-wrap items-center gap-2 rounded-xl border border-line px-2.5 py-2 text-sm ${item.on ? 'bg-sheet' : 'bg-sheet-2/50 opacity-70'}`}
            >
              <GripVertical size={16} aria-hidden className="shrink-0 cursor-grab text-muted" />
              <span className="w-6 shrink-0 font-mono text-xs text-muted">{String(index + 1).padStart(2, '0')}</span>
              <span className="min-w-0 flex-1">
                <b>{t(`procure.formats.name.${item.f}`)}</b>
                <small className="block text-xs text-muted">
                  {[t(`procure.formats.hint.${item.f}`), index === 0 && item.on ? t('procure.formats.first') : '', !item.on ? t('procure.formats.never') : ''].filter(Boolean).join(' · ')}
                </small>
              </span>
              <button type="button" disabled={index === 0} onClick={() => move(index, index - 1)} aria-label={t('procure.formats.up', { name: t(`procure.formats.name.${item.f}`) })} className="rounded-full border border-line p-1.5 text-muted hover:bg-sheet-2 disabled:opacity-40">
                <ArrowUp size={14} aria-hidden />
              </button>
              <button type="button" disabled={index === list.length - 1} onClick={() => move(index, index + 1)} aria-label={t('procure.formats.down', { name: t(`procure.formats.name.${item.f}`) })} className="rounded-full border border-line p-1.5 text-muted hover:bg-sheet-2 disabled:opacity-40">
                <ArrowDown size={14} aria-hidden />
              </button>
              <label className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold">
                <input type="checkbox" checked={item.on} onChange={(event) => tick(index, event.target.checked)} aria-label={t('procure.formats.allowOf', { name: t(`procure.formats.name.${item.f}`) })} className="h-4 w-4 accent-[var(--accent)]" />
                {t('procure.formats.allowed')}
              </label>
            </div>
            {index === goalAt && (
              // The goal line says what lies on each side of it: above it (the goal included) is good enough, below it
              // nexbooks takes what it finds and goes on looking for something better. One text on each side, so that
              // neither can be read as the label of the lines under it.
              <div data-testid={`goal-line-${kind}`} role="separator" aria-label={t('procure.formats.goalLine', { name: t(`procure.formats.name.${item.f}`) })} className="text-right text-[0.68rem] font-bold tracking-wide text-accent">
                <span data-testid={`goal-above-${kind}`} className="block pb-0.5">
                  {t('procure.formats.goalAbove')}
                </span>
                <hr data-testid={`goal-rule-${kind}`} aria-hidden className="border-0 border-t-2 border-dashed border-accent/60" />
                {list.slice(index + 1).some((below) => below.on) && (
                  <span data-testid={`goal-below-${kind}`} className="block pt-0.5 font-semibold text-ink-2">
                    {t('procure.formats.goalBelow')}
                  </span>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">
          <span className="font-semibold">{t('procure.formats.goal')}</span>
          <select value={target} onChange={(event) => void change({ target: { ...out.target, [kind]: event.target.value } })} className="h-9 rounded-xl border border-line bg-sheet px-2 text-ink">
            {allowed.map((item) => (
              <option key={item.f} value={item.f}>
                {t(`procure.formats.name.${item.f}`)}
              </option>
            ))}
          </select>
        </label>
        <small className="text-muted">{t('procure.formats.goalHint', { name: t(`procure.formats.name.${target}`) })}</small>
      </div>
      {note && (
        <p role="status" className="text-sm font-semibold text-warn">
          {note}
        </p>
      )}
      <p className="flex items-start gap-2 text-sm text-ink-2">
        <Info size={15} aria-hidden className="mt-0.5 shrink-0 text-accent" />
        {t('procure.formats.rule.upgrade')}
      </p>
      <p className="flex items-start gap-2 text-sm text-ink-2">
        <ShieldCheck size={15} aria-hidden className="mt-0.5 shrink-0 text-accent" />
        {t('procure.formats.rule.never')}
      </p>
    </Card>
  )
}

function LanguagesCard({ out, change }: CardProps) {
  const { t } = useTranslation()
  const name = useLanguageNames()
  // The ranking of the languages: the ticked ones in the operator's order, "Originalsprache" at its place among
  // them; the first is preferred, and a release in it beats any format in a language below it.
  const ranked = out.languages.includes('original') || !out.original ? out.languages : [...out.languages, 'original']
  const save = (next: string[]) => change({ languages: next, original: next.includes('original') })
  const move = (from: number, to: number) => {
    if (to < 0 || to >= ranked.length || from === to) return
    const next = [...ranked]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    void save(next)
  }
  const others = [...out.known_languages.filter((code) => code !== 'mixed'), 'original'].filter((code) => !ranked.includes(code))
  const chip = 'rounded-full border border-line bg-sheet px-3 py-1 text-sm font-semibold text-ink-2 hover:text-ink'
  return (
    <Card id="formats-languages" icon={Languages} title={t('procure.formats.languages.title')} text={t('procure.formats.languages.text')}>
      <span className="block text-sm font-semibold">{t('procure.formats.languages.title')}</span>
      <ol aria-label={t('procure.formats.languages.list')} data-testid="rank-languages" className="space-y-1.5">
        {ranked.map((code, index) => (
          <li key={code} data-testid={`rank-language-${code}`} className="flex items-center gap-2 rounded-xl border border-line bg-sheet px-2.5 py-2 text-sm">
            <span className="w-6 shrink-0 font-mono text-xs text-muted">{String(index + 1).padStart(2, '0')}</span>
            <span className="min-w-0 flex-1">
              <b>{name(code)}</b>
              {index === 0 && <small className="block text-xs text-muted">{t('procure.formats.languages.first')}</small>}
            </span>
            <button type="button" disabled={index === 0} onClick={() => move(index, index - 1)} aria-label={t('procure.formats.up', { name: name(code) })} className="rounded-full border border-line p-1.5 text-muted hover:bg-sheet-2 disabled:opacity-40">
              <ArrowUp size={14} aria-hidden />
            </button>
            <button type="button" disabled={index === ranked.length - 1} onClick={() => move(index, index + 1)} aria-label={t('procure.formats.down', { name: name(code) })} className="rounded-full border border-line p-1.5 text-muted hover:bg-sheet-2 disabled:opacity-40">
              <ArrowDown size={14} aria-hidden />
            </button>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold">
              <input type="checkbox" checked onChange={() => void save(ranked.filter((item) => item !== code))} aria-label={t('procure.formats.allowOf', { name: name(code) })} className="h-4 w-4 accent-[var(--accent)]" />
              {t('procure.formats.allowed')}
            </label>
          </li>
        ))}
      </ol>
      {others.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('procure.formats.languages.add')}>
          <span className="text-xs text-muted">{t('procure.formats.languages.add')}</span>
          {others.map((code) => (
            <button key={code} type="button" aria-pressed={false} onClick={() => void save([...ranked, code])} className={chip}>
              {name(code)}
            </button>
          ))}
        </div>
      )}
      <p className="text-sm text-muted">{t('procure.formats.languages.hint')}</p>
    </Card>
  )
}

function VariantCard({ out, change }: CardProps) {
  const { t } = useTranslation()
  return (
    <Card id="formats-variant" icon={Headphones} title={t('procure.formats.variant.title')} text={t('procure.formats.variant.text')}>
      <span className="block text-sm font-semibold">{t('procure.formats.variant.label')}</span>
      <Segment
        tint
        label={t('procure.formats.variant.label')}
        value={out.variant}
        options={[
          { value: 'unabridged', label: t('procure.formats.variant.unabridged') },
          { value: 'prefer', label: t('procure.formats.variant.prefer') },
        ]}
        onChange={(variant) => void change({ variant, ...(variant === 'unabridged' ? { abridged: false } : {}) })}
      />
      <Toggle label={t('procure.formats.variant.abridged')} hint={t('procure.formats.variant.abridgedHint')} checked={out.abridged && out.variant !== 'unabridged'} disabled={out.variant === 'unabridged'} onChange={(abridged) => void change({ abridged })} />
      <Toggle label={t('procure.formats.variant.dramatized')} hint={t('procure.formats.variant.dramatizedHint')} checked={out.dramatized} onChange={(dramatized) => void change({ dramatized })} />
    </Card>
  )
}

function SizeCard({ out, change }: CardProps) {
  const { t } = useTranslation()
  return (
    <Card id="formats-size" icon={ShieldCheck} title={t('procure.formats.size.title')} text={t('procure.formats.size.text')}>
      {(['ebook', 'audio'] as const).map((kind) => (
        <SizeRow key={kind} kind={kind} range={out.size[kind]} onChange={(range) => void change({ size: { ...out.size, [kind]: range } })} />
      ))}
    </Card>
  )
}

function SizeRow({ kind, range, onChange }: { kind: Kind; range: number[]; onChange: (range: number[]) => void }) {
  const { t } = useTranslation()
  const [from, setFrom] = useState(String(range[0]))
  const [to, setTo] = useState(String(range[1]))
  useEffect(() => {
    setFrom(String(range[0]))
    setTo(String(range[1]))
  }, [range])
  const commit = () => {
    const low = Number(from)
    const high = Number(to)
    if (Number.isFinite(low) && Number.isFinite(high) && (low !== range[0] || high !== range[1])) onChange([low, high])
  }
  const field = 'mt-1 h-10 w-28 rounded-xl border border-line bg-sheet px-3 text-ink outline-none focus:border-accent'
  return (
    <div className="text-sm">
      <span className="font-semibold">{t(`procure.formats.size.${kind}`)}</span>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2">
          {t('procure.formats.size.from')}
          <input type="number" step="any" min={0} value={from} onChange={(event) => setFrom(event.target.value)} onBlur={commit} aria-label={t('procure.formats.size.minLabel', { kind: t(`procure.formats.size.${kind}`) })} className={field} />
          MB
        </label>
        <label className="flex items-center gap-2">
          {t('procure.formats.size.to')}
          <input type="number" step="any" min={0} value={to} onChange={(event) => setTo(event.target.value)} onBlur={commit} aria-label={t('procure.formats.size.maxLabel', { kind: t(`procure.formats.size.${kind}`) })} className={field} />
          MB
        </label>
      </div>
    </div>
  )
}

const WHY = ['missing', 'read_only', 'no_permission', 'write_failed']

function PlaceCard({ out, change }: CardProps) {
  const { t } = useTranslation()
  return (
    <Card id="formats-place" icon={FolderTree} title={t('procure.formats.place.title')} text={t('procure.formats.place.text')}>
      {(['ebook', 'audio'] as const).map((kind) => (
        <PlaceRow key={kind} kind={kind} out={out} change={change} />
      ))}
      {out.libraries.length > 0 && out.libraries.every((item) => !item.writable) && (
        <p role="alert" className="text-sm text-bad" data-testid="place-none-writable">
          {t('procure.formats.place.writableNone')}
        </p>
      )}
    </Card>
  )
}

function PlaceRow({ kind, out, change }: CardProps & { kind: Kind }) {
  const { t } = useTranslation()
  const [check, setCheck] = useState<LibraryCheck | null>(null)
  const [checking, setChecking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const chosen = out.library[kind]
  const library = out.libraries.find((item) => item.id === chosen) ?? null
  const options = out.libraries.filter((item) => item.kind === 'both' || item.kind === (kind as LibraryKind))
  const writableOthers = options.filter((item) => item.writable && item.id !== chosen)
  useEffect(() => {
    setCheck(null)
    setProblem(null)
  }, [chosen])
  const probe = async () => {
    if (chosen === null) return
    setChecking(true)
    setProblem(null)
    try {
      setCheck(await procureApi.checkLibrary(chosen))
    } catch (error) {
      setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error'))
    } finally {
      setChecking(false)
    }
  }
  return (
    <div className="space-y-2 text-sm">
      <span className="font-semibold">{t(`procure.formats.place.${kind}`)}</span>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={chosen ?? ''}
          aria-label={t(`procure.formats.place.${kind}`)}
          onChange={(event) => void change({ library: { ...out.library, [kind]: event.target.value === '' ? null : Number(event.target.value) } })}
          className="h-10 min-w-0 flex-1 rounded-xl border border-line bg-sheet px-3 text-ink"
        >
          <option value="">{t('procure.formats.place.none')}</option>
          {options.map((item) => (
            // A library nexbooks may not write into stays in the list, greyed out with the reason (it is chosen already,
            // or it becomes writable later); only the one chosen now can stay chosen.
            <option key={item.id} value={item.id} disabled={!item.writable && item.id !== chosen}>
              {item.writable ? item.name : t('procure.formats.place.unwritableOption', { name: item.name, why: shortWhy(item.write_problem, t) })}
            </option>
          ))}
        </select>
        <Button small busy={checking} disabled={chosen === null} onClick={() => void probe()}>
          {t('procure.formats.place.check')}
        </Button>
      </div>
      {library && !check && !problem && <Code>{library.path}</Code>}
      {library && library.writable === false && !check && (
        <div role="alert" data-testid={`unwritable-${kind}`} className="space-y-1">
          <p className="flex items-start gap-2 text-bad">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              {t(`procure.formats.place.problem.${WHY.includes(library.write_problem) ? library.write_problem : 'write_failed'}`, { name: library.name })}
            </span>
          </p>
          {writableOthers.length > 0 && <p className="text-muted">{t('procure.formats.place.writableOthers', { names: writableOthers.map((item) => item.name).join(', ') })}</p>}
        </div>
      )}
      {problem && (
        <p role="alert" className="text-bad">
          {problem}
        </p>
      )}
      {check && library && (
        <div data-testid={`check-${kind}`} className="space-y-1">
          <p className={`flex items-start gap-2 ${check.writable ? 'text-ok' : 'text-bad'}`}>
            {check.writable ? <Check size={15} className="mt-0.5 shrink-0" aria-hidden /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />}
            <span>
              <code className="font-mono text-xs">{library.path}</code>{' '}
              {check.writable ? (check.hardlink ? t('procure.formats.place.okLinks') : t('procure.formats.place.okNoLinks')) : t(`procure.formats.place.why.${WHY.includes(check.reason) ? check.reason : 'write_failed'}`)}
            </span>
          </p>
          {check.writable &&
            check.downloads.map((item) => (
              <p key={item.client} className={`text-xs ${item.same_filesystem === false ? 'text-warn' : 'text-muted'}`}>
                {item.seen ? (item.same_filesystem === false ? t('procure.formats.place.otherDisk', { client: item.client }) : t('procure.formats.place.sameDisk', { client: item.client })) : t('procure.formats.place.unseen', { client: item.client, folder: item.folder })}
              </p>
            ))}
        </div>
      )}
    </div>
  )
}

function NamingCard({ out, change }: CardProps) {
  const { t } = useTranslation()
  const [schema, setSchema] = useState(out.schema)
  const [sample, setSample] = useState(0)
  const [shown, setShown] = useState<{ ebook: string; audio: string } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useEffect(() => setSchema(out.schema), [out.schema])
  // The preview follows the typing, a moment after the last key.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      procureApi.previewNaming(schema, sample).then(
        (found) => {
          setShown(found)
          setProblem(null)
        },
        (error: unknown) => {
          setShown(null)
          setProblem(error instanceof ApiError && error.code === 'scheme_invalid' ? t(`organize.schemeError.${String(error.values.reason ?? '')}`, { detail: String(error.values.detail ?? ''), defaultValue: errorText('scheme_invalid') }) : errorText('internal_error'))
        },
      )
    }, 300)
    return () => window.clearTimeout(timer)
  }, [schema, sample, t, out.library, out.ebook])
  const save = () => {
    if (!problem && schema !== out.schema) void change({ schema })
  }
  return (
    <Card id="formats-naming" icon={SlidersHorizontal} title={t('procure.formats.naming.title')} text={t('procure.formats.naming.text')}>
      <label className="block text-sm">
        <span className="font-semibold">{t('procure.formats.naming.schema')}</span>
        <input
          value={schema}
          onChange={(event) => setSchema(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => event.key === 'Enter' && save()}
          spellCheck={false}
          className="mt-1 h-11 w-full rounded-xl border border-line bg-sheet px-3.5 font-mono text-[0.9rem] text-ink outline-none focus:border-accent"
        />
      </label>
      {problem && (
        <p role="alert" className="text-sm text-bad" data-testid="naming-problem">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap gap-2" aria-label={t('procure.formats.naming.variables')}>
        {VARIABLES.map((variable) => (
          <button key={variable} type="button" onClick={() => setSchema((now) => now + `{${t(`procure.formats.naming.var.${variable}`)}}`)} className="rounded-full border border-line bg-sheet-2/60 px-2.5 py-0.5 font-mono text-xs font-semibold text-ink-2 hover:bg-sheet-2">
            {`{${t(`procure.formats.naming.var.${variable}`)}}`}
          </button>
        ))}
      </div>
      <label className="block text-sm">
        <span className="font-semibold">{t('procure.formats.naming.sample')}</span>
        <select value={sample} onChange={(event) => setSample(Number(event.target.value))} className="mt-1 block h-10 w-full rounded-xl border border-line bg-sheet px-3 text-ink">
          {out.samples.map((item, index) => (
            <option key={item.title} value={index}>
              {item.title}, {item.author}
            </option>
          ))}
        </select>
      </label>
      <div className="space-y-1 text-sm">
        <small className="block text-muted">{t('procure.formats.naming.ebook')}</small>
        <Code testid="preview-ebook">{shown?.ebook ?? '…'}</Code>
        <small className="block text-muted">{t('procure.formats.naming.audio')}</small>
        <Code testid="preview-audio">{shown?.audio ?? '…'}</Code>
      </div>
    </Card>
  )
}
