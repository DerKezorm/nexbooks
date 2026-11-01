/**
 * The turn from a spine (or the growing from a cover) into the page of a book: the same
 * for the page of a book of a library (`WorkPage`) and for one that is not on a shelf (`ForeignPage`). It starts once, when
 * the page is first drawn with the book it was opened from; a page opened by a link (or reloaded) just appears.
 *
 * From a spine the book turns to its cover in two steps (360 ms, then 420 ms), from a cover in a grid the cover grows to its
 * place (520 ms); with `prefers-reduced-motion` nothing moves.
 */
import { useLayoutEffect, type RefObject } from 'react'

import { originOf, reducedMotion, TURN } from './motion'

export type TurnParts = {
  /** The box the page puts the cover in (the turn ends on the `.cover` inside it). */
  cover: RefObject<HTMLDivElement | null>
  /** The layer the turn is drawn on, and the two things that fly in it. */
  flip: RefObject<HTMLDivElement | null>
  spineHost: RefObject<HTMLDivElement | null>
  coverHost: RefObject<HTMLDivElement | null>
}

/**
 * `ready`: the book is known enough to draw (the turn starts when it becomes true). `from`: a selector for the element the
 * book was opened from, or null when it was not opened from a page. `shown(true)` says the page may fade in.
 */
export function useDetailTurn(ready: boolean, from: string | null, parts: TurnParts, shown: (value: boolean) => void): void {
  useLayoutEffect(() => {
    if (!ready) return
    return startTurn(
      {
        target: parts.cover.current?.querySelector<HTMLElement>('.cover') ?? null,
        fromElement: from ? document.querySelector<HTMLElement>(from) : null,
        fl: parts.flip.current,
        fs: parts.spineHost.current,
        fc: parts.coverHost.current,
      },
      shown,
    )
    // Once for the book of this page: a later load of the whole book must not turn it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])
}

type Elements = {
  target: HTMLElement | null
  fromElement: HTMLElement | null
  fl: HTMLElement | null
  fs: HTMLElement | null
  fc: HTMLElement | null
}

/** Starts the turn on the elements it needs; returns what stops it and puts things back. Without them (or with reduced
 * motion) the page just fades in. */
function startTurn({ target, fromElement, fl, fs, fc }: Elements, shown: (value: boolean) => void): () => void {
  if (!target || !fromElement || !fl || !fs || !fc || reducedMotion()) {
    const frame = requestAnimationFrame(() => shown(true))
    return () => cancelAnimationFrame(frame)
  }
  let cancelled = false
  const finish = () => {
    target.style.visibility = ''
    fl.hidden = true
    for (const element of [fs, fc]) {
      element.getAnimations().forEach((animation) => animation.cancel())
      element.style.display = 'none'
    }
  }
  target.style.visibility = 'hidden'
  const a = originOf(fromElement).getBoundingClientRect()
  const b = target.getBoundingClientRect()
  fl.hidden = false
  const frame = requestAnimationFrame(() => shown(true))
  void (async () => {
    try {
      if (fromElement.classList.contains('spine')) {
        const spine = fs.firstElementChild as HTMLElement
        spine.classList.remove('tilt', 'found')
        Object.assign(spine.style, { width: '100%', height: '100%', margin: '0', transform: 'none' })
        Object.assign(fs.style, { left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px`, display: 'block' })
        const cx = b.left + b.width / 2 - a.width / 2
        const cy = b.top
        await fs.animate(
          [
            { transform: 'translate(0,0) rotateY(0deg)', height: `${a.height}px` },
            { transform: `translate(${cx - a.left}px, ${cy - a.top}px) rotateY(-90deg)`, height: `${b.height}px` },
          ],
          { duration: TURN.out.ms, easing: TURN.out.easing, fill: 'forwards' },
        ).finished
        if (cancelled) return
        fs.style.display = 'none'
        Object.assign(fc.style, { left: `${b.left}px`, top: `${b.top}px`, width: `${b.width}px`, height: `${b.height}px`, display: 'block' })
        await fc.animate([{ transform: 'rotateY(90deg)' }, { transform: 'rotateY(0deg)' }], { duration: TURN.in.ms, easing: TURN.in.easing, fill: 'forwards' }).finished
      } else {
        Object.assign(fc.style, { left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px`, display: 'block' })
        await fc.animate(
          [
            { transform: 'translate(0,0) scale(1)' },
            { transform: `translate(${b.left - a.left + (b.width - a.width) / 2}px, ${b.top - a.top + (b.height - a.height) / 2}px) scale(${b.width / a.width})` },
          ],
          { duration: TURN.grow.ms, easing: TURN.grow.easing, fill: 'forwards' },
        ).finished
      }
    } catch {
      // The turn was stopped (the page closed, or the book changed): nothing to do but put things back.
    } finally {
      if (!cancelled) finish()
    }
  })()
  return () => {
    cancelled = true
    cancelAnimationFrame(frame)
    finish()
  }
}
