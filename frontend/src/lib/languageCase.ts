/**
 * What went wrong when the languages named for a downloaded book do not agree, in a word the card can say it with. The check
 * after a download asks a person when its witnesses of the language (the package of the file, the text, the name of the release,
 * the folder) do not say the same, or when only a weak one speaks. The card must say what happened in plain sentences: what was
 * searched, what each witness says, and that often only the entry in the file is wrong.
 */
export type UnclearValues = { file?: unknown; text?: unknown; name?: unknown; folder?: unknown; wanted?: unknown }

export type UnclearCase =
  /** The file names one language and the text reads like another. */
  | 'fileText'
  /** The text is in two languages. */
  | 'mixed'
  /** The text is in a language nexbooks has no words for. */
  | 'other'
  /** Other witnesses name different languages. */
  | 'clash'
  /** Only one witness names a language, and it is not the one wished. */
  | 'single'

export type UnclearFacts = {
  kind: UnclearCase
  /** For `single`: which witness spoke and the language it named. */
  witness: 'file' | 'text' | 'name' | 'folder' | null
  language: string
}

const code = (value: unknown): string => (typeof value === 'string' ? value : '')

export function unclearCase(values: UnclearValues): UnclearFacts {
  const text = code(values.text)
  if (text === 'mixed') return { kind: 'mixed', witness: null, language: '' }
  if (text === 'other') return { kind: 'other', witness: null, language: '' }
  const said = (['file', 'text', 'name', 'folder'] as const)
    .map((witness) => ({ witness, language: code(values[witness]) }))
    .filter((item) => item.language !== '')
  const languages = new Set(said.map((item) => item.language))
  const file = code(values.file)
  if (file && text && file !== text) return { kind: 'fileText', witness: null, language: '' }
  if (languages.size > 1) return { kind: 'clash', witness: null, language: '' }
  const only = said[0]
  return { kind: 'single', witness: only?.witness ?? null, language: only?.language ?? '' }
}
