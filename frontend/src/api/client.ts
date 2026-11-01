/**
 * The server's API, `/api/v1`. Every call names this browser tab in `X-Nexbooks-Client` (changes without it are refused, the
 * wall against requests from other sites). Errors come back as `ApiError` with the server's code; the page builds
 * its sentence from `errors.<code>` in the language files.
 */

import i18n from 'i18next'

import type { AuthentikAddress } from '../vendor/nexoidc/oidc'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly values: Record<string, unknown> = {},
  ) {
    super(code)
  }
}

const CLIENT_KEY = 'nexbooks.client'
/** Sent on `window` when a request finds that the session is gone: the page goes back to the sign-in. */
export const SIGNED_OUT_EVENT = 'nexbooks:signed-out'

function randomId(): string {
  const bytes = new Uint8Array(12)
  crypto.getRandomValues(bytes)
  return 'tab-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

let clientId: string | null = null

/** One id per tab: sessionStorage survives a reload of the tab but is not shared with other tabs. */
export function tabId(): string {
  if (clientId) return clientId
  try {
    clientId = sessionStorage.getItem(CLIENT_KEY)
    if (!clientId) {
      clientId = randomId()
      sessionStorage.setItem(CLIENT_KEY, clientId)
    }
  } catch {
    clientId = randomId()
  }
  return clientId
}

type Options = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: Record<string, string | number | boolean | string[] | undefined>
  body?: unknown
  /** A file as the body (an upload), sent as it is. */
  raw?: Blob
  /** Files in a form (the browser's upload); the browser makes the boundary. */
  form?: FormData
  /** Stops the request (a cancelled upload). */
  signal?: AbortSignal
  /** The answer is a file, not JSON. */
  blob?: boolean
  /** Headers of its own, next to the ones every request carries (the password of an upload). */
  headers?: Record<string, string>
  /** Sent even while the page closes (the last draft). */
  keepalive?: boolean
}

const BUSY_TRIES = 3
const BUSY_WAIT_MS = 1500

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const repeatable = (options.method ?? 'GET') === 'GET'
  for (let attempt = 1; ; attempt++) {
    try {
      return await once<T>(path, options)
    } catch (error) {
      if (!(error instanceof ApiError && error.code === 'busy' && repeatable && attempt < BUSY_TRIES)) throw error
      await new Promise((resolve) => setTimeout(resolve, BUSY_WAIT_MS))
    }
  }
}

async function once<T>(path: string, options: Options): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (Array.isArray(value)) for (const item of value) url.searchParams.append(key, item)
    else if (value !== undefined) url.searchParams.set(key, String(value))
  }
  // The language the page is shown in: what an account without a language of its own starts with (its values).
  const headers: Record<string, string> = { ...options.headers, Accept: 'application/json', 'X-Nexbooks-Client': tabId(), ...(i18n.language ? { 'X-Nexbooks-Language': i18n.language } : {}) }
  let body: BodyInit | undefined
  if (options.raw) body = options.raw
  else if (options.form) body = options.form
  else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  let response: Response
  try {
    response = await fetch(url.pathname + url.search, { method: options.method ?? 'GET', headers, body, ...(options.keepalive ? { keepalive: true } : {}), ...(options.signal ? { signal: options.signal } : {}) })
  } catch (error) {
    // A request the page stopped on purpose is no network trouble.
    if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw new ApiError(0, 'cancelled')
    throw new ApiError(0, 'network')
  }
  if (response.status === 204) return undefined as T
  if (options.blob && response.ok) return (await response.blob()) as T
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
      const { code, message: _message, ...values } = detail
      if (code === 'sign_in_required') window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
      // How long the server asks to wait (a brake, a busy moment), in seconds, for the page to try again then.
      const wait = Number(response.headers?.get?.('Retry-After') ?? NaN)
      if (Number.isFinite(wait) && wait >= 0 && values.retry_after === undefined) values.retry_after = wait
      throw new ApiError(response.status, code, values)
    }
    throw new ApiError(response.status, response.status === 401 ? 'sign_in_required' : 'internal_error')
  }
  return data as T
}

// ---- Types ----------------------------------------------------------------------------------------------------------

export type ThemeChoice = 'system' | 'light' | 'dark'
/** How the library shows the books: the shelf of spines or the grid of covers. */
export type LibraryView = 'shelf' | 'grid' | 'list'

export type Profile = {
  theme: ThemeChoice
  view: LibraryView
  /** The market Discover shows, two lower-case letters ("de", "us"); empty: none chosen yet. */
  market: string
  /** The language Discover shows, two lower-case letters; empty: any. */
  language: string
  /** The time zone; what a notice of a new sign-in names its time in. */
  timezone: string
  /** Reported by a browser, or chosen by the person (then no browser changes it). */
  timezone_source: 'browser' | 'manual'
  /** A push and a mail when the account signs in from a new device. */
  notify_login: boolean
  /** How the reader shows a book: typeface, size in percent, line height in hundredths, margins, colours,
   * justified text, pages or a continuous text. */
  reader_font?: 'fraunces' | 'inter' | 'publisher'
  reader_size?: number
  reader_line?: number
  reader_margin?: 'narrow' | 'normal' | 'wide'
  reader_theme?: 'auto' | 'light' | 'sepia' | 'dark'
  reader_justify?: boolean
  reader_flow?: 'paged' | 'scroll'
  /** How the player plays a recording: the speed in hundredths (50 to 300), the seconds of the jumps back and
   * ahead, and whether it goes back ten seconds after a longer pause. */
  player_speed?: number
  player_back?: number
  player_ahead?: number
  player_rewind?: boolean
  /** Discover: whether the lists leave out the books the person has on their shelves (on from the start). */
  discover_hide_owned?: boolean
  /** The library: books or authors, the column and the direction of the list, how the authors are put in order. */
  library_section?: 'books' | 'authors'
  list_sort?: ListSort
  list_dir?: 'asc' | 'desc'
  authors_sort?: 'name' | 'count' | 'recent'
}
/** The columns the list of the library can be put in order by (`shelf` is the order of the shelf: genre, author, series). */
export type ListSort = 'shelf' | 'title' | 'author' | 'series' | 'genre' | 'year' | 'size' | 'language' | 'added' | 'mine'

export type Me = {
  id: number
  name: string
  display_name: string
  role: 'operator' | 'member'
  sign_in: 'password' | 'oidc'
  email: string
  /** Confirmed by a link sent to it; an address the operator typed or a sign-in provider sent is not. */
  email_confirmed: boolean
  /** A new address waiting for its link; `email` stays until it is opened. */
  email_pending: string
  language: string
  /** Linked to at least one sign-in provider (which ones: `oidcApi.mine`). */
  oidc_linked: boolean
  /** A second factor of any kind: a code from an app or a passkey. */
  two_factor: boolean
  /** A code from an app. */
  totp: boolean
  /** How many passkeys. */
  passkeys: number
  two_factor_recovery_left: number
  avatar: string | null
  version: string
  whats_new_seen: string
  profile: Profile
  mail?: boolean
  /** Whether this session may only set up the second factor, or (until set up) reaches only its own account page. */
  second_factor_setup_required?: boolean
  /** `setup`: right after the password, only the setup; `codes`: the recovery codes to confirm; `full`. */
  session_stage?: SessionStage
  /** The operator asks this account for a second factor: the last one cannot be turned off. */
  second_factor_required?: boolean
  /**
   * This session came in through a provider that checks the second factor, and the account (with a password) has none
   * of its own: its device tokens and API keys wait until it sets one up. Making one stays possible.
   */
  keys_wait_for_second_factor?: boolean
}

export type SessionStage = 'full' | 'setup' | 'codes'
/** The first step went through and the account has a second factor: which kinds it may give. */
export type SecondFactorWaiting = { second_factor: true; totp: boolean; passkey: boolean }

export type SetupState = { needs_setup: boolean; code_required: boolean; signed_in: boolean; version: string; min_password: number }
/** A button on the sign-in and invitation page: one per active provider, in the order of the operator's list. */
export type ProviderButton = { slug: string; label: string }
export type Methods = { password: boolean; providers: ProviderButton[]; passkeys?: boolean; forgot?: boolean; session_days?: number }
export type SignedSession = { id: string; device: string; phone: boolean; network: string; created_at: string; last_seen_at: string; remember: boolean; here: boolean }
export type Passkey = { id: string; name: string; created_at: string; last_used_at: string | null; credential: string }
export type Readiness = { points: { key: string; state: 'ok' | 'warn' | 'bad'; values: Record<string, unknown> }[]; open: number }

// ---- Calls ----------------------------------------------------------------------------------------------------------

