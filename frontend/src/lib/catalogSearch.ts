/**
 * Asking the catalogues of the sources (`/api/v1/search/catalog`): the answer comes after a moment with what the sources gave
 * by then (`pending`), and is asked for again by its ticket until every source has answered. The quick search of the head
 * and the search of Discover ask the same way and show the same hits in the same order.
 *
 * What a search costs the sources is kept to what is shown: a page that gives up on a search that is still pending (the
 * person typed on, or left) says so, and the server asks nothing more for it; a hit's page is made when the person opens or
 * acquires it (`tokenOfHit`), its cover is fetched when the browser shows it. A page that is reloaded or closed gives up on
 * its search as well.
 */
import { useTranslation } from 'react-i18next'

import { ApiError, worksApi, type CatalogHit, type CatalogOut, type ReleaseKind } from '../api/client'
import { useProcure } from '../state/procure'

/** How often an answer that is still pending is asked for again (the server waits up to two seconds each time). */
export const POLL_TIMES = 12
/** The hits the quick search shows; the rest are on the page of the search in Discover. */
export const QUICK_HITS = 6

/**
 * Asks the catalogues for the words and hands each answer on. When the signal ends the search while the sources are still
 * asked, or the answer is still pending after `POLL_TIMES`, the server is told to stop (`catalogStop`); an answer that comes
 * after the signal ended is never handed on.
 */
export async function askCatalog(words: string, signal: AbortSignal, onAnswer: (answer: CatalogOut) => void): Promise<void> {
  let pending: string | null = null
  const stop = () => {
    if (pending) worksApi.catalogStop(pending).catch(() => undefined)
    pending = null
  }
  signal.addEventListener('abort', stop, { once: true })
  // (A page that is reloaded or closed gives up on the search too.)
  window.addEventListener('pagehide', stop)
  try {
    let answer = await worksApi.catalog(words, signal)
    if (signal.aborted) return
    pending = answer.pending && answer.ticket ? answer.ticket : null
    onAnswer(answer)
    for (let time = 0; answer.pending && answer.ticket && time < POLL_TIMES; time += 1) {
      answer = await worksApi.catalogAgain(answer.ticket, signal)
      if (signal.aborted) return
      pending = answer.pending && answer.ticket ? answer.ticket : null
      onAnswer(answer)
    }
    stop()
  } finally {
    signal.removeEventListener('abort', stop)
    window.removeEventListener('pagehide', stop)
  }
}

/**
 * What to say about the sources that gave nothing for a search (`resting`): that they are not answering and the hits are
 * incomplete (or that there are none yet for that reason), and when they may be asked again. Empty when every source
 * answered.
 */
export function useRestingLine(): (answer: CatalogOut | null | undefined) => string {
  const { t, i18n } = useTranslation()
  return (answer) => {
    const resting = Array.isArray(answer?.resting) ? answer.resting : []
    if (!answer || !resting.length) return ''
    const names = new Intl.ListFormat(i18n.language || 'de', { type: 'conjunction' }).format(resting.map((item) => t(`sources.names.${item.source}`)))
    const count = resting.length
    const line = answer.items.length ? t('search.resting', { count, names }) : t('search.restingNone', { count, names })
    const until = resting.map((item) => (item.until ? Date.parse(item.until) : NaN)).filter((value) => Number.isFinite(value) && value > Date.now())
    if (!until.length) return line
    const time = new Intl.DateTimeFormat(i18n.language || 'de', { hour: '2-digit', minute: '2-digit' }).format(new Date(Math.max(...until)))
    return `${line} ${t('search.restingUntil', { time })}`
  }
}

/** Whether a failed search failed because the person searched too much at once (the brake of the server). */
export function tooOften(error: unknown): boolean {
  return error instanceof ApiError && error.status === 429
}

/** The token of the page of a hit: the one it has, or the page made now (`ticket`: the search the hit belongs to). */
export async function tokenOfHit(hit: CatalogHit, ticket: string | null | undefined): Promise<string> {
  if (hit.token) return hit.token
  if (!ticket) throw new ApiError(404, 'search_gone')
  return (await worksApi.catalogBook(ticket, hit.key)).token
}

/** The address of the search in Discover for these words. */
export function searchHref(words: string): string {
  return `/discover/search?q=${encodeURIComponent(words.trim())}`
}

/** The book to acquire of a hit, as the dialog takes it (`token`: the page of the hit, `tokenOfHit`). */
export function bookOfHit(hit: CatalogHit, token: string) {
  return { token, title: hit.title, authors: hit.authors, series: hit.series, series_pos: hit.series_pos, color: '#8e3b46', cover_url: hit.cover_url }
}

/** The authors, the year it first came out, and the volume of its series. */
export function useHitLine(): (hit: CatalogHit) => string {
  const { t } = useTranslation()
  return (hit) =>
    [
      hit.authors.join(', '),
      hit.year ? String(hit.year) : '',
      hit.series && hit.series_pos !== null ? t('search.hit.volumeOf', { pos: hit.series_pos, series: hit.series }) : hit.series,
    ]
      .filter(Boolean)
      .join(' · ')
}

export const KINDS: { key: 'ebook' | 'audio' | 'both'; kinds: ReleaseKind[] }[] = [
  { key: 'ebook', kinds: ['ebook'] },
  { key: 'audio', kinds: ['audio'] },
  { key: 'both', kinds: ['ebook', 'audio'] },
]

/** Opens the dialog to acquire a hit with those kinds: its page is made first when it has none (`ticket`: its search). */
export function useAcquireHit(): (hit: CatalogHit, ticket: string | null | undefined, kinds: ReleaseKind[]) => Promise<void> {
  const procure = useProcure()
  return async (hit, ticket, kinds) => {
    const token = await tokenOfHit(hit, ticket)
    procure.open([bookOfHit(hit, token)], { kinds })
  }
}

/** Whether "E-Book", "Hörbuch" and "Beides" stand at a hit for this person (see `AcquireKinds`). */
export function offersKinds(hit: CatalogHit, allowed: boolean, asked: boolean): boolean {
  return allowed && hit.owned_work_id === null && hit.procurable && !asked
}
