/**
 * Small things the pages share: a sentence after something was done, handed to the frame's toast through the
 * navigation state.
 */
import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

/** A way on from a notice: where it leads and what it says. */
export type NoticeLink = { to: string; label: string }

/** Says `notice` on the current page; `to` goes elsewhere and says it there; `link` adds a way on to the sentence. */
export function useSay() {
  const navigate = useNavigate()
  const location = useLocation()
  return useCallback(
    (notice: string, to?: string, link?: NoticeLink) =>
      navigate(to ?? location.pathname + location.search, { replace: !to, state: { notice, ...(link ? { link } : {}) } }),
    [navigate, location.pathname, location.search],
  )
}

/** An answer that should be a list, as a list: a page never breaks on a server that answers something else. */
export function listOf<T>(value: T[] | unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

const FSI = String.fromCharCode(0x2068)
const PDI = String.fromCharCode(0x2069)

/** A person's text (a name, a device) put into a sentence, kept apart from it: characters inside it that turn the
 * direction of text cannot spill over the sentence around it (Unicode's first-strong isolate, the text form of
 * `<bdi>`). */
export function iso(text: string): string {
  return FSI + text + PDI
}