export const authApi = {
  setupState: () => api<SetupState>('/api/v1/setup'),
  setup: (name: string, password: string, code: string, language: string) =>
    api<Me>('/api/v1/setup', { method: 'POST', body: { name, password, code, language } }),
  methods: () => api<Methods>('/api/v1/auth/methods'),
  login: (name: string, password: string, remember = true) => api<Me | SecondFactorWaiting>('/api/v1/auth/login', { method: 'POST', body: { name, password, remember } }),
  code: (code: string, remember?: boolean) => api<Me>('/api/v1/auth/login/totp', { method: 'POST', body: { code, remember } }),
  /** The recovery codes are kept: the session that set up the second factor becomes a full one. */
  setupDone: () => api<Me>('/api/v1/auth/setup/done', { method: 'POST' }),
  /** New recovery codes while they are being confirmed (a reload lost the ones shown). */
  setupCodes: () => api<{ recovery_codes: string[] }>('/api/v1/auth/setup/codes', { method: 'POST' }),
  sessions: () => api<SignedSession[]>('/api/v1/auth/sessions'),
  endSession: (id: string) => api<void>(`/api/v1/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  logoutOthers: () => api<void>('/api/v1/auth/logout-all', { method: 'POST' }),
  cancelCode: () => api<void>('/api/v1/auth/login/totp/cancel', { method: 'POST' }),
  logout: () => api<void>('/api/v1/auth/logout', { method: 'POST' }),
  me: () => api<Me>('/api/v1/auth/me'),
  language: (language: string) => api<Me>('/api/v1/me/language', { method: 'PUT', body: { language } }),
  preferences: (change: Partial<Profile>) => api<Profile>('/api/v1/me/preferences', { method: 'PUT', body: change }),
  profile: (display_name: string) => api<Me>('/api/v1/me/profile', { method: 'PUT', body: { display_name } }),
  password: (current: string, next: string) => api<void>('/api/v1/auth/password', { method: 'PUT', body: { current, new: next } }),
  /** "Forgot your password?": a link to the address on record, if there is one; the answer is the same either way. */
  forgot: (name: string) => api<{ ok: true }>('/api/v1/auth/forgot', { method: 'POST', body: { name } }),
  whatsNewSeen: () => api<Me>('/api/v1/me/whats-new/seen', { method: 'POST' }),
}

// ---- Sign-in providers (the shared module `vendor/nexoidc`) -----------------------------------------------

/** An entry of the provider list as the operator sees it (never the secret). `managed`: "" by hand, "authentik" by the
 * authentik button. */
export type OidcProvider = {
  id: number
  slug: string
  label: string
  issuer: string
  client_id: string
  has_secret: boolean
  scopes: string
  enabled: boolean
  auto_create: boolean
  trusts_second_factor: boolean
  managed: '' | 'authentik' | 'nexsuite'
  position: number
  redirect_uri: string
  links: number
  editable: boolean
}
export type OidcProviderForm = {
  label: string
  slug?: string
  issuer: string
  client_id: string
  client_secret: string
  scopes: string
  enabled: boolean
  auto_create: boolean
  trusts_second_factor: boolean
}
export type ProviderImpact = { issuer_change: number; count: number; only: number; only_names: string[] }
export type AuthentikStep = { key: string; ok: boolean; detail: string; reason?: string; status?: number }
/** `count`: with the reason `issuer_change`, how many accounts would have to link again (nothing was changed). */
export type AuthentikResult = {
  ok: boolean
  steps: AuthentikStep[]
  links_dropped: number
  count?: number
  /** The public address the run stored (the card's suggestion, confirmed); it stays even when a later step failed. */
  public_url?: string
  /** With the reason `public_url_needed`: what the card shows about the address (the same as `oidcApi.address`). */
  address?: AuthentikAddress
}
/** What the server asks before a new public address leaves redirect addresses behind (409 `public_url_change`). */
export type AddressQuestion = { count: number; address: string; hand: number }
/** An active provider on the account page: linked or not, and the address it knows when it is not the account's. */
export type MyProvider = { slug: string; label: string; linked: boolean; address: string | null }
/** Which account is linked to which provider, for the marks in the operator's account list. */
export type AccountLink = { account_id: number; provider_id: number; slug: string; label: string }

export const oidcApi = {
  providers: () => api<OidcProvider[]>('/api/v1/oidc/admin/providers'),
  addProvider: (form: OidcProviderForm) => api<OidcProvider>('/api/v1/oidc/admin/providers', { method: 'POST', body: form }),
  saveProvider: (id: number, form: OidcProviderForm) => api<OidcProvider & { dropped: number }>(`/api/v1/oidc/admin/providers/${id}`, { method: 'PUT', body: form }),
  removeProvider: (id: number) => api<ProviderImpact>(`/api/v1/oidc/admin/providers/${id}`, { method: 'DELETE' }),
  providerImpact: (id: number, issuer = '') => api<ProviderImpact>(`/api/v1/oidc/admin/providers/${id}/impact`, { query: issuer ? { issuer } : {} }),
  orderProviders: (ids: number[]) => api<ProviderButton[]>('/api/v1/oidc/admin/providers/order', { method: 'PUT', body: { ids } }),
  links: () => api<AccountLink[]>('/api/v1/oidc/admin/links'),
  /** The operator takes an account's link away, with the own password. */
  unlinkAccount: (account: number, provider: number, current_password: string) =>
    api<void>(`/api/v1/oidc/admin/accounts/${account}/links/${provider}`, { method: 'DELETE', body: { current_password } }),
  /**
   * `confirm_issuer_change`: the answer to the question the button stops at when another issuer takes the links.
   * `public_url`: the suggested address ("Use it and set up"), only sent while nexbooks has none.
   */
  authentik: (url: string, token: string, confirm_issuer_change = false, public_url = '') =>
    api<AuthentikResult>('/api/v1/oidc/authentik/setup', { method: 'POST', body: { url, token, confirm_issuer_change, ...(public_url ? { public_url } : {}) } }),
  /** What the authentik card shows about the public address. */
  address: () => api<AuthentikAddress>('/api/v1/oidc/authentik/address'),
  /** "Use it only": the address becomes the public one; 409 `public_url_change` asks first, `confirm` answers it. */
  saveAddress: (public_url: string, confirm = false) => api<AuthentikAddress>('/api/v1/oidc/authentik/address', { method: 'POST', body: { public_url, confirm } }),
  mine: () => api<MyProvider[]>('/api/v1/oidc/me'),
  link: (slug: string, password: string) => api<{ url: string }>(`/api/v1/oidc/${encodeURIComponent(slug)}/link`, { method: 'POST', body: { password } }),
  unlink: (slug: string) => api<void>(`/api/v1/oidc/${encodeURIComponent(slug)}/link`, { method: 'DELETE' }),
  /** The address the provider knows becomes the own one (not confirmed: the link that confirms it is a step of its own). */
  takeAddress: (slug: string) => api<{ email: string; email_confirmed: boolean }>('/api/v1/oidc/me/address', { method: 'POST', body: { slug } }),
}

/** The address a provider's button leads to (a full page load: the browser leaves for the provider). */
export function providerStart(slug: string, extra: { invite?: string; next?: string } = {}): string {
  const query = new URLSearchParams()
  if (extra.invite) query.set('invite', extra.invite)
  if (extra.next && extra.next !== '/') query.set('next', extra.next)
  const text = query.toString()
  return `/api/v1/oidc/${encodeURIComponent(slug)}/start${text ? `?${text}` : ''}`
}

export const passkeyApi = {
  list: () => api<Passkey[]>('/api/v1/auth/passkeys'),
  begin: () => api<{ options: string }>('/api/v1/auth/passkeys/begin', { method: 'POST' }),
  add: (credential: unknown, name: string, password: string) =>
    api<{ passkey: Passkey; recovery_codes: string[] | null; account: Me }>('/api/v1/auth/passkeys', { method: 'POST', body: { credential, name, password } }),
  remove: (id: string, password: string) => api<Me>(`/api/v1/auth/passkeys/${encodeURIComponent(id)}/remove`, { method: 'POST', body: { password } }),
  signInBegin: () => api<{ options: string }>('/api/v1/auth/passkey/begin', { method: 'POST' }),
  signIn: (credential: unknown, remember: boolean) => api<Me>('/api/v1/auth/passkey', { method: 'POST', body: { credential, remember } }),
}

export type ApiKey = {
  id: number
  name: string
  /** `read`: programs that read. */
  scope: 'read'
  prefix: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  blocked: boolean
}
/** What the operator sees of a key: whose it is, what it is called and its first characters, never the key. */
export type AnyApiKey = ApiKey & { account: string }

/** API keys for programs that read. */
export const apiKeysApi = {
  list: () => api<{ allowed: boolean; keys: ApiKey[] }>('/api/v1/api-keys'),
  make: (name: string, days: number | null, scope: ApiKey['scope'] = 'read') =>
    api<{ key: ApiKey; secret: string }>('/api/v1/api-keys', { method: 'POST', body: { name, days, scope } }),
  remove: (id: number) => api<void>(`/api/v1/api-keys/${id}`, { method: 'DELETE' }),
  /** The operator allows API keys for everybody (the switch under Settings, API). */
  allow: () => api<unknown>('/api/v1/settings', { method: 'PUT', body: { api_keys_allowed: true } }),
  every: () => api<AnyApiKey[]>('/api/v1/admin/api-keys'),
  block: (id: number) => api<AnyApiKey>(`/api/v1/admin/api-keys/${id}/block`, { method: 'POST' }),
}

/** What a connection for an e-reader is for: the catalogue for reading apps (`opds`), the progress sync of KOReader (`kosync`)
 * and the sync of a Kobo (`kobo`, whose secret is part of its address). */
export type EreaderKind = 'opds' | 'kosync' | 'kobo'
/** A connection the account made for a reading app: the name, the first characters of its secret (never the secret). */
export type EreaderLink = {
  id: number
  kind: EreaderKind
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
  /** The operator blocked it for good. */
  blocked: boolean
}
/** What the operator sees of a connection: whose it is. */
export type AnyEreaderLink = EreaderLink & { account: string }
/** The Kindle of the account: its address at Amazon, whether the server can mail, and the sender Amazon has to be told to trust. */
export type KindleState = { address: string; mail_ready: boolean; sender: string; max_megabytes: number }
export type EreadersState = {
  allowed: boolean
  links: EreaderLink[]
  kindle: KindleState
  /** The editions on the account's list for the Kobo that it still sees. Absent from an older server. */
  kobo?: number[]
  /** The address nexbooks hands a Kobo for everything else it asks. Absent from an older server. */
  kobo_base?: string
}

/** Connections for e-readers and reading apps, the address of the Kindle, and mailing a book to it. */
export const ereadersApi = {
  state: () => api<EreadersState>('/api/v1/ereaders'),
  make: (name: string, kind: EreaderKind = 'opds') =>
    api<{ link: EreaderLink; username: string; secret: string }>('/api/v1/ereaders/links', { method: 'POST', body: { name, kind } }),
  remove: (id: number) => api<void>(`/api/v1/ereaders/links/${id}`, { method: 'DELETE' }),
  setKindle: (address: string) => api<KindleState>('/api/v1/ereaders/kindle', { method: 'PUT', body: { address } }),
  testKindle: () => api<{ sent: boolean }>('/api/v1/ereaders/kindle/test', { method: 'POST' }),
  /** Queues the mail with the EPUB `fileId` to the Kindle of the account; it goes in the background. */
  sendToKindle: (fileId: number) => api<{ queued: boolean }>(`/api/v1/files/${fileId}/kindle`, { method: 'POST' }),
  /** Puts an e-book on the account's list for the Kobo, or takes it off; the Kobos are told at their next sync. */
  listForKobo: (editionId: number) => api<{ listed: boolean }>(`/api/v1/editions/${editionId}/kobo`, { method: 'PUT' }),
  unlistForKobo: (editionId: number) => api<{ listed: boolean }>(`/api/v1/editions/${editionId}/kobo`, { method: 'DELETE' }),
  /** The operator switches e-readers on or off for everybody (Settings, API). */
  allow: (on: boolean) => api<unknown>('/api/v1/settings', { method: 'PUT', body: { ereaders_allowed: on } }),
  every: () => api<AnyEreaderLink[]>('/api/v1/admin/ereaders/links'),
  block: (id: number) => api<AnyEreaderLink>(`/api/v1/admin/ereaders/links/${id}/block`, { method: 'POST' }),
  /** Deletes a connection of any account; the only way to lift a block. */
  removeAny: (id: number) => api<void>(`/api/v1/admin/ereaders/links/${id}`, { method: 'DELETE' }),
}

/** A device or app of the account: it speaks with a token of its own (`Authorization: Bearer`). */
export type PairedDevice = {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
  /** The device of this account that connected this one from the app (it confirmed its code). Revoking that device
   * revokes this one too. Absent or null for a device made in the browser. */
  via?: { id: string; name: string } | null
}

/**
 * A code that connects the iPhone app (`POST /api/v1/devices/pairing`). The server also sends the secret and the link that
 * the QR code holds. The QR code is drawn by the server, so the page shows neither: it keeps the link in memory only (never
 * in the page's markup) for the one button that opens it on a phone, and drops the secret.
 */
export type AppPairing = {
  id: string
  /** To type by hand, as people read it: `ABCD-2345`. */
  code: string
  expires_at: string
  /** The address the app will remember. */
  address: string
  /** The QR code as SVG. */
  qr_svg: string
  /** What the QR code holds (`nexbooks://connect?...`), for "Open in the app" on the phone itself. It carries the secret:
   * it is used by a button and never written into the page. */
  link: string
}
export type AppPairingState = { state: 'waiting' | 'connected' | 'expired'; device: { id: string; name: string } | null }
/** A device without a camera that asks to be connected (`/api/v1/devices/pair-requests`): how it calls itself, and when. */
export type PairRequestPreview = { id: string; device_name: string; requested_at: string; expires_at: string }
export type PairRequestState = { state: 'confirmed' | 'connected' | 'expired'; device: { id: string; name: string } | null }

export const devicesApi = {
  list: () => api<PairedDevice[]>('/api/v1/devices'),
  /** The token comes back once. */
  make: (name: string) => api<{ device: PairedDevice; token: string }>('/api/v1/devices', { method: 'POST', body: { name } }),
  revoke: (id: string) => api<void>(`/api/v1/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** A code for the iPhone app, made for the address this browser is at. */
  pair: async (address: string): Promise<AppPairing> => {
    const made = await api<AppPairing & { secret?: string; link?: string }>('/api/v1/devices/pairing', { method: 'POST', body: { address } })
    return { id: made.id, code: made.code, expires_at: made.expires_at, address: made.address, qr_svg: made.qr_svg, link: made.link ?? '' }
  },
  /** Whether the app took the code. */
  pairing: (id: string) => api<AppPairingState>(`/api/v1/devices/pairing/${encodeURIComponent(id)}`),
  /** Withdraws a code nobody used; `keepalive` lets it leave while the page closes. */
  unpair: (id: string, keepalive = false) => api<void>(`/api/v1/devices/pairing/${encodeURIComponent(id)}`, { method: 'DELETE', keepalive }),
  /** The code a device without a camera shows (an Apple TV), typed here: how the device is named. Changes nothing. */
  lookUpRequest: (code: string) => api<PairRequestPreview>('/api/v1/devices/pair-requests/lookup', { method: 'POST', body: { code } }),
  /** Connects that device to this account. The id comes from `lookUpRequest`, the code is typed again. */
  confirmRequest: (id: string, code: string) =>
    api<PairRequestState>(`/api/v1/devices/pair-requests/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: { code } }),
  /** Whether the device fetched its token. */
  requestState: (id: string) => api<PairRequestState>(`/api/v1/devices/pair-requests/${encodeURIComponent(id)}`),
  /** Takes a confirmation back as long as the device has not fetched its token. */
  withdrawRequest: (id: string) => api<void>(`/api/v1/devices/pair-requests/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

/** The password for a backup rides in a header, as base64 of its UTF-8: a header carries no umlauts. */
export function passwordHeader(password: string): string {
  let bytes = ''
  for (const byte of new TextEncoder().encode(password)) bytes += String.fromCharCode(byte)
  return btoa(bytes)
}

export function avatarUrl(person: { id: number; avatar: string | null }): string | null {
  return person.avatar ? `/api/v1/avatars/${person.id}?v=${encodeURIComponent(person.avatar)}` : null
}

// ---- Web Push -------------------------------------------------------------------------------------------------------

export type PushDevice = { id: string; name: string; phone: boolean; since: string; last: string | null }
export type PushState = { key: string; devices: PushDevice[] }
export type PushResult = { sent: number; gone: number; failed: number }
export type PushSettings = { devices: number; key: string; contact: string; contact_used: string; known: string[]; hosts: string[] }

/** Web Push: the own devices and a probe; the operator's card. */
export const pushApi = {
  state: () => api<PushState>('/api/v1/push'),
  add: (subscription: { endpoint: string; p256dh: string; auth: string; installed: boolean }) =>
    api<PushDevice>('/api/v1/push/devices', { method: 'POST', body: subscription }),
  lookup: (endpoint: string) => api<{ id: string | null }>('/api/v1/push/devices/lookup', { method: 'POST', body: { endpoint } }),
  rename: (id: string, name: string) => api<PushDevice>(`/api/v1/push/devices/${encodeURIComponent(id)}`, { method: 'PUT', body: { name } }),
  remove: (id: string) => api<void>(`/api/v1/push/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  probe: () => api<PushResult>('/api/v1/push/test', { method: 'POST' }),
  settings: () => api<PushSettings>('/api/v1/settings/push'),
  saveSettings: (change: { contact?: string; hosts?: string[] }) => api<PushSettings>('/api/v1/settings/push', { method: 'PUT', body: change }),
  renew: (current_password: string) => api<PushSettings>('/api/v1/settings/push/renew', { method: 'POST', body: { current_password } }),
}

export const accountsApi = {
  remove: (id: number, body: { current_password: string; confirm_name: string }) =>
    api<void>(`/api/v1/accounts/${id}`, { method: 'DELETE', body }),
}

// ---- Libraries and books --------------------------------------------------------------------------------------------

export type LibraryKind = 'ebook' | 'audio' | 'both'
export type LibraryRight = 'read' | 'acquire' | 'manage'
export type ScanState = 'idle' | 'queued' | 'running'
export type LibraryCounts = {
  ebooks: number
  audiobooks: number
  files: number
  bytes: number
  problems: number
  /** Books the matching is not sure about (only for a member with manage). */
  review: number
  /** The books that have a place in the matching with the sources, and how many of them wait for it (only for a member with manage). */
  entries: number
  pending: number
}

/** A library as a member sees it (no path, no members). */
export type Library = {
  id: number
  name: string
  kind: LibraryKind
  my_right: LibraryRight | null
  state: ScanState
  counts: LibraryCounts
  /** While a run is going: the files done and the files to do (zero otherwise). */
  progress: { done: number; total: number }
  /** The matching with the sources, and the sources it waits for because they rest (only for a member with manage). `off`: books wait and no source is on, so nothing is matched. */
  matching: ScanState | 'off'
  resting: { source: SourceId; until: string }[]
}
export type LibraryMember = { user_id: number; name: string; display_name: string; right: LibraryRight }
/** What the last scan found; `aborted`: the folder was gone (`offline`), empty while books are known (`empty`), too big for one walk (`truncated`), or the run failed (`error`). */
export type ScanReport = { seen: number; added: number; changed: number; missing: number; problems: number; deferred: number; links: number; seconds: number; aborted: 'offline' | 'empty' | 'truncated' | 'error' | null }
/** A library as the operator sees it. */
export type LibraryAdmin = Library & {
  path: string
  scan_enabled: boolean
  scanned_at: string | null
  report: ScanReport | null
  members: LibraryMember[]
}
export type LibraryProblem = { path: string; reason: string; message: string; seen_at: string }
export type LibraryForm = { name: string; path: string; kind: LibraryKind; scan_enabled: boolean }

export const librariesApi = {
  mine: () => api<Library[]>('/api/v1/libraries'),
  every: () => api<LibraryAdmin[]>('/api/v1/libraries/admin'),
  roots: () => api<{ roots: { root: string; folders: string[] }[] }>('/api/v1/libraries/roots'),
  create: (form: LibraryForm) => api<LibraryAdmin>('/api/v1/libraries', { method: 'POST', body: form }),
  change: (id: number, change: Partial<Omit<LibraryForm, 'path'>>) => api<LibraryAdmin>(`/api/v1/libraries/${id}`, { method: 'PATCH', body: change }),
  remove: (id: number) => api<void>(`/api/v1/libraries/${id}`, { method: 'DELETE' }),
  members: (id: number, members: { user_id: number; right: LibraryRight }[]) => api<LibraryAdmin>(`/api/v1/libraries/${id}/members`, { method: 'PUT', body: { members } }),
  /** Only these persons change, each from the right the caller saw (`was`, null for not a member) to `right` (null to take them out); 409 `members_changed` when somebody else changed one meanwhile. */
  changeMembers: (id: number, changes: { user_id: number; was: LibraryRight | null; right: LibraryRight | null }[]) => api<LibraryAdmin>(`/api/v1/libraries/${id}/members`, { method: 'PATCH', body: { changes } }),
  /** `everything` reads every file again, not only the new and changed ones. */
  scan: (id: number, everything = false) =>
    api<{ state: 'queued' | 'running' }>(`/api/v1/libraries/${id}/scan`, { method: 'POST', ...(everything ? { body: { everything: true } } : {}) }),
  problems: (id: number, offset = 0) => api<{ total: number; items: LibraryProblem[] }>(`/api/v1/libraries/${id}/problems`, { query: { limit: 50, offset } }),
}

/** `aliases`: the other names the person is proved to write under (a pen name). */
export type PersonRef = { id: number; name: string; aliases?: string[] }
export type EditionBrief = {
  id: number
  library_id: number
  kind: 'ebook' | 'audio'
  variant: string
  variant_uncertain: boolean
  language: string
  /** The language was worked out (from the e-book of the work, or from the library), not told by a file or a source. */
  language_guess: boolean
  /** The formats of the edition's files, best for reading first: `["epub", "azw3", "mobi"]`. */
  formats: string[]
  pages: number
  /** Seconds; 0 for an e-book. */
  duration: number
  narrators: string[]
}
/** A book in a list. `cover_url` is null without a cover (the interface draws one in `color`). */
export type WorkBrief = {
  id: number
  title: string
  authors: PersonRef[]
  series: { id: number; name: string; pos: number | null } | null
  genre: string
  /** The genre is a guess: no file and no source said it, it is what most of the author's other books in the library are. */
  genre_guess: boolean
  color: string
  cover_url: string | null
  pages: number
  year: number | null
  pattern: number
  tall: number
  gold: boolean
  audio_only: boolean
  added_at: string
  editions: EditionBrief[]
}
/** What the list view adds to a book (`extras=list`): the caller's own place and end, and for a member who manages a library the
 * book lies in, the matching and the libraries. */
export type Mine = {
  ebook: number | null
  audio: number | null
  ebook_done: boolean
  audio_done: boolean
  moved_at: string | null
  match: 'sure' | 'maybe' | 'open' | 'pending' | 'decided' | null
  libraries: string[]
}
export type WorkRow = WorkBrief & { mine?: Mine | null }
export type WorkPage = { items: WorkRow[]; total: number; page: number; per_page: number }
export type BookStats = {
  works: number
  editions: number
  ebook_editions: number
  audio_editions: number
  works_with_ebook: number
  works_with_audio: number
  genres: Record<string, number>
  languages: Record<string, number>
}

export type FileOut = {
  id: number
  format: string
  path: string
  size: number
  part: number
  duration: number
  sha256: string
  /** Copy-protected: nexbooks does not open it (the file can still be downloaded). */
  protected: boolean
}
export type ChapterOut = { number: number; title: string; start: number; target: string; depth: number }
/** An edition on the page of a book: the brief one, and what the page to correct it needs. */
export type EditionOut = EditionBrief & {
  title: string
  subtitle: string
  isbn: string
  asin: string
  publisher: string
  published: string
  library_name: string
  translators: string[]
  /** Of this edition (the book shows the lead edition's): its authors, series, place in it and genre. */
  authors: string[]
  series_name: string
  series_pos: number | null
  genre: string
  genre_guess: boolean
  /** The fields a person corrected by hand (each can be taken back), and the sources that know the edition. */
  corrected: string[]
  matched_by: SourceId[]
  files: FileOut[]
  chapters: ChapterOut[]
  /** The caller may move this edition into another library they manage (`editionsApi.moveOptions`). */
  can_move?: boolean
  /** The release this edition was fetched from, for a member who looks after its library; null for a book nexbooks did not fetch. */
  delivery?: { release: string; indexer: string; delivered_at: string | null; trash_days: number } | null
  /** For a member who looks after its library: how many days the trash keeps the edition (`editionsApi.trash`); 0 for anybody else. */
  trash_days?: number
}
export type WorkOut = Omit<WorkBrief, 'editions'> & { description: string; original_title: string; original_language: string; editions: EditionOut[]; duplicates: number[][] }
export type SeriesOut = { id: number; name: string; works: WorkBrief[] }
/** Another name a person is known by (a pen name), with the id a split takes. */
export type PenName = { id: number; name: string }
export type PhotoCredit = { source: string; author: string; license: string; license_url: string; page_url: string }
export type BioText = { text: string; language: string; source: string; url: string }
export type Bio = { texts: BioText[]; born: number | null; died: number | null }
export type PersonOut = {
  id: number
  name: string
  /** Other names the person writes under (pen names), proved by a source or the same ISBN or ASIN. */
  aliases?: string[]
  pen_names?: PenName[]
  /** Whether the caller may split a pen name off. */
  can_split?: boolean
  /** Whether the caller may join this author with another one (the same right). */
  can_merge?: boolean
  wrote: WorkBrief[]
  narrated: WorkBrief[]
  /** The photo (from the author's folder or from a source), who made it and under which licence, and a short text. */
  photo_url: string | null
  credit: PhotoCredit | null
  bio: Bio | null
}
export type AuthorCard = { id: number; name: string; sort_name: string; works: number; audio: number; genre: string; added_at: string | null; photo_url: string | null }
export type AuthorPage = { items: AuthorCard[]; total: number; page: number; per_page: number; genres: Record<string, number> }
/** One edition behind a hit of the catalogue, as a library or a shop lists it ("show the editions"). */
export type CatalogHitEdition = {
  source: SourceId
  form: 'ebook' | 'audio'
  language: string
  title: string
  subtitle: string
  year: number | null
  isbn: string
  asin: string
  publisher: string
  narrators: string[]
  minutes: number | null
  variant: '' | 'unabridged' | 'abridged' | 'dramatized' | string
  pages: number | null
}
/** What a hit of the catalogue is: a book, a box or collection, a study guide, another book about the book, a play, a part, a
 * comic of the book. */
export type CatalogLabel = 'work' | 'box' | 'guide' | 'companion' | 'drama' | 'part' | 'comic'
/** Why a hit cannot be acquired: it is no book of its own, or a book named like its series whose volume nobody knows. */
export type CatalogWhyNot = '' | 'not_a_book' | 'volume_unknown'
/** Where a hit stands: the book asked for, the other volumes of its series, the rest, what is labelled, a book in none of
 * the languages fetched in. */
export type CatalogGroup = 'match' | 'series' | 'more' | 'related' | 'elsewhere'
/** One book of the catalogues (a work): its e-books, recordings, translations and editions are one hit. The title is the
 * one of the first language the operator fetches in that the book has (`language`); `allowed` is false for a book in none. */
export type CatalogHit = {
  key: string
  /** The page of the book (`discoverApi.book`), null until the hit is opened (`worksApi.catalogBook` makes it), and the book
   * of the caller's shelves it is, when it is one. */
  token: string | null
  owned_work_id: number | null
  title: string
  language: string
  allowed: boolean
  languages: string[]
  authors: string[]
  year: number | null
  series: string
  series_pos: number | null
  label: CatalogLabel
  group: CatalogGroup
  ebook: boolean
  audio: boolean
  /** Whether "E-Book", "Hörbuch" and "Beides" are offered, and why not. */
  procurable: boolean
  why_not: CatalogWhyNot
  /** The cover, fetched by the server from its source when the page shows it. */
  cover_url: string | null
  sources: SourceId[]
  editions: CatalogHitEdition[]
}
/** An author among the hits of a search: the page is the person of the caller's libraries (`person_id`), else the
 * catalogue (`token`). `works` is null while the catalogue was never built. */
export type AuthorHit = {
  name: string
  person_id: number | null
  token: string | null
  alias: string
  works: number | null
  on_shelf: number
  soon: number
  photo_url: string | null
  genre: string
}
/** A source that gave nothing for a search: it rests after errors until `until`, or did not answer (`until` null). */
export type CatalogResting = { source: SourceId; until: string | null }
/** The answer of the catalogues. `pending`: sources are still being asked; `catalogAgain(ticket)` gives the answer with what
 * they add; `resting`: the sources that gave nothing, so that the hits are incomplete. */
export type CatalogOut = { available: boolean; failed: boolean; pending?: boolean; ticket?: string | null; items: CatalogHit[]; people?: AuthorHit[]; resting?: CatalogResting[] }

/** The catalogue of an author's works. */
export type CatalogKind = 'roman' | 'erz' | 'bio' | 'sach' | 'lyrik' | 'kinder' | 'box' | 'unsure'
export type CatalogEdition = { language: string; title: string; year: number | null; released: string; upcoming: boolean; ebook: boolean; audio: boolean }
export type CatalogWork = {
  key: string
  kind: CatalogKind
  why: string
  moved: boolean
  pen: string
  series: string
  series_pos: number | null
  year: number | null
  orig_language: string
  editions: CatalogEdition[]
  sources: string[]
  /** The book of the sources behind the work: the wish, the wish list and its page go through it. */
  token: string | null
  /** The book on the person's own shelf this is (only their libraries). */
  work: WorkBrief | null
  /** A book on the shelf that may be this work (the same place in its series, a title that is only the series and a
   * number): "Alle fehlenden beschaffen" leaves the work out. */
  maybe_have?: WorkBrief | null
  have_ebook: boolean
  have_audio: boolean
  have_languages: string[]
  wished: boolean
  color: string
  cover_url: string | null
  pages: number | null
  minutes: number | null
}
export type AuthorCatalog = {
  token: string
  name: string
  person_id: number | null
  aliases: string[]
  born: string
  died: string
  orig_language: string
  languages: string[]
  state: 'ready' | 'building' | 'no_source' | 'main_off' | 'failed'
  refreshing: boolean
  built_at: string | null
  sources: { source: string; works: number }[]
  away: string[]
  sure: number
  unsure: number
  level: 'low' | 'mid' | 'high'
  works_total: number
  on_shelf: number
  soon: number
  first_year: number | null
  last_year: number | null
  can_move: boolean
  genre: string
  /** Other people of the name are known: their books are left out, what no source proves stands under "unsure". */
  common_name?: boolean
  /** An author nobody of the libraries is: a photo and a short text from Wikidata (two works of the catalogue prove it). */
  photo_url?: string | null
  credit?: PhotoCredit | null
  bio?: Bio | null
  /** The caller's watch of this author, or null. */
  watch?: AuthorWatch | null
  works: CatalogWork[]
}

/** Watching an author: new books are wished as they are announced and come out, in one language. */
export type WatchKind = 'ebook' | 'audio' | 'both'
/**
 * A book announced in the language of the watch. `date` is the day, or the year alone (`2027`) when no day is known.
 * `state`: `wished`, `listed` (on the wish list; `why` says why when the person may ask for books), `year` (wished when
 * the day is known), `held` (not wished, the code in `why`), `covered` (somebody else wished it for the library it would
 * go into: wished for the person when that wish goes) or `open` (the look for it is still to come).
 */
export type WatchedBook = { key: string; title: string; language: string; date: string; state: 'wished' | 'listed' | 'year' | 'held' | 'covered' | 'open'; kinds: ('ebook' | 'audio')[]; why?: string }
/** A book announced only in another of the languages books are fetched in: on the wish list until it is in `language`. */
export type NotYetBook = { key: string; title: string; language: string; announced_in: string; listed: boolean }
export type AuthorWatch = {
  token: string
  name: string
  person_id: number | null
  photo_url: string | null
  genre: string
  kind: WatchKind
  /** The language the person chose, or empty for the first of `languages`. */
  language: string
  /** The languages books are fetched in for this author, the first one first, and the one the watch wishes in now. */
  languages: string[]
  target: string
  /** The language the person chose is no longer one books are fetched in: the watch wishes in the first. */
  language_off: boolean
  more: boolean
  /** Neither Audible nor Hardcover may be asked: few books to come are known. */
  sources_few: boolean
  /** The main switch of the automatic work is off: the watch wishes nothing until it is on again. */
  paused: boolean
  /** `waiting` while the catalogue of the author is not built; the first look comes when it is. */
  state: 'waiting' | 'watching'
  can_acquire: boolean
  created_at: string
  checked_at: string | null
  wished: number
  listed: number
  announced: WatchedBook[]
  next: WatchedBook | null
  not_yet: NotYetBook[]
  /** What the look this answer follows made; `stopped` says why it broke off (`no_indexers`, `share`, `list_full`, `many`, `off`). */
  made?: { wished: number; listed: number; stopped: string } | null
}

export const catalogApi = {
  /** The works of an author of the caller's libraries. */
  person: (id: number) => api<AuthorCatalog>(`/api/v1/people/${id}/catalog`),
  /** The works of any author, by the token of the catalogue. */
  author: (token: string) => api<AuthorCatalog>(`/api/v1/catalog/authors/${encodeURIComponent(token)}`),
  move: (token: string, key: string, kind: Exclude<CatalogKind, 'unsure'>) =>
    api<AuthorCatalog>(`/api/v1/catalog/authors/${encodeURIComponent(token)}/works/${encodeURIComponent(key)}/kind`, { method: 'PUT', body: { kind } }),
  unmove: (token: string, key: string) => api<AuthorCatalog>(`/api/v1/catalog/authors/${encodeURIComponent(token)}/works/${encodeURIComponent(key)}/kind`, { method: 'DELETE' }),
  /** The catalogue of an author by name, made when the person opens a hit of the search (typing makes none). */
  open: (name: string) => api<{ token: string; person_id: number | null }>('/api/v1/catalog/authors', { method: 'POST', body: { name } }),
  /** The authors of the caller's libraries whose name holds the words. */
  people: (q: string, signal?: AbortSignal) => api<{ items: AuthorHit[] }>('/api/v1/search/people', { query: { q, limit: 4 }, signal }),
}

export const watchApi = {
  /** The authors the caller watches. */
  list: (signal?: AbortSignal) => api<{ items: AuthorWatch[]; limit: number }>('/api/v1/watching', { signal }),
  /** Switches the watch on or changes it; the language is left out for the server's choice. */
  set: (token: string, body: { kind: WatchKind; language?: string; more: boolean }) =>
    api<AuthorWatch>(`/api/v1/watching/authors/${encodeURIComponent(token)}`, { method: 'PUT', body }),
  /** Switches it off; the wishes it made stay (`keep`) or go when nothing has started on them (`remove`). */
  stop: (token: string, wishes: 'keep' | 'remove') =>
    api<void>(`/api/v1/watching/authors/${encodeURIComponent(token)}`, { method: 'DELETE', query: { wishes } }),
}

export const worksApi = {
  list: (page = 1, perPage = 60, genre?: string, signal?: AbortSignal, extras?: 'list') =>
    api<WorkPage>('/api/v1/works', { query: { page, per_page: perPage, ...(genre ? { genre } : {}), ...(extras ? { extras } : {}) }, signal }),
  stats: () => api<BookStats>('/api/v1/works/stats'),
  get: (id: number) => api<WorkOut>(`/api/v1/works/${id}`),
  series: (id: number) => api<SeriesOut>(`/api/v1/series/${id}`),
  person: (id: number, language?: string) => api<PersonOut>(`/api/v1/people/${id}`, { query: language ? { language } : {} }),
  /** Says that another name of a person is somebody else: the two are two people again, for good. */
  /** Says that the author and another one are one person, and which of the two names stays; the answer is the author that stays. */
  mergePerson: (id: number, otherId: number, keep: 'this' | 'other') => api<PersonOut>(`/api/v1/people/${id}/merge`, { method: 'POST', body: { other_id: otherId, keep } }),
  splitPerson: (id: number, aliasId: number) => api<PersonOut>(`/api/v1/people/${id}/split`, { method: 'POST', body: { alias_id: aliasId } }),
  /** The books of the caller's libraries that the words find, best first. */
  search: (q: string, limit = 8, signal?: AbortSignal) => api<{ query: string; items: WorkBrief[] }>('/api/v1/search', { query: { q, limit }, signal }),
  /** The catalogues of the sources the operator switched on. */
  catalog: (q: string, signal?: AbortSignal) => api<CatalogOut>('/api/v1/search/catalog', { method: 'POST', body: { q }, signal }),
  /** The same search again, with what the slower sources added (while an answer is `pending`). */
  catalogAgain: (ticket: string, signal?: AbortSignal) => api<CatalogOut>(`/api/v1/search/catalog/${encodeURIComponent(ticket)}`, { signal }),
  /** The page gave up on the search: the server asks the sources nothing more for it (`keepalive`: it leaves while the page
   * goes). */
  catalogStop: (ticket: string) => api<void>(`/api/v1/search/catalog/${encodeURIComponent(ticket)}`, { method: 'DELETE', keepalive: true }),
  /** The page of a hit of the search, made now that the person opens or acquires it. */
  catalogBook: (ticket: string, key: string) =>
    api<{ token: string }>(`/api/v1/search/catalog/${encodeURIComponent(ticket)}/books`, { method: 'POST', body: { key } }),
}

/** Where a file of a book is downloaded from (the browser's session decides whether it may). */
export function downloadUrl(fileId: number): string {
  return `/api/v1/files/${fileId}/download`
}

/** Where the caller is in an edition. `moved_at`: when the reader was at this place. */
export type ProgressOut = {
  edition_id: number
  work_id: number
  locator: string
  /** Of a recording: the place in seconds from the beginning of all its parts. Null for an e-book. */
  position: number | null
  fraction: number
  unit: 'chapter' | 'page' | 'time'
  chapter: number
  chapter_count: number
  chapter_title: string
  device_key: string
  device: string
  moved_at: string
  received_at: string
  /** Seconds since the reader was at this place, by the server's clock when it answered. */
  age: number
}
export type ProgressIn = {
  locator: string
  fraction: number
  unit: 'chapter' | 'page' | 'time'
  chapter: number
  chapter_count: number
  chapter_title: string
  device_key: string
  device: string
  /** Seconds since the reader was at this place (the server counts back from when the report arrives). */
  age: number
}
export type BookmarkOut = {
  id: number
  edition_id: number
  locator: string
  /** Of a recording: the place in seconds. Null for an e-book. */
  position: number | null
  fraction: number
  chapter_title: string
  /** A few words of the person's own about the place. */
  note: string
  created_at: string
}
export type EditionToRead = { work: WorkBrief; edition: EditionOut }

/** The bytes of a file for the reader: the same file as the download, with `Range` and an `ETag`. */
export function contentUrl(fileId: number): string {
  return `/api/v1/files/${fileId}/content`
}

export const readingApi = {
  edition: (id: number) => api<EditionToRead>(`/api/v1/editions/${id}`),
  progress: (id: number) => api<ProgressOut | null>(`/api/v1/editions/${id}/progress`),
  /** The caller's places in the editions of one book, or in all books. */
  places: (workId?: number) => api<ProgressOut[]>('/api/v1/progress', { query: { work: workId } }),
  save: (id: number, body: ProgressIn, keepalive = false) =>
    api<{ applied: boolean; progress: ProgressOut }>(`/api/v1/editions/${id}/progress`, { method: 'PUT', body, keepalive }),
  bookmarks: (id: number) => api<BookmarkOut[]>(`/api/v1/editions/${id}/bookmarks`),
  addBookmark: (id: number, body: { locator: string; fraction: number; chapter_title: string; note?: string }) =>
    api<BookmarkOut>(`/api/v1/editions/${id}/bookmarks`, { method: 'POST', body }),
  changeNote: (id: number, note: string) => api<BookmarkOut>(`/api/v1/bookmarks/${id}`, { method: 'PATCH', body: { note } }),
  removeBookmark: (id: number) => api<void>(`/api/v1/bookmarks/${id}`, { method: 'DELETE' }),
}

// ---- The reading stack ------------------------------------------------------------------------------------------

/** A book the caller finished: `read` (an e-book) or `listened` (a recording), on a day (`YYYY-MM-DD`) in their time zone. */
export type HistoryOut = { id: number; work_id: number; edition_id: number; kind: 'read' | 'listened'; finished_on: string; source: 'auto' | 'hand'; created_at: string }
export type StackEntry = HistoryOut & { work: WorkBrief; pages: number; seconds: number }
export type StackYear = {
  year: number
  books: number
  read: number
  listened: number
  pages: number
  seconds: number
  genre: string
  thickest: { work: WorkBrief; pages: number } | null
  entries: StackEntry[]
}
/** The years, oldest first, and today as the caller's time zone has it (`YYYY-MM-DD`). */
export type StackOut = { years: StackYear[]; today: string }

export const historyApi = {
  stack: (signal?: AbortSignal) => api<StackOut>('/api/v1/stack', { signal }),
  /** The caller's own entries for one book, the newest first. */
  ofWork: (workId: number) => api<HistoryOut[]>('/api/v1/history', { query: { work: workId } }),
  /** "Finished": today, or the day named. The same day twice is the one entry (the answer is 200 then). */
  finish: (editionId: number, finishedOn?: string) => api<HistoryOut>(`/api/v1/editions/${editionId}/finished`, { method: 'POST', body: finishedOn ? { finished_on: finishedOn } : {} }),
  /** "Not finished": takes one of the caller's own entries away. */
  remove: (id: number) => api<void>(`/api/v1/history/${id}`, { method: 'DELETE' }),
}

export const authorsApi = {
  list: (query: { sort?: 'name' | 'count' | 'recent'; genre?: string; q?: string; page?: number; per_page?: number }, signal?: AbortSignal) =>
    api<AuthorPage>('/api/v1/authors', { query: { ...query }, signal }),
}

// ---- Tidying the files -----------------------------------------------------------------------------------------

export type OrganizeState = 'preview' | 'queued' | 'running' | 'done' | 'failed' | 'undoing' | 'undone'
export type OrganizeSummary = {
  move_books?: number
  stay_books?: number
  problem_books?: number
  files?: number
  move_files?: number
  problems?: Record<string, number>
  empty?: Record<string, number>
  digest?: string
  made?: string[]
  removed?: string[]
}
export type OrganizeRun = {
  id: number
  library_id: number
  scheme: string
  state: OrganizeState
  total: number
  done: number
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  summary: OrganizeSummary
}
export type OrganizeMove = { id: number; edition_id: number | null; title: string; source: string; target: string; state: 'planned' | 'moved' | 'undone' | 'skipped' | 'problem'; reason: string }
export type OrganizeStatus = {
  enabled: boolean
  writable: boolean
  reason: string
  scheme: string
  placeholders: { name: string; aliases: string[] }[]
  runs: OrganizeRun[]
}

export const organizeApi = {
  enabled: () => api<{ enabled: boolean }>('/api/v1/organize/settings'),
  setEnabled: (enabled: boolean) => api<{ enabled: boolean }>('/api/v1/organize/settings', { method: 'PUT', body: { enabled } }),
  status: (library: number) => api<OrganizeStatus>('/api/v1/organize/status', { query: { library } }),
  preview: (library_id: number, scheme: string) => api<OrganizeRun>('/api/v1/organize/preview', { method: 'POST', body: { library_id, scheme } }),
  run: (id: number) => api<OrganizeRun>(`/api/v1/organize/runs/${id}`),
  moves: (id: number, state: string | undefined, offset: number) =>
    api<{ items: OrganizeMove[]; total: number }>(`/api/v1/organize/runs/${id}/moves`, { query: { offset, limit: 100, ...(state ? { state } : {}) } }),
  start: (id: number) => api<OrganizeRun>(`/api/v1/organize/runs/${id}/start`, { method: 'POST', body: { confirm: true } }),
  undo: (id: number) => api<OrganizeRun>(`/api/v1/organize/runs/${id}/undo`, { method: 'POST', body: { confirm: true } }),
  discard: (id: number) => api<void>(`/api/v1/organize/runs/${id}`, { method: 'DELETE' }),
}

export type Correction = Partial<{ title: string | null; subtitle: string | null; authors: string[] | null; narrators: string[] | null; series: string | null; series_pos: number | null; genre: string | null; language: string | null; variant: 'unabridged' | 'abridged' | 'dramatized' | null }>

/** A library an edition may be moved into: where its book files would lie there (relative, by the scheme of filing). */
export type MoveTarget = {
  id: number
  name: string
  kind: LibraryKind
  writable: boolean
  write_problem: string
  preview: string[]
  more: number
  folder: string
  /** Another disk: the files are copied. */
  copies: boolean
  /** Why there is no place there (`taken`, `too_long`, `link` ...); empty when there is. */
  problem: string
}
export type MoveState = 'queued' | 'running' | 'done' | 'failed'
/** A move of an edition into another library, and where its files lie now. */
export type MoveOut = {
  id: number
  edition_id: number | null
  source: { id: number; name: string } | null
  target: { id: number; name: string } | null
  state: MoveState
  step: 'place' | 'clear' | 'done'
  error: string
  files: number
  placed: number
  removed: number
  where: 'source' | 'target' | 'both'
  left: number
  created_at: string
  started_at: string | null
  finished_at: string | null
}
export type MoveOptions = {
  edition_id: number
  kind: 'ebook' | 'audio'
  source: { id: number; name: string; writable: boolean; write_problem: string }
  files: number
  bytes: number
  targets: MoveTarget[]
  busy: boolean
  latest: MoveOut | null
}

export const movesApi = {
  options: (editionId: number) => api<MoveOptions>(`/api/v1/editions/${editionId}/move`),
  /** Moves the edition, once the person said yes to the question (`confirm`). */
  start: (editionId: number, libraryId: number) => api<MoveOut>(`/api/v1/editions/${editionId}/move`, { method: 'POST', body: { library_id: libraryId, confirm: true } }),
  read: (moveId: number) => api<MoveOut>(`/api/v1/moves/${moveId}`),
  retry: (moveId: number) => api<MoveOut>(`/api/v1/moves/${moveId}/retry`, { method: 'POST', body: { confirm: true } }),
}

export const editionsApi = {
  correct: (id: number, change: Correction) => api<{ work_id: number }>(`/api/v1/editions/${id}`, { method: 'PATCH', body: change }),
  /** Takes the match with the sources back. */
  unlink: (id: number) => api<{ to_look_at: number }>(`/api/v1/editions/${id}/unlink`, { method: 'POST' }),
  join: (id: number, workId: number) => api<{ work_id: number }>(`/api/v1/editions/${id}/join`, { method: 'POST', body: { work_id: workId } }),
  split: (id: number) => api<{ work_id: number }>(`/api/v1/editions/${id}/split`, { method: 'POST' }),
  /** A book nexbooks fetched is not the one wished: the files go to the trash, the release is blocked, another is searched for. */
  wrongBook: (id: number) => api<SentAway>(`/api/v1/editions/${id}/wrong-book`, { method: 'POST' }),
  /** One edition into the trash of its library, whatever brought it there; nothing is blocked or looked for. */
  trash: (id: number) => api<Discarded>(`/api/v1/editions/${id}/trash`, { method: 'POST' }),
}

// ---- Sources and matching -------------------------------------------------------------------------------------------

export type SourceId = 'hardcover' | 'openlibrary' | 'audible' | 'dnb' | 'apple' | 'wikidata' | 'bnf' | 'libris' | 'kb' | 'nb'
/** `ready`; `main_off` (the main switch), `off` (this source), `not_connected` (Hardcover without a token), `token_invalid`
 * (Hardcover refused it), `resting` (a pause after errors). */
export type SourceState = 'ready' | 'main_off' | 'off' | 'not_connected' | 'token_invalid' | 'resting'
export type SourceInfo = {
  id: SourceId
  enabled: boolean
  state: SourceState
  requests_today: number
  requests_per_day: number
  last_ok_at: string | null
  /** The code of the last failure (`timeout`, `rate_limited`, `server` ...), empty when it went well. */
  last_error: string
  last_error_at: string | null
  resting_until: string | null
  /** Books that wait for this source (its absence is why they wait). */
  waiting: number
}
export type SourcesOut = {
  enabled: boolean
  markets: string[]
  available_markets: string[]
  hardcover_connected: boolean
  /** Whether the operator has chosen about Audible (it is off from the start; the settings ask once). */
  audible_decided: boolean
  /** Whether authors are looked up on the internet for a photo and a short biography. */
  author_info: boolean
  sources: SourceInfo[]
  waiting: number
  to_look_at: number
}

export const sourcesApi = {
  state: () => api<SourcesOut>('/api/v1/sources'),
  save: (change: { enabled?: boolean; author_info?: boolean; markets?: string[]; sources?: Partial<Record<SourceId, boolean>> }) => api<SourcesOut>('/api/v1/sources', { method: 'PUT', body: change }),
  /** The token goes out once and is never read back. */
  connect: (token: string) => api<SourcesOut>('/api/v1/sources/hardcover/token', { method: 'PUT', body: { token } }),
  disconnect: () => api<SourcesOut>('/api/v1/sources/hardcover/token', { method: 'DELETE' }),
  rematch: () => api<{ books: number }>('/api/v1/sources/rematch', { method: 'POST' }),
}

export type ReviewWhy = { code: string; text: string }
export type Proposal = {
  id: string
  source: SourceId
  title: string
  subtitle: string
  authors: string[]
  narrators: string[]
  year: number | null
  languages: string[]
  series: string
  series_pos: number | null
  runtime_min: number | null
  variant: string
  market: string
  score: number
  why: ReviewWhy[]
  /** What is missing to be sure of it (grey), and what speaks against it though it could be the book (red). */
  missing: ReviewWhy[]
  against: ReviewWhy[]
  /** The numbers some reasons carry: `year_off` has the year of the file and of the source. */
  detail: Record<string, Record<string, number | string>>
  /** What would make this proposal surer, as words (`isbn`, `second_source`, `language`, `author`, `runtime`, `volume`, `year`). */
  lacks: string[]
  /** `or`: each of `lacks` is enough alone; `and`: all of them are needed together. */
  lacks_join: 'or' | 'and'
  /** What holds it back whatever its points: `author_weak`, `author_unknown`, `language_missing`; empty for nothing. */
  cap: string
  /** It cannot be the book; the last of `why` says why. */
  fatal: boolean
  /** Found by a search a person made. */
  found: boolean
  cover_url: string | null
}
export type ReviewItem = {
  id: number
  edition_id: number
  work_id: number
  library_id: number
  state: 'maybe' | 'open'
  reason: string
  reason_text: string
  kind: 'ebook' | 'audio'
  title: string
  subtitle: string
  authors: string[]
  language: string
  year: number | null
  series: string
  series_pos: number | null
  /** The running time of the files in minutes; null for an e-book. */
  runtime_min: number | null
  /** How many days the trash keeps a book that is thrown away from the list. */
  trash_days: number
  file: string
  cover_url: string | null
  /** What stands between the book and "sure": null without a proposal that could be the book. */
  gap: ReviewGap | null
  proposals: Proposal[]
}
export type ReviewGap = {
  sure_at: number
  points: number
  /** What would help: `isbn`, `second_source`, `language`, `author`, `runtime`, `volume`, `year`. */
  missing: string[]
  /** `or`: each of `missing` is enough alone; `and`: all of them are needed together. */
  join: 'or' | 'and'
  /** What holds the best proposal back whatever its points: `author_weak`, `author_unknown`, `language_missing`; empty for nothing. */
  cap: string
  /** Set when two proposals are about as good: what tells them apart (`volume`, `year`, `language`, `variant`, `title`, `nothing`). */
  tie: { difference: string; a: string | null; b: string | null } | null
}

/** How a field of the file stands against a proposal: the same, close, contradicted (`differs`), unsaid by the file, or unknown to the source. */
export type CompareState = 'agrees' | 'similar' | 'differs' | 'file_silent' | 'source_silent'
export type CompareField = 'title' | 'authors' | 'narrators' | 'series' | 'volume' | 'isbn' | 'language' | 'text_language' | 'year' | 'runtime' | 'publisher'
export type CompareRow = { field: CompareField; file: string; source: string; state: CompareState; unit: '' | 'minutes' }
export type FileLine = { id: number; name: string; folder: string; format: string; size: number; part: number; duration: number; pages: number; chapters: number; protected: boolean }
/** What the files of a book say about themselves; an empty value is "the file says nothing about it". */
export type FileSays = {
  title: string
  subtitle: string
  authors: string[]
  narrators: string[]
  series: string
  series_pos: number | null
  isbn: string
  asin: string
  publisher: string
  year: number | null
  language: string
  text_language: string
  genre: string
  subjects: string[]
  pages: number
  pages_counted: boolean
  duration: number
  chapters: number
}
export type FileFacts = {
  entry_id: number
  edition_id: number
  kind: 'ebook' | 'audio'
  folder: string
  files: FileLine[]
  says: FileSays
  cover_url: string | null
  proposals: { proposal_id: string; rows: CompareRow[] }[]
}
/** What became of an edition sent away: `searching` a wish looks for another release now, `idle` the wish is there but fetching by
 * itself is off, `have` the book has another edition of this kind, `none` no book is left to wish for, `failed` (`code` says why).
 * `trash_id` is null when no file was there to move (`missing` names the files that were gone); `people` is how many people had a
 * place, a mark or a finished pass that is kept with the files. */
export type Discarded = {
  trash_id: number | null
  work_id: number | null
  missing: string[]
  people: number
}

export type SentAway = {
  trash_id: number | null
  replacement: 'searching' | 'idle' | 'have' | 'none' | 'failed'
  code: string
  work_id: number | null
  missing: string[]
  people: number
}

export const reviewApi = {
  /** What the files of the book say about themselves, with the comparison against every proposal. */
  file: (entry: number) => api<FileFacts>(`/api/v1/review/${entry}/file`),
  list: (library: number, limit = 50, offset = 0) => api<{ total: number; items: ReviewItem[] }>(`/api/v1/libraries/${library}/review`, { query: { limit, offset } }),
  /** Every library the caller looks after, in one list (each entry names its library). */
  listAll: (limit = 50, offset = 0) => api<{ total: number; items: ReviewItem[] }>('/api/v1/review', { query: { limit, offset } }),
  accept: (entry: number, proposal_id: string) => api<{ to_look_at: number }>(`/api/v1/review/${entry}/accept`, { method: 'POST', body: { proposal_id } }),
  keep: (entry: number) => api<{ to_look_at: number }>(`/api/v1/review/${entry}/keep`, { method: 'POST' }),
  search: (entry: number, title: string, author: string) => api<ReviewItem>(`/api/v1/review/${entry}/search`, { method: 'POST', body: { title, author } }),
}


// ---- Discover ---------------------------------------------------------------------------------------------------------

export type DiscoverListName = 'best' | 'new' | 'soon' | 'trending'
export type FormKind = 'ebook' | 'audio'
export type FormBrief = { kind: FormKind; language: string; /** `unabridged`, `abridged` or `dramatized`; empty for an e-book. */ variant: string }
export type FormOut = FormBrief & { minutes: number | null; narrators: string[]; pages: number | null; source: SourceId }
/** A book of a source that is not (necessarily) on a shelf. `owned_work_id`: the book on the caller's own shelves, if it is one. */
export type DiscoverCard = {
  token: string
  title: string
  authors: string[]
  rank: number | null
  kinds: FormKind[]
  forms: FormBrief[]
  language: string
  /** `YYYY-MM-DD`, and whether that day is after today. */
  released: string
  upcoming: boolean
  genre: string
  color: string
  cover_url: string | null
  sources: SourceId[]
  owned_work_id: number | null
  wished: boolean
  series: string
  series_pos: number | null
}
export type SourceAvailability = { source: SourceId; state: SourceState }
export type DiscoverState = 'ready' | 'fetching' | 'empty' | 'failed' | 'main_off' | 'no_source'
export type DiscoverList = {
  list: DiscoverListName
  market: string
  format: 'all' | FormKind
  language: string
  genre: string
  hide_owned: boolean
  state: DiscoverState
  availability: SourceAvailability[]
  /** Whether any source of the list may be asked now; when not, the cards are what was kept earlier. */
  asking: boolean
  sources: SourceId[]
  fetched_at: string | null
  languages: { code: string; count: number }[]
  markets: string[]
  total: number
  owned: number
  items: DiscoverCard[]
}
export type Volume = { pos: number; state: 'own' | 'missing' | 'soon'; work: WorkBrief | null; book: DiscoverCard | null }
export type SeriesGap = {
  series_id: number
  name: string
  author: string
  have: number
  out: number
  missing: number
  soon: number
  pending: boolean
  volumes: Volume[]
}
export type AuthorMore = { person_id: number; name: string; on_shelf: number; pending: boolean; books: DiscoverCard[] }
export type ForYou = { state: 'ready' | 'main_off' | 'no_source'; sources: SourceId[]; pending: boolean; gaps: SeriesGap[]; authors: AuthorMore[] }
export type BookPage = DiscoverCard & {
  description: string
  year: number | null
  forms_known: FormOut[]
  volumes: Volume[]
  series_pending: boolean
  more: WorkBrief[]
  pending: boolean
  /** The authors, each with the page of their works: "Über ...". */
  about?: AuthorHit[]
}
export type WishList = {
  count: number
  items: DiscoverCard[]
  /** The books a watch of an author put on the list: the token of the book to the author's name, and why it is not wished. */
  via?: Record<string, string>
  via_why?: Record<string, string>
}

// ---- Browse: the themed shelves like in a bookshop -----------------------------------------------------------

export type ShelfSection = 'new' | 'popular' | 'award' | 'classic'
export type Department = 'bel' | 'sach' | 'kj'
/** A book on a shelf: the card of a list, and what the cover turned over says. */
export type ShelfCard = DiscoverCard & {
  /** The prize the source names for the book, as the source writes it; empty for none. */
  award: string
  /** The topics of the shelf the book is about. */
  topics: string[]
  pages: number | null
  /** The longest recording of the book in minutes, when a source says. */
  minutes: number | null
  year: number | null
  /** The words on the back cover when a source gave them (kept for a day); empty otherwise. */
  blurb: string
}
export type Sourcing = {
  state: DiscoverState
  availability: SourceAvailability[]
  asking: boolean
  /** Parts of the shelves are still being fetched: the page asks again after a few seconds. */
  pending: boolean
  sources: SourceId[]
  fetched_at: string | null
}
export type ShelfTile = { key: string; books: number; new: number; fan: DiscoverCard[] }
export type ShelfStrip = { shelf: string; section: ShelfSection; sections: { key: ShelfSection; count: number }[]; items: ShelfCard[] }
export type BrowseWall = Sourcing & {
  market: string
  markets: string[]
  hide_owned: boolean
  departments: { key: Department; shelves: ShelfTile[] }[]
  strips: ShelfStrip[]
}
export type PersonalKey = 'because' | 'like' | 'short' | 'long'
export type PersonalRow = { key: PersonalKey; author: string; genre: string; reads: WorkBrief[]; items: ShelfCard[] }
export type ShelfYears = '' | 'new' | 'mid' | 'old'
export type ShelfSort = 'pop' | 'new' | 'short'
export type ShelfQuery = {
  market?: string
  section?: ShelfSection | ''
  language?: string
  format?: 'all' | FormKind
  years?: ShelfYears
  topic?: string
  sort?: ShelfSort
  hide_owned?: boolean
  limit?: number
  offset?: number
}
export type ShelfPageOut = Sourcing & {
  shelf: string
  dept: Department
  market: string
  markets: string[]
  hide_owned: boolean
  section: ShelfSection | null
  language: string
  format: 'all' | FormKind
  years: ShelfYears
  topic: string
  sort: ShelfSort
  total: number
  owned: number
  offset: number
  limit: number
  sections: { key: ShelfSection; count: number }[]
  languages: { key: string; count: number }[]
  topics: { key: string; count: number }[]
  year_bounds: { new_from: number; mid_from: number }
  items: ShelfCard[]
}

/** What is empty or "all" is not sent: the server's own defaults stand. */
function sent(query: Record<string, string | number | boolean | undefined>) {
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '' && value !== 'all' && value !== undefined))
}

