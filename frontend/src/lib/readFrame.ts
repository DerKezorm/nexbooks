/**
 * What the reader page and the frames that draw a book say to each other.
 *
 * A book is made by somebody else: HTML, CSS and pictures that nexbooks does not control. It is drawn in a frame of its
 * own (`/readview.html` for EPUB, MOBI and AZW3, `/pdfview.html` for PDF) with **no network at all** (its policy says so,
 * `backend/app/main.py`). The PDF frame has no origin either; the frame of a book has the origin of nexbooks (the reader
 * reaches into the pages of the book, which a browser allows only inside one origin) and keeps the book from running code
 * by other walls (`readview/sanitize.ts`, `readview/selftest.ts`). The page fetches the file
 * with the person's session and hands the bytes over; the frame hands back where the reader is, the contents and what
 * a search found. Both frames speak this one language, so the page needs one controller for both.
 *
 * Messages count only from the page that holds the frame (`fromParent`), and the page listens only to its own frame.
 */

/** The colours of the page of the book. `auto` follows the app (light by day, dark by night); the frame gets a resolved one. */
export type PageTheme = 'light' | 'sepia' | 'dark'
export type ReaderTheme = 'auto' | PageTheme
export type ReaderFont = 'fraunces' | 'inter' | 'publisher'
export type ReaderMargin = 'narrow' | 'normal' | 'wide'
export type ReaderFlow = 'paged' | 'scroll'

/** How a book is shown, as the person set it (the profile keys `reader_*`) with `theme` already resolved. */
export type ReaderLook = {
  font: ReaderFont
  /** Percent of the size the book sets itself. */
  size: number
  /** Hundredths: 150 is a line height of 1.5. */
  line: number
  margin: ReaderMargin
  theme: PageTheme
  justify: boolean
  flow: ReaderFlow
  /** The accent of the app (for links in the book and the marks of a search). */
  accent: string
  /** Whether the pages turn with a motion (false with reduced motion). */
  animate: boolean
}

/** The formats a frame shows. */
export type BookFormat = 'epub' | 'azw3' | 'mobi' | 'pdf'

/** One line of the contents. `id` is its place in the list (the frame knows how to go there). */
export type TocLine = { id: number; label: string; depth: number }

/** A hit of the search in a book, with the words around it. */
export type SearchHit = { cfi: string; pre: string; match: string; post: string }

/** Where the reader is, as the frame tells it. */
export type Place = {
  /** A CFI for an EPUB, MOBI or AZW3; `page:12` for a PDF; `time:8040.500` for a recording. */
  locator: string
  /** 0 to 1 in the whole book. */
  fraction: number
  /** The line of the contents the reader is under (its `id`), or -1. */
  tocId: number
  /** What the unit of `chapter` is; `time` for a recording (its `chapter` is still the chapter). */
  unit: 'chapter' | 'page' | 'time'
  chapter: number
  chapterCount: number
  chapterTitle: string
}

/** Why a book could not be shown. */
export type FailReason = 'protected' | 'broken' | 'too_large' | 'too_many_entries' | 'zip_bomb' | 'password' | 'unsupported' | 'unsafe'

export type ToFrame =
  | { type: 'open'; data: ArrayBuffer; format: BookFormat; look: ReaderLook; locator: string | null; fraction: number | null }
  | { type: 'look'; look: ReaderLook }
  | { type: 'turn'; direction: 'next' | 'prev' }
  | { type: 'goto'; locator: string }
  | { type: 'goto-fraction'; fraction: number }
  | { type: 'goto-toc'; id: number }
  | { type: 'search'; query: string }
  | { type: 'search-step'; direction: 'next' | 'prev' }
  | { type: 'search-clear' }
  | { type: 'data'; id: number; bytes: ArrayBuffer | null }

export type FromFrame =
  | { type: 'ready' }
  | {
      type: 'opened'
      title: string
      toc: TocLine[]
      /** Where each chapter starts, 0 to 1 (for the strokes on the bar). */
      marks: number[]
      /** The book keeps its own layout (pictures of pages): the typeface and the colours do not apply. */
      fixed: boolean
      /** Right to left, or vertical text: the pages turn the other way. */
      rtl: boolean
      pages: number
    }
  | ({ type: 'relocate'; reason?: string } & Place)
  | { type: 'tap'; zone: 'center' }
  | { type: 'key'; key: string; shift: boolean; ctrl: boolean; alt: boolean; meta: boolean }
  | { type: 'link'; href: string }
  | { type: 'search-hits'; label: string; hits: SearchHit[] }
  | { type: 'search-progress'; fraction: number }
  | { type: 'search-done'; total: number; more: boolean }
  | { type: 'search-count'; current: number; total: number }
  | { type: 'data'; id: number; kind: DataKind; name: string }
  | { type: 'failed'; reason: FailReason }
  /** A page of the book had to be changed to be shown: nested too deeply (`flattened`) or too big (`cut`); or a part the book names is not in the file (`missing`). */
  | { type: 'notice'; kind: 'flattened' | 'cut' | 'missing' }

