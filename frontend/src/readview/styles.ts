/**
 * How the pages of a book look, as the person set it: the typeface, the size, the line height, the margins, the colours and
 * whether the text is justified. Written as two stylesheets that the reader puts into every page of the book, one before
 * the book's own styles and one after them (the choices of the person win over the book's own).
 *
 * The typefaces of the reader (Fraunces and Inter) travel in the app's own files; the pages ask for them by their full
 * address, because a page of a book has no address of its own to resolve a short one against. The policy of the page
 * lets exactly this host, and nothing else, give it fonts.
 */
import fraunces from '@fontsource-variable/fraunces/files/fraunces-latin-opsz-normal.woff2?url'
import frauncesExt from '@fontsource-variable/fraunces/files/fraunces-latin-ext-opsz-normal.woff2?url'
import frauncesItalic from '@fontsource-variable/fraunces/files/fraunces-latin-opsz-italic.woff2?url'
import frauncesItalicExt from '@fontsource-variable/fraunces/files/fraunces-latin-ext-opsz-italic.woff2?url'
import inter from '@fontsource-variable/inter/files/inter-latin-wght-normal.woff2?url'
import interExt from '@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2?url'
import interCyrillic from '@fontsource-variable/inter/files/inter-cyrillic-wght-normal.woff2?url'
import interGreek from '@fontsource-variable/inter/files/inter-greek-wght-normal.woff2?url'
import interItalic from '@fontsource-variable/inter/files/inter-latin-wght-italic.woff2?url'
import interItalicExt from '@fontsource-variable/inter/files/inter-latin-ext-wght-italic.woff2?url'

import { PAGE_COLOURS, type ReaderLook } from '../lib/readFrame'

const LATIN = 'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD'
const LATIN_EXT = 'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF'
const CYRILLIC = 'U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116'
const GREEK = 'U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF'

function face(family: string, url: string, range: string, weights: string, style = 'normal'): string {
  const absolute = new URL(url, document.baseURI).href
  return `@font-face{font-family:'${family}';font-style:${style};font-display:swap;font-weight:${weights};src:url("${absolute}") format('woff2');unicode-range:${range}}`
}

/** The @font-face rules of the two typefaces of the reader. */
export function fontFaces(): string {
  return [
    face('Fraunces Reader', fraunces, LATIN, '100 900'),
    face('Fraunces Reader', frauncesExt, LATIN_EXT, '100 900'),
    face('Fraunces Reader', frauncesItalic, LATIN, '100 900', 'italic'),
    face('Fraunces Reader', frauncesItalicExt, LATIN_EXT, '100 900', 'italic'),
    face('Inter Reader', inter, LATIN, '100 900'),
    face('Inter Reader', interExt, LATIN_EXT, '100 900'),
    face('Inter Reader', interCyrillic, CYRILLIC, '100 900'),
    face('Inter Reader', interGreek, GREEK, '100 900'),
    face('Inter Reader', interItalic, LATIN, '100 900', 'italic'),
    face('Inter Reader', interItalicExt, LATIN_EXT, '100 900', 'italic'),
  ].join('\n')
}

const FAMILY = {
  fraunces: "'Fraunces Reader', Georgia, 'Times New Roman', serif",
  inter: "'Inter Reader', system-ui, -apple-system, 'Segoe UI', sans-serif",
}

/** The sizes between which the page of a book sets its text apart from the ground of the frame. */
export const MARGINS = {
  narrow: { gap: '3%', inline: '920px' },
  normal: { gap: '6%', inline: '720px' },
  wide: { gap: '11%', inline: '600px' },
} as const

/** The stylesheets for the pages: before the book's own, and after. */
export function pageStyles(look: ReaderLook): [string, string] {
  const colours = PAGE_COLOURS[look.theme]
  const family = look.font === 'publisher' ? '' : FAMILY[look.font]
  const before = `${fontFaces()}\nhtml{color-scheme:${look.theme === 'dark' ? 'dark' : 'light'}}`
  const rules: string[] = []
  rules.push(`html{background:${colours.ground} !important;color:${colours.ink} !important}`)
  rules.push(`body{background:transparent !important;color:${colours.ink} !important}`)
  if (look.theme !== 'light') {
    // Dark and sepia pages: the book's own colours would put dark ink on dark ground.
    rules.push(`body *:not(a):not(svg):not(svg *):not(img){color:inherit !important;background-color:transparent !important;border-color:${colours.line} !important}`)
    rules.push(`a:link,a:visited,a *{color:${look.accent} !important}`)
  } else {
    rules.push(`a:link,a:visited{color:${look.accent}}`)
  }
  if (look.theme === 'dark') rules.push('img,svg image{filter:brightness(.88) contrast(1.05)}')
  rules.push(`::selection{background:color-mix(in srgb,${look.accent} 35%,transparent)}`)
  if (look.size !== 100) rules.push(`html{font-size:${look.size}% !important}`)
  if (family) {
    rules.push(`html,body,body *:not(pre):not(code):not(kbd):not(samp):not(tt):not(svg *){font-family:${family} !important}`)
  }
  rules.push(`p,li,blockquote,dd,dt,td,th,div.text{line-height:${look.line / 100} !important}`)
  if (look.justify) {
    rules.push('p,li,blockquote,dd{text-align:justify !important;-webkit-hyphens:auto;hyphens:auto}')
    rules.push('[align="left"]{text-align:left !important}[align="right"]{text-align:right !important}[align="center"]{text-align:center !important}')
  }
  rules.push('pre{white-space:pre-wrap !important}')
  rules.push('img,svg,video{max-width:100%}')
  return [before, rules.join('\n')]
}