export const browseApi = {
  wall: (query: { market?: string; hide_owned?: boolean }, signal?: AbortSignal) => api<BrowseWall>('/api/v1/discover/browse', { query: sent(query), signal }),
  personal: (query: { market?: string; hide_owned?: boolean }, signal?: AbortSignal) =>
    api<{ rows: PersonalRow[] }>('/api/v1/discover/browse/personal', { query: sent(query), signal }),
  shelf: (key: string, query: ShelfQuery, signal?: AbortSignal) =>
    api<ShelfPageOut>(`/api/v1/discover/shelves/${encodeURIComponent(key)}`, { query: sent({ ...query, sort: query.sort === 'pop' ? undefined : query.sort }), signal }),
}

export const discoverApi = {
  /** A list, set against the caller's shelves. What is empty or "all" is not sent: the server's own defaults stand. */
  list: (name: DiscoverListName, query: { market?: string; format?: string; language?: string; genre?: string; hide_owned?: boolean }, signal?: AbortSignal) =>
    api<DiscoverList>(`/api/v1/discover/lists/${name}`, {
      query: Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '' && value !== 'all' && value !== undefined)),
      signal,
    }),
  forYou: (signal?: AbortSignal) => api<ForYou>('/api/v1/discover/for-you', { signal }),
  series: (id: number) => api<SeriesGap>(`/api/v1/discover/series/${id}`),
  book: (token: string) => api<BookPage>(`/api/v1/discover/books/${encodeURIComponent(token)}`),
}