/** The kinds of file pdf.js may ask for, and the folder they lie in under `/pdfjs/`. */
export type DataKind = 'cMapUrl' | 'standardFontDataUrl' | 'wasmUrl'
export const DATA_FOLDERS: Record<DataKind, string> = { cMapUrl: 'cmaps', standardFontDataUrl: 'standard_fonts', wasmUrl: 'wasm' }

/** A file name pdf.js may ask for: no path, nothing but letters, digits, dots, dashes and underscores. */
export function plainName(name: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$/.test(name) && !name.includes('..')
}

/** A message the frame listens to: only from the page that holds it (the frame's parent), never from another window (a link
 * the book opened, another frame), whatever it says. */
export function fromParent(event: Pick<MessageEvent, 'source'>, parent: Window | null): boolean {
  return parent !== null && event.source === parent
}

/** Only what the web is: an address a click in a book may open, in a tab of its own. */
export function openable(href: string): boolean {
  return /^(https?:\/\/|mailto:)/i.test(href)
}

/** The address a click in a book would open, written the way the browser reads it (so a name in foreign letters shows as
 * what it really is), or null when it is none of the web, not an address at all or too long to show whole. */
export function addressOf(href: string): string | null {
  if (!openable(href) || href.length > 2000) return null
  try {
    return new URL(href).href
  } catch {
    return null
  }
}

/** The formats of an edition's files in the order the reader prefers: EPUB, then AZW3 and MOBI (read by the same
 * reader), then PDF. */
export const READ_ORDER: BookFormat[] = ['epub', 'azw3', 'mobi', 'pdf']

/** The frame for a format. */
export function frameFor(format: BookFormat): string {
  return format === 'pdf' ? '/pdfview.html' : '/readview.html'
}

/** A file of an edition as far as the reader needs to know it. */
export type ReadableFile = { id: number; format: string; protected: boolean; size: number }

/** What to open: the best file of the edition the reader can show, or why there is none. `wanted` is a format the person
 * asked for (the address says `?format=pdf`); it wins when it is there and open. */
export function chooseFile<T extends ReadableFile>(files: T[], wanted?: string | null): { file: T } | { none: 'nothing' | 'protected' } {
  const known = files.filter((file) => (READ_ORDER as string[]).includes(file.format))
  if (known.length === 0) return { none: 'nothing' }
  const open = known.filter((file) => !file.protected)
  if (open.length === 0) return { none: 'protected' }
  const asked = wanted ? open.find((file) => file.format === wanted) : undefined
  if (asked) return { file: asked }
  const order = (file: T) => READ_ORDER.indexOf(file.format as BookFormat)
  return { file: [...open].sort((a, b) => order(a) - order(b) || a.id - b.id)[0] }
}

/**
 * How big a book the browser reads. Measured in Chromium (a book of heavy pictures, every picture on one page): the tab needs
 * about twice the size of the file on top of the 145 MB it needs without a book (50 MB file: 252 MB in all, 100 MB: 349 MB,
 * 200 MB: 542 MB, 300 MB: 646 MB). A phone or a tablet gives a tab a good deal less than a desktop does, and a tab that is
 * over its share is closed by the system without a word, so the limit is the size at which a tab still stays within about
 * 350 MB. A larger book is named as too large for the browser and offered for download.
 */
export const MAX_BOOK_MB = 100
export const MAX_BOOK_BYTES = MAX_BOOK_MB * 1024 * 1024

/** The look as the person set it (the profile) and the colours of the app, for the frame. */
export type ProfileLook = {
  reader_font?: ReaderFont
  reader_size?: number
  reader_line?: number
  reader_margin?: ReaderMargin
  reader_theme?: ReaderTheme
  reader_justify?: boolean
  reader_flow?: ReaderFlow
}

export const DEFAULT_LOOK: Omit<ReaderLook, 'accent' | 'animate'> = {
  font: 'fraunces',
  size: 100,
  line: 150,
  margin: 'normal',
  theme: 'light',
  justify: false,
  flow: 'paged',
}

