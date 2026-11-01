/**
 * Settings, Sources (the operator's): which sources on the internet nexbooks may ask about the books it found. A main
 * switch ("nexbooks may ask sources on the internet"), a card for each source (on or off, how it fares, how many requests
 * today), Hardcover's token (pasted in, sealed, never shown again), the markets of Audible's catalogue, and "match
 * everything again". One sentence says what goes out: a title, an author and an ISBN, never a file.
 */
import { BookMarked, Globe, Headphones, Landmark, Library, RefreshCw, ShieldCheck, Trophy, UserRound } from 'lucide-react'
import { Fragment, useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { sourcesApi, type SourceId, type SourceInfo, type SourcesOut, type SourceState } from '../../api/client'
import { StartGate } from '../../components/procure/StartDialog'
import { useConfirm } from '../../components/useConfirm'
import { ago } from '../../lib/ago'
import { timeOfMoment } from '../../lib/dates'
import { useAuth } from '../../state/auth'
import { Button, Card, Feedback, Input, Toggle, useAction } from './ui'

const ICON = { hardcover: BookMarked, openlibrary: Library, audible: Headphones, dnb: Landmark, apple: Trophy, wikidata: UserRound, bnf: Landmark, libris: Landmark, kb: Landmark, nb: Landmark } as const
// The national libraries of other languages come after the German one: they serve the catalogue of an author only.
const ORDER: SourceId[] = ['openlibrary', 'audible', 'hardcover', 'dnb', 'bnf', 'libris', 'kb', 'nb', 'apple', 'wikidata']
/** The most markets that can be asked (the server holds the same limit). */
const MAX_MARKETS = 6
/** While books wait for the sources the card looks again this often (milliseconds). */
const POLL_MS = 4000

const STATE_TONE: Record<SourceState, string> = {
  ready: 'text-ok',
  resting: 'text-warn',
  token_invalid: 'text-bad',
  not_connected: 'text-muted',
  off: 'text-muted',
  main_off: 'text-muted',
}

export function SourcesPart() {
  const { t } = useTranslation()
  const [state, setState] = useState<SourcesOut | null>(null)
  const load = useCallback(() => sourcesApi.state().then(setState, () => setState((now) => now)), [])
  useEffect(() => {
    void load()
  }, [load])
  const waiting = state?.waiting ?? 0
  useEffect(() => {
    if (waiting === 0) return
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [waiting, load])
  if (!state) return <p className="text-sm text-muted">{t('sources.loading')}</p>
  const byId = new Map(state.sources.map((source) => [source.id, source]))
  return (
    <div className="space-y-6">
      <StartGate onAnswered={setState} />
      <MainCard state={state} onState={setState} />
      {ORDER.map((id) => {
        const source = byId.get(id)
        return source ? (
          <Fragment key={id}>
            <SourceCard source={source} state={state} onState={setState} />
            {/* The markets are Audible's: they stand right below its card. */}
            {id === 'audible' && <MarketsCard state={state} onState={setState} />}
            {/* What the photos of authors are for stands right below the source that gives most of them. */}
            {id === 'wikidata' && <AuthorInfoCard state={state} onState={setState} />}
          </Fragment>
        ) : null
      })}
    </div>
  )
}

function MainCard({ state, onState }: { state: SourcesOut; onState: (next: SourcesOut) => void }) {
  const { t } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const rematch = () =>
    ask({
      title: t('sources.rematch.title'),
      text: t('sources.rematch.text'),
      confirm: t('sources.rematch.confirm'),
      danger: false,
      run: async () => {
        await run(async () => {
          const queued = await sourcesApi.rematch()
          onState(await sourcesApi.state())
          return queued
        }, t('sources.rematch.done'))
      },
    })
  return (
    <Card id="sources-main" icon={Globe} title={t('sources.main.title')} text={t('sources.main.text')}>
      <Toggle
        label={t('sources.main.switch')}
        hint={t('sources.main.hint')}
        checked={state.enabled}
        disabled={busy}
        onChange={(enabled) => void run(async () => onState(await sourcesApi.save({ enabled })), t('settings.saved'))}
      />
      <p className="flex items-start gap-2 rounded-xl border border-line bg-sheet-2/50 px-3 py-2 text-sm text-ink-2" data-testid="sources-privacy">
        <ShieldCheck size={16} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        {t('sources.main.sends')}
      </p>
      {(state.waiting > 0 || state.to_look_at > 0) && (
        <p className="text-sm text-muted" data-testid="sources-numbers">
          {[state.waiting > 0 ? t('sources.main.waiting', { count: state.waiting }) : '', state.to_look_at > 0 ? t('sources.main.lookAt', { count: state.to_look_at }) : '']
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <Button onClick={rematch} busy={busy}>
          <RefreshCw size={15} aria-hidden /> {t('sources.rematch.button')}
        </Button>
      </div>
      <Feedback problem={problem} done={done} values={values} />
      {dialog}
    </Card>
  )
}

function AuthorInfoCard({ state, onState }: { state: SourcesOut; onState: (next: SourcesOut) => void }) {
  const { t } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  return (
    <Card id="sources-authors" icon={UserRound} title={t('sources.authorInfo.title')} text={t('sources.authorInfo.text')}>
      <Toggle
        label={t('sources.authorInfo.switch')}
        hint={t('sources.authorInfo.hint')}
        checked={state.author_info}
        disabled={busy}
        onChange={(author_info) => void run(async () => onState(await sourcesApi.save({ author_info })), t('settings.saved'))}
      />
      <Feedback problem={problem} done={done} values={values} />
    </Card>
  )
}

function SourceCard({ source, state, onState }: { source: SourceInfo; state: SourcesOut; onState: (next: SourcesOut) => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { busy, problem, values, done, run } = useAction()
  const Icon = ICON[source.id]
  const moment = source.last_error_at ? ago(source.last_error_at, i18n.language, t('libraries.justNow')) : ''
  return (
    <Card id={`source-${source.id}`} icon={Icon} title={t(`sources.names.${source.id}`)} text={t(`sources.texts.${source.id}`)}>
      <Toggle
        label={t('sources.use', { name: t(`sources.names.${source.id}`) })}
        checked={source.enabled}
        disabled={busy}
        onChange={(enabled) => void run(async () => onState(await sourcesApi.save({ sources: { [source.id]: enabled } })), t('settings.saved'))}
      />
      <p className="text-sm" data-testid={`source-state-${source.id}`}>
        <span className={`font-semibold ${STATE_TONE[source.state]}`}>{t(source.state === 'resting' && source.last_error === 'daily_limit' ? 'sources.state.daily_limit' : `sources.state.${source.state}`)}</span>
        <span className="text-muted"> · {t('sources.today', { count: source.requests_today, limit: source.requests_per_day })}</span>
        {source.state === 'resting' && source.resting_until && (
          <span className="text-muted" data-testid={`source-until-${source.id}`}>
            {' '}
            · {t('sources.restsUntil', { time: timeOfMoment(source.resting_until, i18n.language, me?.profile?.timezone) })}
          </span>
        )}
        {(source.waiting ?? 0) > 0 && (
          <span className="text-muted" data-testid={`source-waiting-${source.id}`}>
            {' '}
            · {t('sources.waitingFor', { count: source.waiting })}
          </span>
        )}
      </p>
      {source.last_error && (
        <p className="text-sm text-muted" data-testid={`source-error-${source.id}`}>
          {t('sources.lastError', { error: t(`sources.errors.${source.last_error}`, { defaultValue: source.last_error }), when: moment })}
        </p>
      )}
      {source.id === 'hardcover' && <HardcoverToken source={source} state={state} onState={onState} />}
      <Feedback problem={problem} done={done} values={values} />
    </Card>
  )
}

/** The token of Hardcover: pasted in once, sealed by the server, never shown again. */
function HardcoverToken({ source, state, onState }: { source: SourceInfo; state: SourcesOut; onState: (next: SourcesOut) => void }) {
  const { t } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const { ask, dialog } = useConfirm()
  const [token, setToken] = useState('')
  const connected = state.hardcover_connected
  const connect = async () => {
    const ok = await run(async () => onState(await sourcesApi.connect(token.trim())), t('sources.hardcover.connected'))
    if (ok) setToken('')
  }
  const disconnect = () =>
    ask({
      title: t('sources.hardcover.disconnectTitle'),
      text: t('sources.hardcover.disconnectText'),
      confirm: t('sources.hardcover.disconnect'),
      run: async () => {
        await run(async () => onState(await sourcesApi.disconnect()), t('sources.hardcover.disconnected'))
      },
    })
  return (
    <div className="space-y-3 border-t border-line pt-3">
      {source.state === 'token_invalid' && (
        <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="hardcover-token-invalid">
          {t('sources.hardcover.invalid')}
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (token.trim()) void connect()
        }}
        className="space-y-3"
      >
        <Input
          label={connected ? t('sources.hardcover.newToken') : t('sources.hardcover.token')}
          type="password"
          value={token}
          onChange={setToken}
          maxLength={4000}
          autoComplete="off"
          hint={connected ? t('sources.hardcover.sealed') : t('sources.hardcover.hint')}
        />
        <div className="flex flex-wrap gap-2">
          <Button primary type="submit" busy={busy} disabled={!token.trim()}>
            {connected ? t('sources.hardcover.replace') : t('sources.hardcover.connect')}
          </Button>
          {connected && (
            <Button danger onClick={disconnect} busy={busy}>
              {t('sources.hardcover.disconnect')}
            </Button>
          )}
        </div>
      </form>
      <p className="text-xs text-muted">{t('sources.hardcover.beta')}</p>
      <Feedback problem={problem} done={done} values={values} />
      {dialog}
    </div>
  )
}

/** The markets of Audible's catalogue that are asked (the market of a book's language first). */
function MarketsCard({ state, onState }: { state: SourcesOut; onState: (next: SourcesOut) => void }) {
  const { t } = useTranslation()
  const { busy, problem, values, done, run } = useAction()
  const chosen = new Set(state.markets)
  const toggle = (market: string) => {
    const next = chosen.has(market) ? state.markets.filter((m) => m !== market) : [...state.markets, market]
    if (next.length === 0 || next.length > MAX_MARKETS) return
    void run(async () => onState(await sourcesApi.save({ markets: next })), t('settings.saved'))
  }
  return (
    <Card id="sources-markets" icon={Globe} title={t('sources.markets.title')} text={t('sources.markets.text')}>
      <div role="group" aria-label={t('sources.markets.title')} className="flex flex-wrap gap-2">
        {state.available_markets.map((market) => {
          const on = chosen.has(market)
          return (
            <button
              key={market}
              type="button"
              aria-pressed={on}
              disabled={busy || (!on && chosen.size >= MAX_MARKETS) || (on && chosen.size === 1)}
              onClick={() => toggle(market)}
              className={`rounded-full border px-3.5 py-1.5 text-sm font-semibold transition disabled:opacity-50 ${on ? 'border-accent/50 bg-accent-soft text-accent' : 'border-line bg-sheet text-ink-2 hover:text-ink'}`}
            >
              {t(`sources.markets.names.${market}`, { defaultValue: market })}
            </button>
          )
        })}
      </div>
      <p className="text-xs text-muted">{t('sources.markets.hint', { max: MAX_MARKETS })}</p>
      <Feedback problem={problem} done={done} values={values} />
    </Card>
  )
}