export const wishApi = {
  list: () => api<WishList>('/api/v1/wishlist'),
  add: (token: string) => api<WishList>(`/api/v1/wishlist/${encodeURIComponent(token)}`, { method: 'PUT' }),
  remove: (token: string) => api<WishList>(`/api/v1/wishlist/${encodeURIComponent(token)}`, { method: 'DELETE' }),
}

// ---- Fetching books: indexers, Prowlarr, download programs, formats, automation ---------------------------

export type IndexerKind = 'newznab' | 'torznab'
export type IndexerState = 'ready' | 'resting' | 'key_rejected' | 'off'
/** A book category of an indexer: asked when `on`, in the language the operator gave it (`mixed`: the indexer does not keep them apart). */
/** `asked`: false for a category of a language books are not fetched in (settings, "Sprachen"): a search never asks it. */
/** `proposed`: the language the caps of the indexer state for it; `lang` differs when the operator set another. */
export type IndexerCategory = { id: number; name: string; type: 'ebook' | 'audio'; lang: string; on: boolean; suggested: boolean; asked?: boolean; proposed?: string }
export type SupportedParams = { search: string[]; book: string[]; audio: string[]; book_search: boolean; audio_search: boolean }
export type IndexerInfo = {
  id: number
  name: string
  kind: IndexerKind
  url: string
  key_set: boolean
  enabled: boolean
  priority: number
  daily_limit: number
  state: IndexerState
  used_today: number
  grabs: number
  /** Releases fetched today (counted apart from the requests). */
  grabs_today: number
  /** When its newest entries were read the last time. */
  rss_checked_at: string | null
  /** The code of the failure of the last reading; `rss_share_used` when the half of its limit of the day that the reading may use is spent. */
  rss_error: string
  /** When the count of the day starts again (midnight of the instance). */
  day_ends_at: string
  via_prowlarr: boolean
  categories: IndexerCategory[]
  resting_until: string | null
  /** `request_limit` (it said so), `api_limit` (its own limits are used up), `daily_limit` (the operator's limit of the day). */
  resting_cause: string
  last_error: string
  api_current: number | null
  api_max: number | null
  grab_current: number | null
  grab_max: number | null
  supported_params: SupportedParams
  caps_checked_at: string | null
  /** The languages books are fetched in (settings, "Sprachen"). */
  languages?: string[]
  /** How many chosen categories have another language than the caps state ("Vorschläge übernehmen"). */
  proposals?: number
}
export type IndexerForm = {
  name: string
  kind: IndexerKind
  url: string
  /** Left out: the saved key stays. */
  api_key?: string
  priority: number
  daily_limit: number
  enabled?: boolean
  categories?: number[]
  category_langs?: Record<string, string>
  language?: string
}
export type IndexerTest = {
  kind: IndexerKind | null
  book_search: boolean
  audio_search: boolean
  search_params: string[]
  book_params: string[]
  audio_params: string[]
  categories: IndexerCategory[]
  category_count: number
  items: number
  indexer: IndexerInfo | null
  /** The languages books are fetched in (settings, "Sprachen"). */
  languages?: string[]
}
export type ProwlarrInfo = {
  connected: boolean
  url: string
  key_set: boolean
  auto_add: boolean
  version: string
  last_sync_at: string | null
  last_error: string
  counts: Record<string, number>
  taken: number
  sync_state: ScanState
}
export type ClientKind = 'sabnzbd' | 'nzbget' | 'qbittorrent' | 'transmission' | 'deluge'
export type ClientInfo = {
  id: number
  name: string
  kind: ClientKind
  protocol: 'usenet' | 'torrent'
  url: string
  username: string
  secret_set: boolean
  category: string
  /** False for Transmission: it has no categories, the jobs go to a folder of nexbooks. */
  has_categories: boolean
  priority: number
  enabled: boolean
  version: string
  state: 'ready' | 'unreachable' | 'login' | 'other'
  last_error: string
  checked_at: string | null
}
export type ClientForm = { kind: ClientKind; name?: string; url: string; username: string; secret?: string; category: string; priority?: number; enabled?: boolean }
export type ClientTest = { version: string; category_existed: boolean; has_categories: boolean; category: string; folder: string }
export type PathMapping = { id: number; client_id: number; client: string; remote: string; local: string }
export type RankedFormat = { f: string; on: boolean }
export type FormatsConfig = {
  ebook: RankedFormat[]
  audio: RankedFormat[]
  target: { ebook: string; audio: string }
  languages: string[]
  original: boolean
  variant: 'unabridged' | 'prefer'
  abridged: boolean
  dramatized: boolean
  size: { ebook: number[]; audio: number[] }
  library: { ebook: number | null; audio: number | null }
  schema: string
  /** How many days what an upgrade replaced lies in the trash of its library. */
  trash_days?: number
}
export type FormatsOut = FormatsConfig & {
  libraries: { id: number; name: string; kind: LibraryKind; path: string; writable: boolean; write_problem: string }[]
  known_ebook: string[]
  known_audio: string[]
  known_languages: string[]
  samples: { author: string; title: string; series: string; position: number | null; year: string }[]
}
export type LibraryCheck = {
  writable: boolean
  reason: string
  hardlink: boolean | null
  downloads: { client: string; folder: string; seen: boolean; same_filesystem: boolean | null }[]
}
export type AutomationConfig = { enabled: boolean; rss_minutes: 5 | 15 | 30 | 60; retry_hours: 1 | 3 | 6 | 12 | 24; replace: boolean; replace_max: number; seed: 'ratio' | '7d' | 'no'; retention_days: number; retention_read?: number | null }