/** The look from the profile. `appIsDark`: the app shows its dark colours now; the look `auto` follows it. */
export function lookOf(profile: ProfileLook | undefined, appIsDark: boolean, accent: string, animate: boolean): ReaderLook {
  const theme = profile?.reader_theme ?? 'auto'
  return {
    font: profile?.reader_font ?? DEFAULT_LOOK.font,
    size: profile?.reader_size ?? DEFAULT_LOOK.size,
    line: profile?.reader_line ?? DEFAULT_LOOK.line,
    margin: profile?.reader_margin ?? DEFAULT_LOOK.margin,
    theme: theme === 'auto' ? (appIsDark ? 'dark' : 'light') : theme,
    justify: profile?.reader_justify ?? DEFAULT_LOOK.justify,
    flow: profile?.reader_flow ?? DEFAULT_LOOK.flow,
    accent,
    animate,
  }
}

/** The ground and the ink of a page, per theme. The page of the app uses the same for its bars. */
export const PAGE_COLOURS: Record<PageTheme, { ground: string; ink: string; muted: string; line: string }> = {
  light: { ground: '#fffdf8', ink: '#2a241d', muted: '#75695a', line: 'rgba(60, 45, 25, 0.13)' },
  sepia: { ground: '#f4ecd8', ink: '#433422', muted: '#7a6850', line: 'rgba(67, 52, 34, 0.18)' },
  dark: { ground: '#1b1714', ink: '#efe7d7', muted: '#b0a38c', line: 'rgba(233, 214, 178, 0.13)' },
}

/** The sizes the reader offers, in percent, in the order of the profile. */
export const SIZES = [80, 90, 100, 110, 120, 135, 150, 175, 200]
/** The line heights the reader offers, in hundredths. */
export const LINES = [120, 135, 150, 165, 180, 200]

/** The next size in a direction, or the same one at the end of the list. */
export function stepSize(current: number, direction: 1 | -1): number {
  const at = SIZES.indexOf(current)
  const from = at === -1 ? SIZES.indexOf(100) : at
  return SIZES[Math.min(SIZES.length - 1, Math.max(0, from + direction))]
}

/** A short name for this device in the list of devices ("Firefox on Linux"), from what the browser says about itself.
 * An iPad in the desktop view of Safari calls itself a Mac ("Macintosh", platform "MacIntel"); a Mac has no touch screen
 * and an iPad has several touch points, which is how the two are told apart. */
export function deviceName(agent: string, platform = '', touchPoints = 0): string {
  const text = agent || ''
  const system = /iPad/.test(text) || (/Macintosh/.test(text) && (/Mobile/.test(text) || touchPoints > 1)) ? 'iPadOS'
    : /iPhone|iPod/.test(text) ? 'iOS'
    : /Android/.test(text) ? 'Android'
    : /Windows/.test(text) ? 'Windows'
    : /Mac OS X|Macintosh/.test(text) ? (platform.toLowerCase().includes('iphone') ? 'iOS' : 'macOS')
    : /CrOS/.test(text) ? 'ChromeOS'
    : /Linux|X11/.test(text) ? 'Linux'
    : ''
  const browser = /Edg\//.test(text) ? 'Edge'
    : /OPR\/|Opera/.test(text) ? 'Opera'
    : /Firefox|FxiOS/.test(text) ? 'Firefox'
    : /Chrome|CriOS/.test(text) ? 'Chrome'
    : /Safari/.test(text) ? 'Safari'
    : ''
  return [browser, system].filter(Boolean).join(' · ').slice(0, 80) || 'Browser'
}

/** A key a device makes up for itself, the same on every report (it tells "this device" from "another"). */
export function newDeviceKey(random: () => number = Math.random): string {
  const letters = 'abcdefghijklmnopqrstuvwxyz0123456789'
  let key = 'bw-'
  for (let i = 0; i < 16; i++) key += letters[Math.floor(random() * letters.length)]
  return key
}

/** Whether a place that is not ours is further along than ours, enough to tell the person (a hair's breadth is none). */
export function furtherAlong(theirs: number, ours: number): boolean {
  return theirs - ours > 0.004
}

/** Whether the browser is WebKit's (Safari, and every browser on an iPhone or iPad). There a frame without `allow-scripts` does
 * not pass events to the reader that holds it (WebKit bug 218086), so the pages of a book keep that flag and the policy of
 * each page forbids scripts instead. Elsewhere the flag is left off and the browser itself runs no script in a page. */
export function isWebKit(agent: string, platform: string, touchPoints: number): boolean {
  const phone = /iPhone|iPad|iPod/.test(agent) || (platform === 'MacIntel' && touchPoints > 1)
  const safari = /Safari\//.test(agent) && !/Chrome\/|Chromium\/|Edg\/|OPR\/|Firefox\//.test(agent)
  return phone || safari
}
