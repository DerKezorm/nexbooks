/**
 * The spine of a book on the shelf, as the design makes it: how wide and
 * how high, which colours, how big the letters, and whether it leans. Pure functions of what the server says about a work
 * (`WorkBrief`), so that a test can hold them against the numbers of the design.
 */
import type { WorkBrief } from '../api/client'
import { luminance } from './books'

/** The width of a spine is the pages over this, within the bounds below. */
const PAGES_PER_PX = 12
const MIN_WIDTH = 18
const MAX_WIDTH = 54
/** A recording without a book is a case of this size. */
export const AUDIO_CASE = { t: 26, h: 166 }

/** The width `t` and the height `h` of a spine in pixels. A recording alone is a case; a book is as wide as its pages say
 * and as high as its (fixed) `tall` says, a child's book a little lower. */
export function spineDims(work: Pick<WorkBrief, 'audio_only' | 'pages' | 'tall' | 'genre'>): { t: number; h: number } {
  if (work.audio_only) return { ...AUDIO_CASE }
  return {
    t: Math.round(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, work.pages / PAGES_PER_PX))),
    h: Math.round(158 + work.tall * 58 - (work.genre === 'kinder' ? 14 : 0)),
  }
}

/** The last word of a name: what the foot of the spine says. */
export function lastName(author: string): string {
  const words = author.split(' ')
  return words[words.length - 1] ?? ''
}

/** The size of the letters of the title in pixels: smaller for a longer title, between 10.5 and 15; a crime novel's
 * spine has a wider band at the top and so less room. */
export function titleSize(work: Pick<WorkBrief, 'title' | 'genre'>, height: number): number {
  const room = height - (work.genre === 'krimi' ? 110 : 78)
  return Math.max(10.5, Math.min(15, room / (work.title.length * 0.52)))
}

/** The colours of a spine: the ink on the book's colour, the gilt of a classic's lines and the band of a crime novel. */
export function spineColors(work: Pick<WorkBrief, 'color' | 'id'>): { ink: string; gold: string; band: string } {
  const light = luminance(work.color) > 0.45
  return {
    ink: light ? '#2a2420' : '#efe6d2',
    gold: light ? '#6e5120' : '#e3c57f',
    band: ['#c9993f', '#b5463c', '#d8cba9', '#5b7c9c'][work.id % 4]!,
  }
}

/** One book in 23 leans on its neighbour. */
export function leans(work: Pick<WorkBrief, 'id'>): boolean {
  return work.id % 23 === 7
}

/** The class of a spine for its genre (`s-krimi`); a book with no genre has the plain look. */
export function genreClass(genre: string): string {
  return genre ? `s-${genre}` : 's-none'
}

/** The first author's last name for the foot of the spine; empty when nobody is named. */
export function footName(work: Pick<WorkBrief, 'authors'>): string {
  return work.authors[0] ? lastName(work.authors[0].name) : ''
}