/** What the automatic work did, round by round (the operator reads it). */
export type AutoRunIndexer = { id: number; name: string; requests: number; entries: number; error: string }
export type AutoRun = {
  id: number
  kind: 'rss' | 'search' | 'handover'
  started_at: string
  finished_at: string | null
  error: string
  indexers: AutoRunIndexer[]
  wishes: number
  matched: number
  loaded: number
  handovers: number
}
export type AutoIndexer = { id: number; name: string; checked_at: string | null; next_at: string; error: string; resting_until: string | null }
export type AutomationStatus = {
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  due: number
  waiting_for_a_day: number
  wishes_open: number
  indexers: AutoIndexer[]
  day_ends_at: string
  /** The name of the instance's time zone; empty when it has none. */
  zone: string
  runs: AutoRun[]
}

export type ReleaseKind = 'ebook' | 'audio'
export type Reason = { code: string; arg?: unknown }
export type ReleaseOut = {
  id: number
  title: string
  indexer: string
  protocol: 'usenet' | 'torrent'
  kind: ReleaseKind
  /** In the words of the ranking (`epub`, `m4b_aac`, `mp3_192`); empty when the name does not tell. */
  format: string
  format_how: 'name' | 'estimate' | 'unknown'
  tier: number
  language: string
  /** A mark or the words of the name say it; otherwise it is presumed. */
  language_sure: boolean
  language_how: string
  variant: string
  bitrate: number | null
  size: number | null
  age_days: number | null
  seeders: number | null
  peers: number | null
  grabs: number | null
  score: number
  /** The place among those of its kind that pass, 1 first; null when it was turned down. */
  rank: number | null
  reasons: Reason[]
  /** The blocklist entry that turns it down, when one does. */
  blocked_by: number | null
}
export type ReleaseCard = {
  kind: ReleaseKind
  total: number
  passing: number
  reasons: { code: string; count: number }[]
  top: ReleaseOut | null
  goal: string
  below_goal: boolean
  have: string | null
}
export type IndexerProgress = {
  id: number
  name: string
  kind: IndexerKind
  state: 'waiting' | 'searching' | 'done' | 'failed' | 'timeout' | 'skipped' | 'stopped'
  found: number
  error: string
  until: string | null
  /** The operator's limit of requests for the day, when it (or a person's share of it) is what stopped the indexer. */
  limit: number | null
}
/** An indexer that cannot be asked right now, read live: why, until when, and the limit of the day where that is the cause. */
export type RestingIndexer = {
  id: number
  name: string
  cause: 'daily_limit' | 'share_used' | 'request_limit' | 'api_limit' | 'key_rejected' | 'server_error' | 'grab_limit'
  until: string
  limit: number | null
}
export type ReleaseSearch = {
  id: number
  state: 'queued' | 'running' | 'done' | 'failed'
  error: string
  /** The language looked for; empty when every language the operator accepts is taken. */
  language: string
  /** The languages a release may be in. */
  languages: string[]
  kinds: ReleaseKind[]
  work_id: number | null
  book: string | null
  title: string
  author: string
  created_at: string
  finished_at: string | null
  indexers: IndexerProgress[]
  /** The indexers that cannot be asked right now (live, not as of the time of the search). */
  resting: RestingIndexer[]
  /** A new search could ask an indexer now. */
  can_search_again: boolean
  /** The person has made the searches of the day they may; a new one is possible from then. */
  searches_used_until: string | null
  cards: ReleaseCard[]
  releases: ReleaseOut[]
}
export type DownloadOut = { id: number; title: string; kind: ReleaseKind; state: 'sending' | 'unsure' | 'queued' | 'downloading' | 'paused' | 'completed' | 'problem' | 'failed' | 'removed' | 'imported'; client: string; protocol: 'usenet' | 'torrent'; forced: string[]; created_at: string }
export type WorkProcure = { allowed: boolean; kinds: { kind: ReleaseKind; library: string; have: string | null; goal: string; upgrade: boolean }[] }


