/**
 * What the pictures of books share, taken over from the design: which text colour a cover colour
 * wears, the pattern a drawn cover has, and the words of the chips.
 */
import type { CSSProperties } from 'react'

import type { EditionBrief, WorkBrief } from '../api/client'

/** The brightness of `#rrggbb` as 0..1. */
export function luminance(hex: string): number {
  const value = parseInt(hex.slice(1), 16)
  if (Number.isNaN(value)) return 0
  return (0.2126 * (value >> 16) + 0.7152 * ((value >> 8) & 255) + 0.0722 * (value & 255)) / 255
}

/** Dark text on a light cover, light text on a dark one (`lum > 0.45`). */
export function inkFor(hex: string): string {
  return luminance(hex) > 0.45 ? '#2a2420' : '#efe6d2'
}

/** The five patterns a drawn cover can have, by the number the work carries. */
export function patternFor(color: string, pattern: number): CSSProperties {
  const accent = luminance(color) > 0.45 ? 'rgba(0,0,0,.13)' : 'rgba(255,255,255,.12)'
  switch (pattern % 5) {
    case 0:
      return { boxShadow: `inset 0 0 0 7px ${color}, inset 0 0 0 9px ${accent}, inset 0 0 0 13px ${color}, inset 0 0 0 14px ${accent}, 0 12px 24px rgba(0,0,0,.22)` }
    case 1:
      return { backgroundImage: `radial-gradient(circle at 50% 66%, ${accent} 0 30%, transparent 30.5%)` }
    case 2:
      return { backgroundImage: `repeating-linear-gradient(60deg, transparent 0 9px, ${accent} 9px 11px)` }
    case 3:
      return { backgroundImage: `repeating-linear-gradient(180deg, transparent 0 55%, ${accent} 55% 57.5%, transparent 57.5% 62%)` }
    default:
      return { backgroundImage: `radial-gradient(${accent} 1.2px, transparent 1.6px)`, backgroundSize: '12px 12px', backgroundPosition: '0 60%' }
  }
}

/** The name of a work's first author as the cover shows it. */
export function authorLine(work: Pick<WorkBrief, 'authors'>): string {
  return work.authors.map((person) => person.name).join(', ')
}

/** The formats of an e-book edition as one chip: `EPUB · AZW3 · MOBI`. */
export function formatsLabel(edition: Pick<EditionBrief, 'formats'>): string {
  return edition.formats.map((format) => format.toUpperCase()).join(' · ')
}

/** The two letters of a language for a chip (`DE`); empty when none is known. */
export function languageChip(language: string): string {
  return language ? language.slice(0, 2).toUpperCase() : ''
}

/** A figure with the thousands separators of a language. */
export function figure(value: number, language: string): string {
  return value.toLocaleString(language)
}

/** The name of a language in the language of the page ("Deutsch", "English"); the code in capitals when it has none. */
export function languageName(code: string, inLanguage: string): string {
  if (!code) return ''
  try {
    return new Intl.DisplayNames([inLanguage], { type: 'language' }).of(code) ?? code.toUpperCase()
  } catch {
    return code.toUpperCase()
  }
}

/** A running time as the design writes it: "9 Std 30 Min", "45 Min" (the words come from the language files). */
export function durationParts(seconds: number): { h: number; m: number } {
  const minutes = Math.round(seconds / 60)
  return { h: Math.floor(minutes / 60), m: minutes % 60 }
}

/** The pattern (0 to 4) of the drawn cover of a book that is not on a shelf: fixed by its token, so that it looks the same
 * every time (the books of a library carry theirs from the server). */
export function patternOfToken(token: string): number {
  let hash = 0
  for (let i = 0; i < token.length; i++) hash = (hash * 31 + token.charCodeAt(i)) >>> 0
  return hash % 5
}
