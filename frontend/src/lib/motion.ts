/** Whether the person asked the system for less motion: then nothing turns, flies or glows on the shelf. */
export function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** The two steps of the turn from a spine, and the growing of a cover from the grid (milliseconds and curves of the design). */
export const TURN = {
  out: { ms: 360, easing: 'cubic-bezier(.5,0,.75,0)' },
  in: { ms: 420, easing: 'cubic-bezier(.2,.7,.3,1)' },
  grow: { ms: 520, easing: 'cubic-bezier(.2,.8,.2,1)' },
}

/** What a turn starts from: a spine itself; from a card of the grid the cover grows, not the whole card with its title and
 * chips (the design: `item.querySelector('.cover')`). */
export function originOf(element: HTMLElement): HTMLElement {
  return element.classList.contains('spine') ? element : (element.querySelector<HTMLElement>('.cover') ?? element)
}