// --- Wishes and following the downloads ---
export type WishState = 'searching' | 'idle' | 'not_found' | 'waiting' | 'capped' | 'upgrade' | 'loading' | 'done'
export type ProcureBook = { work_id: number | null; book: string | null; title: string; authors: string[]; series: string; series_pos: number | null; genre: string; color: string; cover_url: string | null }
export type LibraryRef = { id: number; name: string }
/** A place a book may go, with whether nexbooks may write there (`missing`, `read_only`, `no_permission` say why not). */
export type TargetRef = LibraryRef & { writable: boolean; write_problem: string }
export type WishDetail = { total?: number; code?: string; limit?: number | null; reasons?: { code: string; count: number }[]; have?: string; goal?: string; indexer?: string; until?: string | null; load_error?: string; error?: string; reason?: string; passing?: number; language?: string; look?: number; have_language?: string; goal_language?: string }
export type WishOut = {
  id: number
  kind: ReleaseKind
  language: string
  state: WishState
  detail: WishDetail | null
  library: LibraryRef | null
  by: string
  mine: boolean
  created_at: string
  searched_at: string | null
  next_try_at: string | null
  release_date: string
  waiting_for_a_place: boolean
  /** A release was found and the download program did not answer: it is handed over again in a few minutes. */
  handing_over_again: boolean
  book: ProcureBook
  /** The author whose watching made this wish; empty when somebody wished it by hand. */
  via?: string
}
export type WishResult = { index: number; kind: ReleaseKind; status: 'created' | 'exists' | 'have' | 'error'; code: string; wish: WishOut | null }
export type ProcureCounts = { allowed: boolean; seeking: number; running: number; needs: number; blocklist: number; automatic: boolean; indexers: number; clients_down: { id: number; name: string; code: string }[] }
export type OptionsKind = { kind: ReleaseKind; state: 'free' | 'have' | 'upgrade' | 'wished' | 'loading'; wish_id: number | null; have: string | null; goal: string; libraries: TargetRef[]; default_library: number | null; takes: string[]; abridged: boolean; choose?: boolean }
export type BookOptions = { allowed: boolean; kinds: OptionsKind[]; original_language: string; languages: string[]; release_date: string; automatic: boolean; indexers: number }
export type RunningPhase = 'sending' | 'unsure' | 'queued' | 'downloading' | 'paused' | 'unpacking' | 'waiting' | 'checking' | 'filing' | 'seeding'
export type RunningOut = {
  id: number
  book: ProcureBook
  kind: ReleaseKind
  language: string
  release: string
  protocol: 'usenet' | 'torrent' | 'upload'
  client: string
  indexer: string
  format: string
  size: number | null
  phase: RunningPhase
  progress: number | null
  remaining_seconds: number | null
  speed: number | null
  stalled: boolean
  seed_ratio: number | null
  goal_ratio: number | null
  seed_seconds: number | null
  goal_seconds: number | null
  library: LibraryRef | null
  by: string
  mine: boolean
  forced: string[]
  created_at: string
}
export type ProblemOut = { id: number; book: ProcureBook; kind: ReleaseKind; release: string; code: string; values: Record<string, unknown>; client: string; library: LibraryRef | null; at: string; can_map: boolean; protocol: string }
/** A note the filing left the caller: theirs alone. */
export type NoteOut = {
  id: number
  code: 'upgrade_position' | 'upgrade_replaced' | 'folder_kept' | 'works_joined' | 'duplicate' | 'kindle_failed'
  book: ProcureBook
  edition_id: number | null
  /** `why`, `other_title`, `here`, `there`, `other_edition_id`, `other_work_id`: of an edition the library holds twice (`duplicate`). `reason`: why a mail to a Kindle did not go (`kindle_failed`). */
  detail: { from?: string; to?: string; fraction?: number; page?: number | null; pages?: number | null; client?: string; title?: string; into?: string; why?: 'same_files' | 'contains' | 'same_format' | 'runtime' | 'size'; other_title?: string; here?: string; there?: string; other_edition_id?: number; other_work_id?: number; reason?: string }
  can_restore: boolean
  release: string
  at: string
}
/** A book of a bundle the check asks about: ``unit`` names it for the answer. */
export type BundleUnit = { unit: string; title: string; authors: string[]; volume: number | null; series: string; format: string; files: number }
export type DecideAction = 'keep' | 'pick' | 'whole' | 'take' | 'block' | 'acknowledge'
export type TrashEntry = { id: number; library: LibraryRef | null; work_id: number | null; kind: ReleaseKind; title: string; author: string; format: string; files: number; size: number; created_at: string; expires_at: string }
export type UploadPhase = 'uploading' | 'waiting' | 'filing' | 'problem' | 'rejected' | 'done' | 'removed'
export type UploadOut = { id: number; state: string; phase: UploadPhase; library_id: number | null; work_id: number | null; kind: ReleaseKind; problem: string; files: { path: string; size: number }[]; limits: { ebook_bytes: number; file_bytes: number; total_bytes: number; files: number }; created_at: string }
export type ForeignJobOut = { id: number; name: string; client: string; state: string; first_seen_at: string }
/** What was wished, as it read when the wish was made (the work may have been renamed since). */
export type WishedBook = { title: string; authors: string[]; kind: ReleaseKind; language: string }
export type HistoryLine = { id: number; book_title: string; book_author: string; work_id: number | null; book: string | null; kind: ReleaseKind; release: string; indexer: string; result: 'done' | 'rejected' | 'failed' | 'removed' | 'joined'; note: Record<string, unknown> | null; created_at: string; wished?: WishedBook | null; filed_as?: string | null; differs?: boolean; can_split?: boolean }
export type MappingOptions = { reported: string | null; search: 'proposed' | 'twice' | 'cut' | 'nowhere' | 'no_path'; proposal: { remote: string; local: string } | null; matches: string[]; choices: { remote: string; local: string; place: string }[]; roots: string[] }
export type BlockLine = { id: number; title: string; kind: ReleaseKind; protocol: 'usenet' | 'torrent'; indexer: string; reason: string; by: string; created_at: string; work_id: number | null; book: string | null; book_title: string; book_author: string }

