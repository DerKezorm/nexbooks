/** The form to correct an edition: what it starts with, and what the person changed (the body of the correction). */
import type { Correction, EditionOut } from '../api/client'

export type Form = { title: string; subtitle: string; authors: string; narrators: string; series: string; pos: string; genre: string; language: string }

export function formOf(edition: EditionOut): Form {
  return {
    title: edition.title,
    subtitle: edition.subtitle,
    authors: edition.authors.join('\n'),
    narrators: edition.narrators.join('\n'),
    series: edition.series_name,
    pos: edition.series_pos === null ? '' : String(edition.series_pos),
    genre: edition.genre,
    language: edition.language,
  }
}

/** What the person changed against what the edition says now. A field left alone is not sent; a place in a series that is
 * cleared is not a change either (a correction is taken back with its own button). */
export function changesOf(before: Form, after: Form): Correction {
  const out: Correction = {}
  if (after.title.trim() !== before.title) out.title = after.title.trim()
  if (after.subtitle.trim() !== before.subtitle) out.subtitle = after.subtitle.trim()
  const lines = (text: string) =>
    text
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
  const authors = lines(after.authors)
  if (authors.join('\n') !== before.authors) out.authors = authors
  const narrators = lines(after.narrators)
  if (narrators.join('\n') !== before.narrators) out.narrators = narrators
  if (after.series.trim() !== before.series) out.series = after.series.trim()
  const pos = Number(after.pos.replace(',', '.'))
  if (after.pos.trim() !== '' && Number.isFinite(pos) && pos > 0 && String(pos) !== before.pos) out.series_pos = pos
  if (after.genre !== before.genre) out.genre = after.genre
  if (after.language.trim().toLowerCase() !== before.language) out.language = after.language.trim().toLowerCase()
  return out
}
