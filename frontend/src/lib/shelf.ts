/** What the shelf and its page share that is no component: the genres in the order of the shelf, and the tip over a spine. */
import { useCallback, useState, type MouseEvent } from 'react'

import type { WorkBrief } from '../api/client'

/** The genres of the shelf, in the order of the shelf; a book with none stands last. */
export const GENRES = ['roman', 'krimi', 'fantasy', 'scifi', 'klassiker', 'sachbuch', 'kinder', 'lyrik', 'none'] as const
export type GenreKey = (typeof GENRES)[number]

export function genreKey(genre: string): GenreKey {
  return (GENRES as readonly string[]).includes(genre) ? (genre as GenreKey) : 'none'
}

export type Tip = { work: WorkBrief; x: number; y: number }

/** The tip over a spine: title, author and series, the chips of its editions; it follows the mouse. Put `handlers` on the
 * element that holds the spines. */
export function useSpineTip(works: WorkBrief[]) {
  const [tip, setTip] = useState<Tip | null>(null)
  const find = useCallback(
    (target: EventTarget | null) => {
      const element = (target as HTMLElement | null)?.closest?.('.spine, .lying')
      const id = element?.getAttribute('data-work')
      return id ? (works.find((work) => String(work.id) === id) ?? null) : null
    },
    [works],
  )
  const place = (event: MouseEvent) => ({ x: Math.min(window.innerWidth - 300, event.clientX + 14), y: event.clientY + 16 })
  const handlers = {
    onMouseOver: (event: MouseEvent) => {
      const work = find(event.target)
      setTip(work ? { work, ...place(event) } : null)
    },
    onMouseMove: (event: MouseEvent) => setTip((current) => (current ? { ...current, ...place(event) } : current)),
    onMouseLeave: () => setTip(null),
  }
  return { tip, handlers, hide: () => setTip(null) }
}

/** About how high a section of `count` spines will be, for the browser to reserve until the section has been drawn (a
 * section far down is not drawn before; with a guess near the truth the scroll bar does not jump when it is). The mean
 * spine with its gap is 34 px wide; a row is the height of a row of the shelf and its board; the heading is 40 px. */
export function sectionHeight(count: number, width: number): number {
  const phone = width <= 820
  const room = Math.max(160, Math.min(width, 1500) - (phone ? 52 : 108) - 44)
  const rows = Math.max(1, Math.ceil((count * 34) / room))
  return 40 + 18 + rows * (phone ? 198 : 254)
}