const PROCURE = '/api/v1/procure'

/** The files of a download that asks a question, as the list of what needs a person shows a book of the library. */
export type DownloadLook = {
  download_id: number
  kind: 'ebook' | 'audio'
  folder: string
  files: FileLine[]
  says: FileSays
  chapters: { number: number; title: string; start: number }[]
}

/** The bytes of one file of a download that asks a question (the reader and the player ask for them with `Range`). */
export function lookContentUrl(downloadId: number, fileId: number): string {
  return `/api/v1/procure/downloads/${downloadId}/files/${fileId}/content`
}

export const procureApi = {
  look: (id: number) => api<DownloadLook>(`/api/v1/procure/downloads/${id}/look`),
  indexers: () => api<{ indexers: IndexerInfo[] }>(`${PROCURE}/indexers`).then((answer) => answer.indexers),
  addIndexer: (form: IndexerForm) => api<IndexerInfo>(`${PROCURE}/indexers`, { method: 'POST', body: form }),
  changeIndexer: (id: number, change: Partial<IndexerForm>) => api<IndexerInfo>(`${PROCURE}/indexers/${id}`, { method: 'PATCH', body: change }),
  takeIndexerProposals: (id: number) => api<IndexerInfo>(`${PROCURE}/indexers/${id}/proposals`, { method: 'POST' }),
  removeIndexer: (id: number) => api<void>(`${PROCURE}/indexers/${id}`, { method: 'DELETE' }),
  /** An address and a key not saved yet; a key left out is the one of the saved indexer `indexer_id`. */
  testAddress: (body: { indexer_id?: number; kind: IndexerKind; url: string; api_key?: string; language?: string }) => api<IndexerTest>(`${PROCURE}/indexers/test`, { method: 'POST', body }),
  testIndexer: (id: number, language?: string) => api<IndexerTest>(`${PROCURE}/indexers/${id}/test`, { method: 'POST', query: { language } }),
  prowlarr: () => api<ProwlarrInfo>(`${PROCURE}/prowlarr`),
  testProwlarr: (body: { url: string; api_key?: string }) => api<{ version: string; indexers: number; with_books: number }>(`${PROCURE}/prowlarr/test`, { method: 'POST', body }),
  saveProwlarr: (body: { url: string; api_key?: string; auto_add: boolean }) => api<ProwlarrInfo>(`${PROCURE}/prowlarr`, { method: 'PUT', body }),
  syncProwlarr: () => api<ProwlarrInfo>(`${PROCURE}/prowlarr/sync`, { method: 'POST' }),
  disconnectProwlarr: () => api<void>(`${PROCURE}/prowlarr`, { method: 'DELETE' }),
  clients: () => api<{ clients: ClientInfo[] }>(`${PROCURE}/clients`).then((answer) => answer.clients),
  addClient: (form: ClientForm) => api<ClientInfo>(`${PROCURE}/clients`, { method: 'POST', body: form }),
  changeClient: (id: number, change: Partial<ClientForm>) => api<ClientInfo>(`${PROCURE}/clients/${id}`, { method: 'PATCH', body: change }),
  removeClient: (id: number) => api<void>(`${PROCURE}/clients/${id}`, { method: 'DELETE' }),
  testClientAddress: (body: { client_id?: number; kind: ClientKind; url: string; username: string; secret?: string; category: string }) => api<ClientTest>(`${PROCURE}/clients/test`, { method: 'POST', body }),
  testClient: (id: number) => api<ClientTest>(`${PROCURE}/clients/${id}/test`, { method: 'POST' }),
  mappings: () => api<{ mappings: PathMapping[] }>(`${PROCURE}/path-mappings`).then((answer) => answer.mappings),
  removeMapping: (id: number) => api<void>(`${PROCURE}/path-mappings/${id}`, { method: 'DELETE' }),
  formats: () => api<FormatsOut>(`${PROCURE}/formats`),
  saveFormats: (config: FormatsConfig) => api<FormatsOut>(`${PROCURE}/formats`, { method: 'PUT', body: config }),
  previewNaming: (schema: string, sample: number) => api<{ ebook: string; audio: string; sample: number }>(`${PROCURE}/formats/preview`, { method: 'POST', body: { schema, sample } }),
  checkLibrary: (library_id: number) => api<LibraryCheck>(`${PROCURE}/formats/check-library`, { method: 'POST', body: { library_id } }),
  automation: () => api<AutomationConfig>(`${PROCURE}/automation`),
  saveAutomation: (config: AutomationConfig) => api<AutomationConfig>(`${PROCURE}/automation`, { method: 'PUT', body: config }),
  /** When the automatic work last did something and comes back next, and what it did (the operator). */
  automationStatus: (limit?: number) => api<AutomationStatus>(`${PROCURE}/automation/status`, { query: { limit } }),
  /** The libraries the caller may ask for books in. */
  targets: () => api<{ targets: { id: number; name: string; kind: LibraryKind; writable: boolean; write_problem: string }[] }>(`${PROCURE}/targets`).then((answer) => answer.targets),
  /** What the caller may look for of a work of a library: the kinds, where they go, an upgrade. */
  work: (workId: number, language?: string) => api<WorkProcure>(`${PROCURE}/works/${workId}`, { query: { language } }),
  /** A search of the indexers for a work of a library or a book of the sources (its token). */
  search: (body: { work_id?: number; book?: string; language?: string; new?: boolean }) => api<ReleaseSearch>(`${PROCURE}/searches`, { method: 'POST', body }),
  readSearch: (id: number) => api<ReleaseSearch>(`${PROCURE}/searches/${id}`),
  load: (searchId: number, releaseId: number, force = false) => api<DownloadOut>(`${PROCURE}/searches/${searchId}/releases/${releaseId}/load`, { method: 'POST', body: { force } }),
  block: (searchId: number, releaseId: number, reason = 'by_hand') => api<{ id: number }>(`${PROCURE}/searches/${searchId}/releases/${releaseId}/block`, { method: 'POST', body: { reason } }),
  unblock: (entryId: number) => api<void>(`${PROCURE}/blocklist/${entryId}`, { method: 'DELETE' }),
  counts: () => api<ProcureCounts>(`${PROCURE}/counts`),
  bookOptions: (which: { work_id?: number; book?: string }) => api<BookOptions>(`${PROCURE}/book-options`, { query: which }),
  wishes: () => api<{ wishes: WishOut[] }>(`${PROCURE}/wishes`).then((answer) => answer.wishes),
  wish: (body: { items: { work_id?: number; book?: string }[]; kinds: ReleaseKind[]; language?: string; library_id?: number }) => api<{ results: WishResult[] }>(`${PROCURE}/wishes`, { method: 'POST', body }),
  removeWish: (id: number) => api<void>(`${PROCURE}/wishes/${id}`, { method: 'DELETE' }),
  searchWish: (id: number) => api<WishOut>(`${PROCURE}/wishes/${id}/search`, { method: 'POST' }),
  /** A wish whose language is no longer allowed looks for another one (`null`: every language allowed). */
  wishLanguage: (id: number, language: string | null) => api<WishOut>(`${PROCURE}/wishes/${id}/language`, { method: 'POST', body: { language } }),
  running: () => api<{ downloads: RunningOut[] }>(`${PROCURE}/running`).then((answer) => answer.downloads),
  problems: () => api<{ problems: ProblemOut[]; foreign: ForeignJobOut[]; notes: NoteOut[] }>(`${PROCURE}/problems`),
  /** An answer to a question of the check after the download, or "read" for the note of a release it turned down. */
  decide: (id: number, action: DecideAction, unit?: string) => api<{ id: number; state: string }>(`${PROCURE}/downloads/${id}/decide`, { method: 'POST', body: unit ? { action, unit } : { action } }),
  acknowledgeNote: (id: number) => api<void>(`${PROCURE}/notes/${id}/acknowledge`, { method: 'POST' }),
  restoreNote: (id: number) => api<void>(`${PROCURE}/notes/${id}/restore`, { method: 'POST' }),
  dropCopy: (id: number, folder: string) => api<void>(`${PROCURE}/notes/${id}/drop`, { method: 'POST', body: { folder } }),
  assignForeign: (id: number, wish_id: number) => api<{ id: number; state: string }>(`${PROCURE}/foreign/${id}/assign`, { method: 'POST', body: { wish_id } }),
  trash: () => api<{ entries: TrashEntry[]; days: number; size: number }>(`${PROCURE}/trash`),
  restoreTrash: (id: number) => api<void>(`${PROCURE}/trash/${id}/restore`, { method: 'POST' }),
  deleteTrash: (id: number) => api<void>(`${PROCURE}/trash/${id}`, { method: 'DELETE' }),
  emptyTrash: () => api<{ deleted: number }>(`${PROCURE}/trash`, { method: 'DELETE' }),
  history: (result?: 'done' | 'bad') => api<{ entries: HistoryLine[] }>(`${PROCURE}/history`, { query: { result } }).then((answer) => answer.entries),
  blocklist: () => api<{ entries: BlockLine[] }>(`${PROCURE}/blocklist`).then((answer) => answer.entries),
  cancel: (id: number, block: boolean) => api<{ id: number; state: string }>(`${PROCURE}/downloads/${id}/cancel`, { method: 'POST', body: { block } }),
  recheck: (id: number) => api<{ id: number; state: string }>(`${PROCURE}/downloads/${id}/recheck`, { method: 'POST' }),
  chooseLibrary: (id: number, library_id: number) => api<{ id: number; state: string }>(`${PROCURE}/downloads/${id}/library`, { method: 'POST', body: { library_id } }),
  stopSeeding: (id: number) => api<{ id: number; state: string }>(`${PROCURE}/downloads/${id}/stop-seeding`, { method: 'POST' }),
  mappingOptions: (id: number) => api<MappingOptions>(`${PROCURE}/downloads/${id}/mapping`),
  confirmMapping: (id: number, pair: { remote: string; local: string }) => api<{ id: number; client: string; remote: string; local: string }>(`${PROCURE}/downloads/${id}/mapping`, { method: 'POST', body: pair }),
  ignoreForeign: (id: number) => api<void>(`${PROCURE}/foreign/${id}/ignore`, { method: 'POST' }),
  removeForeign: (id: number) => api<void>(`${PROCURE}/foreign/${id}`, { method: 'DELETE' }),
}

