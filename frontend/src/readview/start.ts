/**
 * Where a book opens the first time. A book names the start of its text (an EPUB 3 landmark `bodymatter`, an EPUB 2 guide
 * reference `text`), and the reader goes there. A real book named as its "start of the text" the page of its cover, which
 * stood last of 64 sections, after the imprint: it opened at 100 %. The start is followed only when it lies in the first
 * quarter of the sections; otherwise the book opens at its first page.
 */

interface Landmark {
  type: string[]
  href: string
}

interface Section {
  linear?: string
}

export interface BookLike {
  landmarks?: Landmark[] | null
  sections: Section[]
  resolveHref?: (href: string) => { index: number } | null | undefined
}

/** Whether the start of the text the book names is one to open at: true when it names none (the reader then takes the
 * first section read in order) or when it lies in the first quarter of the sections. */
export function followTextStart(book: BookLike): boolean {
  const mark = book.landmarks?.find((item) => item.type.includes('bodymatter') || item.type.includes('text'))
  if (!mark) return true
  let index: number | undefined
  try {
    index = book.resolveHref?.(mark.href)?.index
  } catch {
    return false
  }
  if (typeof index !== 'number' || index < 0) return false
  const count = book.sections.length
  return index < Math.max(1, Math.ceil(count / 4))
}
