import i18n from '../i18n'
import { oidcErrorKey } from '../vendor/nexoidc/oidc'

/** The sentence for a server's error code, in the language of the page; an unknown code gets the general one. */
export function errorText(code: string, values: Record<string, unknown> = {}): string {
  // The fixed codes of sign-in through a provider bring their sentences with the shared module (`oidc.error.*`), the
  // same in every nex app; they go before nexbooks's own.
  const signIn = oidcErrorKey(code)
  if (signIn) return i18n.t(signIn)
  // A refusal of an input names the fields it is about, as far as it knows their names ("The input is not valid: title.").
  if (code === 'invalid_input') {
    const named = [values.field, ...(Array.isArray(values.fields) ? values.fields : [])]
      .filter((name): name is string => typeof name === 'string' && i18n.exists(`errorFields.${name}`))
      .map((name) => i18n.t(`errorFields.${name}`))
    if (named.length > 0) return i18n.t('errors.invalid_input_in', { fields: [...new Set(named)].join(', ') })
  }
  const key = `errors.${code}`
  return i18n.exists(key) ? i18n.t(key, values) : i18n.t('errors.internal_error')
}
