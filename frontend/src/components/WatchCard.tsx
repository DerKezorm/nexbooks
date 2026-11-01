/**
 * "{name} beobachten" on the page of an author: one switch for the
 * person, and when it is on the choices that belong to it (kind, language, "also non-fiction ..."), and what is announced
 * and what became of it. Switching it on wishes the books announced at once (or puts them on the wish list for who may not
 * ask for books) and never what is out already; switching it off asks first whether the wishes it made stay.
 *
 * The language is one of the languages books are fetched in (the same list as "Beschaffen"), the first of them unless the
 * person picks another: only that one is wished. A book announced in another of them only waits on the wish list.
 *
 * Everything is the person's own: the server keeps it, the page only shows the answer it got.
 */
import { Eye } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, watchApi, type AuthorCatalog, type AuthorWatch, type WatchedBook, type WatchKind } from '../api/client'
import { languageName } from '../lib/books'
import { errorText } from '../lib/errors'
import { useAuth } from '../state/auth'
import { useProcure } from '../state/procure'
import { useWish } from '../state/wish'
import { Dialog } from './Dialog'

type Said = { text: string; see: boolean }

/** The sentence about what a look made: "Du beobachtest X. 2 Wünsche ..." when the watch is new, only the news when not. */
function madeText(t: (key: string, values?: Record<string, unknown>) => string, name: string, next: AuthorWatch, fresh: boolean): Said | null {
  const made = next.made
  if (!made) return null
  const parts: string[] = []
  if (fresh) {
    if (made.wished > 0) parts.push(t('author.watch.on_wished', { name, count: made.wished }))
    else if (made.listed > 0) parts.push(t('author.watch.on_listed', { name, count: made.listed }))
    else parts.push(t('author.watch.on_none', { name }))
  } else {
    if (made.wished > 0) parts.push(t('author.watch.more_wished', { count: made.wished }))
    if (made.listed > 0) parts.push(t('author.watch.more_listed', { count: made.listed }))
  }
  if (made.stopped) parts.push(t(`author.watch.stopped.${made.stopped}`, { defaultValue: '' }))
  const text = parts.filter(Boolean).join(' ')
  return text ? { text, see: made.wished + made.listed > 0 } : null
}

