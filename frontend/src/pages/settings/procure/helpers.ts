/** What the fetching settings need besides components: the names of languages, the sentence of an error, the time of day. */
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../../api/client'
import { errorText } from '../../../lib/errors'

/** The languages a category or a format can be given: their codes, named in the language of the page. */
export function useLanguageNames() {
  const { i18n, t } = useTranslation()
  const names = (() => {
    try {
      return new Intl.DisplayNames([i18n.language], { type: 'language' })
    } catch {
      return null
    }
  })()
  return (code: string): string => {
    if (code === 'mixed') return t('procure.lang.mixed')
    if (code === 'original') return t('procure.lang.original')
    const named = names?.of(code)
    return named ? named.charAt(0).toLocaleUpperCase(i18n.language) + named.slice(1) : code
  }
}

/** The sentence for an error, with the values the server sent along. */
export function problemText(error: unknown): string {
  return error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error')
}

/**
 * Whether the key saved for the address `saved` may stay in use for the address `typed`: the same host and port (the
 * scheme the same, or raised from http to https with the same port number or from 80 to 443). The server holds to the same
 * rule (`gate.key_follows`): a saved key is never sent to another place, so an edited address that points elsewhere needs
 * the key typed again. An address that cannot be read is left to the server to refuse.
 */
export function keyFollows(saved: string, typed: string): boolean {
  let before: URL
  let after: URL
  try {
    before = new URL(saved)
    after = new URL(typed)
  } catch {
    return true
  }
  const port = (place: URL) => Number(place.port || (place.protocol === 'https:' ? 443 : 80))
  if (before.hostname.toLowerCase() !== after.hostname.toLowerCase()) return false
  if (before.protocol === after.protocol) return port(before) === port(after)
  if (before.protocol !== 'http:' || after.protocol !== 'https:') return false
  return port(after) === port(before) || (port(before) === 80 && port(after) === 443)
}

/** The time of day of a moment, in the language of the page and the time zone of the browser ("01:00"). */
export function clock(moment: string, language: string): string {
  return new Date(moment).toLocaleTimeString(language, { hour: '2-digit', minute: '2-digit' })
}
