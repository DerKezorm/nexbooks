/**
 * What the pages of Browse share: the line under a cover on a shelf, a running time in words, and the numbers that cut the
 * time a cover is held before it is taken in the hand.
 */
import type { TFunction } from 'i18next'

import type { Department, ShelfCard, ShelfSection } from '../api/client'
import { dayText } from './discover'

export const DEPARTMENTS: Department[] = ['bel', 'sach', 'kj']
/** The sections of a strip in the order of the tabs. */
export const SECTIONS: ShelfSection[] = ['new', 'popular', 'award', 'classic']

/** The mouse rests on a cover this long (milliseconds) before it is taken in the hand; at once when another is in the hand. */
export const HOLD_MS = 320
export const HOLD_NEXT_MS = 60
/** The hand stays this long after the mouse left the cover and the hand. */
export const LET_GO_MS = 220

/** 1263 minutes as "21 Std 3 Min" (the words come from the language files). */
export function minutesText(minutes: number, t: TFunction): string {
  return t('browse.personal.hours', {
    hours: Math.floor(minutes / 60),
    minutes: minutes % 60,
  })
}

/** The small line under the title of a card: the day it came out in the section "new", the prize in the section "award". */
export function cardMeta(card: Pick<ShelfCard, 'released' | 'year' | 'award' | 'pages'>, kind: 'new' | 'award' | 'pages' | '', t: TFunction, language: string): string {
  if (kind === 'new') return card.released ? t('discover.when.new', { date: dayText(card.released, language) }) : card.year ? String(card.year) : ''
  if (kind === 'award') return card.award
  if (kind === 'pages') return card.pages ? t('browse.personal.pages', { count: card.pages }) : ''
  return ''
}

/** Whether the screen has no mouse that can rest on a cover: then the first tap takes the cover in the hand. */
export function touchOnly(): boolean {
  try {
    return window.matchMedia('(hover: none)').matches
  } catch {
    return false
  }
}