/** Asks whether the wishes the watch made stay, when it is switched off (the default: they stay). */
function StopDialog({ name, busy, problem, onCancel, onStop }: { name: string; busy: boolean; problem: string | null; onCancel: () => void; onStop: (remove: boolean) => void }) {
  const { t } = useTranslation()
  const [remove, setRemove] = useState(false)
  return (
    <Dialog title={t('author.watch.offTitle', { name })} onClose={onCancel} above>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          onStop(remove)
        }}
      >
        <p className="text-sm text-ink-2">{t('author.watch.offText', { name })}</p>
        <fieldset className="watch-choice" aria-label={t('author.watch.offChoice')}>
          <label className="chk top">
            <input type="radio" name="watch-wishes" checked={!remove} onChange={() => setRemove(false)} data-autofocus="" data-testid="watch-keep" />
            <span>
              <b>{t('author.watch.offKeep')}</b>
              <small>{t('author.watch.offKeepText')}</small>
            </span>
          </label>
          <label className="chk top">
            <input type="radio" name="watch-wishes" checked={remove} onChange={() => setRemove(true)} data-testid="watch-remove" />
            <span>
              <b>{t('author.watch.offRemove')}</b>
              <small>{t('author.watch.offRemoveText')}</small>
            </span>
          </label>
        </fieldset>
        {problem && (
          <p role="alert" className="text-sm text-bad">
            {problem}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="inline-flex h-10 items-center justify-center rounded-full border border-line px-4 text-sm font-semibold text-ink-2 transition hover:bg-sheet-2">
            {t('common.cancel')}
          </button>
          <button
            type="submit"
            disabled={busy}
            data-testid="watch-stop"
            className={`inline-flex h-10 items-center justify-center rounded-full px-4 text-sm font-semibold transition disabled:opacity-50 ${remove ? 'bg-bad text-bad-ink hover:brightness-110' : 'bg-accent text-accent-ink hover:brightness-105'}`}
          >
            {busy ? '…' : t('author.watch.offConfirm')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function WatchCard({ catalog, onChange, died }: { catalog: AuthorCatalog; onChange: (next: AuthorCatalog) => void; died: number | string | null }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const procure = useProcure()
  const wish = useWish()
  const watch = catalog.watch ?? null
  const can = procure.allowed
  const name = catalog.name
  const [said, setSaid] = useState<Said | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  const [stopProblem, setStopProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // One press, one request: a second press before the first has been answered is the same press.
  const sent = useRef(false)

  const apply = (next: AuthorWatch | null) => {
    onChange({ ...catalog, watch: next })
    void procure.reload()
    void wish.reload()
  }

  const put = async (body: { kind: WatchKind; language?: string; more: boolean }, fresh: boolean) => {
    if (sent.current) return
    sent.current = true
    setBusy(true)
    setProblem(null)
    try {
      const next = await watchApi.set(catalog.token, body)
      setSaid(madeText(t, name, next, fresh))
      apply(next)
    } catch (error) {
      setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}))
    } finally {
      sent.current = false
      setBusy(false)
    }
  }

  const stop = async (remove: boolean) => {
    if (sent.current) return
    sent.current = true
    setBusy(true)
    setStopProblem(null)
    try {
      await watchApi.stop(catalog.token, remove ? 'remove' : 'keep')
      setStopping(false)
      setSaid({ text: t(remove ? 'author.watch.offDoneRemove' : 'author.watch.offDoneKeep', { name }), see: false })
      apply(null)
    } catch (error) {
      setStopProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}))
    } finally {
      sent.current = false
      setBusy(false)
    }
  }

  const original = catalog.orig_language
  const named = (code: string) => languageName(code, i18n.language)
  const titles = (items: { title: string }[]) => items.map((item) => t('author.watch.quoted', { text: item.title })).join(', ')
  const why = (item: WatchedBook) => {
    const [code, other] = (item.why ?? '').split(':')
    return code ? t(`discover.wish.why.${code}`, { language: other ? named(other) : '', defaultValue: '' }) : ''
  }
  const reasoned = (items: WatchedBook[]) => items.map((item) => (why(item) ? t('author.watch.quotedWhy', { text: item.title, why: why(item) }) : t('author.watch.quoted', { text: item.title }))).join(', ')
  let summary: ReactNode = null
  if (watch) {
    if (watch.state === 'waiting') summary = <p className="w-sum">{t('author.watch.waiting', { name })}</p>
    else {
      const of = (state: WatchedBook['state']) => watch.announced.filter((item) => item.state === state)
      const wished = of('wished')
      // Without the right to acquire everything goes onto the wish list: said as the design says it, without reasons.
      const listed = of('listed')
      const done = watch.can_acquire ? wished : [...wished, ...listed]
      const open = of('open')
      const held = of('held')
      const years = of('year')
      // Wished already by somebody else for the library the book would go into: the person's watch wishes it when that wish goes.
      const covered = of('covered')
      const quiet = done.length === 0 && open.length === 0 && years.length === 0 && (!watch.can_acquire || listed.length === 0) && held.length === 0 && covered.length === 0
      // As the design has it: what is announced (or that nothing is), then each book announced only in another language.
      const parts = [
        ...(watch.paused ? [t('author.watch.paused')] : []),
        ...(watch.language_off ? [t('author.watch.languageOff', { language: named(watch.language), target: named(watch.target) })] : []),
        ...(watch.target ? [] : [t('author.watch.noLanguage')]),
        ...(done.length > 0 ? [t(watch.can_acquire ? 'author.watch.announcedCan' : 'author.watch.announcedList', { titles: titles(done) })] : quiet ? [t('author.watch.nothing')] : []),
        ...(watch.can_acquire && listed.length > 0 ? [t('author.watch.listedCan', { titles: reasoned(listed) })] : []),
        ...(covered.length > 0 ? [t('author.watch.covered', { titles: titles(covered) })] : []),
        ...(open.length > 0 ? [t('author.watch.announcedOpen', { titles: titles(open) })] : []),
        ...years.map((item) => t(watch.can_acquire ? 'author.watch.yearCan' : 'author.watch.yearList', { title: item.title, year: item.date })),
        ...(held.length > 0 ? [t('author.watch.held', { titles: titles(held) })] : []),
        ...watch.not_yet.map((item) => t(watch.can_acquire ? 'author.watch.notYetCan' : 'author.watch.notYetList', { title: item.title, language: named(item.language), other: named(item.announced_in) })),
      ]
      summary = (
        <p className="w-sum" data-testid="watch-summary">
          {parts.join(' ')}
          {watch.sources_few && (
            <>
              {' '}
              <span data-testid="watch-few-sources">
                {t('author.watch.fewSources')}
                {me?.role === 'operator' && (
                  <>
                    {' '}
                    <Link to="/settings?tab=sources" className="lnk">
                      {t('author.watch.fewSourcesLink')}
                    </Link>
                  </>
                )}
              </span>
            </>
          )}
        </p>
      )
    }
  }
  // The languages of "Beschaffen" for this author, the first one first: empty (the first, whatever it is) or one of them.
  const codes = watch ? watch.languages : []
  // (A language no longer fetched in is not sent back: the server would refuse it.)
  const chosenLanguage = watch && !watch.language_off ? watch.language : ''
  const language = (code: string) => (code === original ? t('author.watch.languageOriginal', { language: named(code) }) : named(code))
  const chosen = watch?.kind ?? 'both'
  return (
    <section className={`watch ${watch ? 'on' : ''}`} data-testid="watch-card">
      <div className="w-head">
        <span className="w-ic">
          <Eye aria-hidden size={20} />
        </span>
        <div>
          <h3>{t('author.watch.title', { name })}</h3>
          <p>
            {can ? t('author.watch.textCan', { name }) : t('author.watch.textList', { name })}
            {died ? ` ${t('author.watch.died', { name, year: died })}` : ''}
          </p>
        </div>
        <label className="toggle big">
          <input
            type="checkbox"
            checked={watch !== null}
            disabled={busy}
            aria-label={t('author.watch.switch', { name })}
            data-testid="watch-switch"
            onChange={(event) => {
              if (event.target.checked) void put({ kind: 'both', more: false }, true)
              else {
                setStopProblem(null)
                setStopping(true)
              }
            }}
          />
          <span />
          <b>{watch ? t('author.watch.on') : t('author.watch.off')}</b>
        </label>
      </div>
      {watch && (
        <div className="w-opts">
          {can && (
            <div className="fld">
              <span className="lab">{t('author.watch.kind')}</span>
              <div className="seg small" role="radiogroup" aria-label={t('author.watch.kind')}>
                {(['ebook', 'audio', 'both'] as const).map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    role="radio"
                    aria-checked={chosen === kind}
                    className={chosen === kind ? 'on' : ''}
                    disabled={busy}
                    data-wkind={kind}
                    onClick={() => chosen !== kind && void put({ kind, language: chosenLanguage, more: watch.more }, false)}
                  >
                    {t(kind === 'ebook' ? 'author.watch.kindEbook' : kind === 'audio' ? 'author.watch.kindAudio' : 'author.watch.kindBoth')}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="fld">
            <label className="lab" htmlFor="w-lang">
              {t('author.watch.language')}
            </label>
            <select id="w-lang" className="sel mini" value={chosenLanguage} disabled={busy} data-testid="watch-language" onChange={(event) => void put({ kind: watch.kind, language: event.target.value, more: watch.more }, false)}>
              {codes.length > 0 && <option value="">{t('author.watch.languageFirst', { language: named(codes[0]) })}</option>}
              {codes
                .filter((code, index) => index > 0 || code === chosenLanguage)
                .map((code) => (
                  <option key={code} value={code}>
                    {language(code)}
                  </option>
                ))}
            </select>
          </div>
          <label className="chk">
            <input type="checkbox" checked={watch.more} disabled={busy} data-testid="watch-more" onChange={(event) => void put({ kind: watch.kind, language: chosenLanguage, more: event.target.checked }, false)} />
            {t('author.watch.more')}
          </label>
          {summary}
        </div>
      )}
      {said && (
        <p className="w-said" role="status" data-testid="watch-said">
          {said.text}
          {said.see && (
            <>
              {' '}
              <Link to={can ? '/acquire' : '/discover'} className="lnk">
                {t('author.watch.see')}
              </Link>
            </>
          )}
        </p>
      )}
      {problem && (
        <p className="w-said" role="alert">
          {problem}
        </p>
      )}
      {stopping && watch && <StopDialog name={name} busy={busy} problem={stopProblem} onCancel={() => setStopping(false)} onStop={(remove) => void stop(remove)} />}
    </section>
  )
}
