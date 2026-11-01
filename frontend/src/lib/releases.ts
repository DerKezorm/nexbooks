/** What the dialog of the releases writes the same way as the design. */

/** A size as the design writes it: below 10 MB with one decimal, from 1000 MB in GB with one decimal, else whole MB. */
export function sizeText(bytes: number, language: string): string {
  const mb = bytes / 1_000_000
  const one = (value: number) => value.toLocaleString(language, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  if (mb >= 1000) return `${one(mb / 1000)} GB`
  if (mb < 10) return `${one(mb)} MB`
  return `${Math.round(mb).toLocaleString(language)} MB`
}

/** The reasons that are doubts (the server's ``judge.ASK``): nexbooks loads such a release only after a person looked. */
export const DOUBTS: ReadonlySet<string> = new Set(['language_unsure', 'bilingual', 'edition', 'collection', 'maybe_partial', 'title_short', 'maybe_other_volume', 'maybe_other_book', 'author_missing', 'size_odd'])
