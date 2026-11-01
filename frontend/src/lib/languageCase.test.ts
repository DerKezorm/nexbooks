import { unclearCase } from './languageCase'

describe('what the card says when the languages of a downloaded book do not agree', () => {
  it('the file names one language and the text reads like another', () => {
    expect(unclearCase({ file: 'en', text: 'de', wanted: 'de' }).kind).toBe('fileText')
    // The name of the release, if it names a third one, does not change what the card leads with.
    expect(unclearCase({ file: 'en', text: 'de', name: 'fr', wanted: 'de' }).kind).toBe('fileText')
  })

  it('a text in two languages, or in one nexbooks has no words for, is told as that', () => {
    expect(unclearCase({ file: 'de', text: 'mixed', wanted: 'de' }).kind).toBe('mixed')
    expect(unclearCase({ text: 'other', wanted: 'de' }).kind).toBe('other')
  })

  it('witnesses that name different languages without the file against the text are a clash', () => {
    expect(unclearCase({ file: 'de', name: 'en', wanted: 'de' }).kind).toBe('clash')
    expect(unclearCase({ name: 'en', folder: 'fr', wanted: 'de' }).kind).toBe('clash')
  })

  it('one witness alone that names a language is told with its name', () => {
    expect(unclearCase({ text: 'en', wanted: 'de' })).toEqual({ kind: 'single', witness: 'text', language: 'en' })
    expect(unclearCase({ name: 'en', wanted: 'de' })).toEqual({ kind: 'single', witness: 'name', language: 'en' })
    expect(unclearCase({ folder: 'fr', wanted: 'de' })).toEqual({ kind: 'single', witness: 'folder', language: 'fr' })
  })

  it('leaves nothing to chance for values that are no text', () => {
    expect(unclearCase({ file: 3, text: null, wanted: 'de' }).kind).toBe('single')
  })
})
