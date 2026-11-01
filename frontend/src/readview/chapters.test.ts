import { chaptersOf, type TocLike } from './chapters'

/** The section a href lies in: the file name before the fragment, numbered in order of first appearance. */
function sections(): (href: string | undefined) => number | undefined {
  const seen = new Map<string, number>()
  return (href) => {
    if (!href) return undefined
    const file = href.split('#')[0]
    if (!seen.has(file)) seen.set(file, seen.size)
    return seen.get(file)
  }
}

const item = (label: string, href?: string, subitems?: TocLike[]): TocLike & { label: string } => ({ label, href, subitems })

describe('what counts as a chapter', () => {
  it('counts every entry of a book without parts, as before', () => {
    const toc = [item('One', 'c1.xhtml'), item('Two', 'c2.xhtml'), item('Three', 'c3.xhtml'), item('Four', 'c4.xhtml')]
    const chapters = chaptersOf(toc, sections())
    expect(chapters.entries).toHaveLength(4)
    expect(toc.map((entry) => chapters.numberOf(entry))).toEqual([1, 2, 3, 4])
  })

  it('counts the chapters of a book with parts, and not the parts', () => {
    // The shape of a novel in three parts of four chapters: a part lies where its first chapter begins.
    const parts = [1, 2, 3].map((part) =>
      item(`Part ${part}`, `c${(part - 1) * 4 + 1}.xhtml`, [1, 2, 3, 4].map((n) => item(`Chapter ${(part - 1) * 4 + n}`, `c${(part - 1) * 4 + n}.xhtml`))),
    )
    const chapters = chaptersOf(parts, sections())
    expect(chapters.entries).toHaveLength(12)
    const all = parts.flatMap((part) => part.subitems!)
    expect(all.map((chapter) => chapters.numberOf(chapter))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    // Where the reader is on the page of a part itself, no chapter is named.
    expect(parts.map((part) => chapters.numberOf(part))).toEqual([0, 0, 0])
  })

  it('counts a part that has a page of its own and no link as a part', () => {
    const toc = [item('Part One', 'part1.xhtml', [item('A', 'a.xhtml'), item('B', 'b.xhtml')]), item('Part Two', undefined, [item('C', 'c.xhtml')])]
    const chapters = chaptersOf(toc, sections())
    expect(chapters.entries.map((entry) => (entry as { label: string }).label)).toEqual(['A', 'B', 'C'])
  })

  it('counts a chapter with sub-points inside it as one chapter, and the sub-points are in it', () => {
    const second = item('Two', 'c2.xhtml', [item('Two, first', 'c2.xhtml#a'), item('Two, second', 'c2.xhtml#b'), item('Two, third', 'c2.xhtml#c')])
    const toc = [item('One', 'c1.xhtml'), second, item('Three', 'c3.xhtml')]
    const chapters = chaptersOf(toc, sections())
    expect(chapters.entries).toHaveLength(3)
    expect(chapters.numberOf(toc[2])).toBe(3)
    expect(chapters.numberOf(second)).toBe(2)
    expect(second.subitems!.map((sub) => chapters.numberOf(sub))).toEqual([2, 2, 2])
  })

  it('reads parts, chapters and the sub-points of the chapters in one book', () => {
    const chapter1 = item('Chapter 1', 'a1.xhtml', [item('1.1', 'a1.xhtml#x'), item('1.2', 'a1.xhtml#y')])
    const chapter2 = item('Chapter 2', 'a2.xhtml')
    const chapter3 = item('Chapter 3', 'b1.xhtml', [item('3.1', 'b1.xhtml#x')])
    const toc = [item('Part A', 'a1.xhtml', [chapter1, chapter2]), item('Part B', undefined, [chapter3]), item('Epilogue', 'e.xhtml')]
    const chapters = chaptersOf(toc, sections())
    expect(chapters.entries).toHaveLength(4)
    expect([chapter1, chapter2, chapter3, toc[2]].map((entry) => chapters.numberOf(entry))).toEqual([1, 2, 3, 4])
    expect(chapters.numberOf(chapter1.subitems![1])).toBe(1)
    expect(chapters.numberOf(chapter3.subitems![0])).toBe(3)
  })

  it('counts a chapter whose sub-points are files of their own as the chapters they are', () => {
    // A tool that cuts a long chapter into files makes its sub-points readable sections of their own.
    const toc = [item('Chapter', 'x.xhtml', [item('Part a', 'x-1.xhtml'), item('Part b', 'x-2.xhtml')])]
    expect(chaptersOf(toc, sections()).entries).toHaveLength(2)
  })

  it('knows nothing of an entry that is not in the contents, and of a book with none', () => {
    const chapters = chaptersOf([], sections())
    expect(chapters.entries).toEqual([])
    expect(chapters.numberOf(null)).toBe(0)
    expect(chapters.numberOf(undefined)).toBe(0)
    expect(chapters.numberOf(item('Stranger', 'z.xhtml'))).toBe(0)
  })

  it('does not run out of stack on contents nested a thousand deep', () => {
    let node: TocLike = item('deep', 'd.xhtml')
    for (let level = 0; level < 1000; level++) node = item(`level ${level}`, 'd.xhtml', [node])
    const chapters = chaptersOf([node], sections())
    expect(chapters.entries.length).toBeGreaterThanOrEqual(1)
  })
})
