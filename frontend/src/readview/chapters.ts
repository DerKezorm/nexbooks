/**
 * What counts as a chapter in "Chapter 14 of 38".
 *
 * The contents of a book list more than its chapters: parts ("Part Two") that hold chapters, and the sub-points of a chapter
 * that lead to places inside it. Counted as they come, a book of three parts and twelve chapters reads "Chapter 2 of 15" at
 * its first chapter. So the contents are read as a tree, and only the entries that are chapters are counted:
 *
 * - an entry with nothing below it is a chapter;
 * - an entry with sub-points that all lie in the same section of the book as the entry itself is a chapter, and its sub-points
 *   are places inside it (they do not count);
 * - any other entry with sub-points holds the chapters below it (a part): it is not counted, its sub-points are looked at in
 *   the same way.
 *
 * A book without parts is counted as before: every entry once.
 */
export type TocLike = { id?: number; href?: string; subitems?: TocLike[] }

export type Chapters = {
  /** The entries that are chapters, in the order of the book. */
  entries: TocLike[]
  /** The number of the chapter an entry of the contents is in (1 for the first), or 0 for a part or an entry that is none. */
  numberOf: (item: TocLike | null | undefined) => number
}

/** How deep the contents are read (as far as the list of the contents shown to the person). */
const MAX_LEVELS = 8

export function chaptersOf(toc: TocLike[], sectionOf: (href: string | undefined) => number | undefined): Chapters {
  const entries: TocLike[] = []
  const owner = new Map<TocLike, number>()

  /** Every entry below `item`, to any depth the contents are read. */
  const below = (item: TocLike, level: number): TocLike[] => {
    const out: TocLike[] = []
    const stack: { node: TocLike; level: number }[] = (item.subitems ?? []).map((node) => ({ node, level: level + 1 }))
    while (stack.length > 0) {
      const { node, level: at } = stack.pop()!
      out.push(node)
      if (at < MAX_LEVELS) for (const child of node.subitems ?? []) stack.push({ node: child, level: at + 1 })
    }
    return out
  }

  const claim = (item: TocLike, level: number, number: number) => {
    owner.set(item, number)
    for (const sub of below(item, level)) owner.set(sub, number)
  }

  const visit = (nodes: TocLike[], level: number) => {
    for (const node of nodes) {
      const kids = node.subitems ?? []
      if (kids.length === 0 || level >= MAX_LEVELS) {
        entries.push(node)
        claim(node, level, entries.length)
        continue
      }
      const own = node.href ? sectionOf(node.href) : undefined
      const inside = own !== undefined && below(node, level).every((sub) => sectionOf(sub.href) === own)
      if (inside) {
        entries.push(node)
        claim(node, level, entries.length)
      } else visit(kids, level + 1)
    }
  }
  visit(toc, 1)

  return { entries, numberOf: (item) => (item ? (owner.get(item) ?? 0) : 0) }
}