/** Uploading books from the browser: the same way into the library as a download. */
export const uploadsApi = {
  begin: (body: { library_id: number; work_id?: number; kind?: ReleaseKind }) => api<UploadOut>('/api/v1/uploads', { method: 'POST', body }),
  read: (id: number) => api<UploadOut>(`/api/v1/uploads/${id}`),
  finish: (id: number) => api<UploadOut>(`/api/v1/uploads/${id}/finish`, { method: 'POST', body: {} }),
  cancel: (id: number) => api<void>(`/api/v1/uploads/${id}`, { method: 'DELETE' }),
  /** One file, with the share of it sent so far (``fetch`` cannot tell, so this is an ``XMLHttpRequest``). */
  send: (id: number, path: string, file: Blob, onProgress: (sent: number) => void, signal?: AbortSignal) =>
    new Promise<{ path: string; size: number }>((resolve, reject) => {
      const request = new XMLHttpRequest()
      const url = new URL(`/api/v1/uploads/${id}/files`, window.location.origin)
      url.searchParams.set('path', path)
      request.open('PUT', url.pathname + url.search)
      request.setRequestHeader('Accept', 'application/json')
      request.setRequestHeader('Content-Type', 'application/octet-stream')
      request.setRequestHeader('X-Nexbooks-Client', tabId())
      request.upload.onprogress = (event) => onProgress(event.loaded)
      request.onload = () => {
        const data = readJson(request.responseText) as { detail?: { code?: string } & Record<string, unknown> } | null
        if (request.status >= 200 && request.status < 300) resolve(data as unknown as { path: string; size: number })
        else {
          const { code, ...values } = data?.detail ?? {}
          reject(new ApiError(request.status, typeof code === 'string' ? code : 'unknown', values))
        }
      }
      request.onerror = () => reject(new ApiError(0, 'network'))
      request.onabort = () => reject(new ApiError(0, 'cancelled'))
      if (signal) {
        if (signal.aborted) {
          reject(new ApiError(0, 'cancelled'))
          return
        }
        signal.addEventListener('abort', () => request.abort(), { once: true })
      }
      request.send(file)
    }),
}

function readJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}
