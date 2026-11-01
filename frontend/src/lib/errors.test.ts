/** A refusal that names a limit says it in the sentence: "at most 20 MB", never a raw placeholder. */
import '../i18n'
import { changeLanguage } from '../i18n'
import { errorText } from './errors'

describe('the sentence for an error', () => {
  it('fills in what the server sent along', async () => {
    await changeLanguage('de', false)
    expect(errorText('avatar_too_large', { max_mb: 20 })).toBe('Das Bild ist zu groß, höchstens 20 MB.')
    await changeLanguage('en', false)
    expect(errorText('too_large', { max_mb: 16 })).toBe('The file is larger than allowed, at most 16 MB.')
  })

  it('names the field a refused input is about, when it is one it knows', async () => {
    await changeLanguage('de', false)
    expect(errorText('invalid_input', { field: 'title' })).toBe('Die Eingabe ist nicht gültig: Titel.')
    expect(errorText('invalid_input', { fields: ['series_pos', 'genre'] })).toBe('Die Eingabe ist nicht gültig: Band, Genre.')
    await changeLanguage('en', false)
    expect(errorText('invalid_input', { fields: ['title'] })).toBe('The input is not valid: title.')
    // A field it has no name for, or none at all: the general sentence.
    expect(errorText('invalid_input', { field: 'something_else' })).toBe('The input is not valid.')
    expect(errorText('invalid_input')).toBe('The input is not valid.')
  })

  it('falls back to the general sentence for a code it does not know', async () => {
    await changeLanguage('en', false)
    expect(errorText('no_such_code')).toBe(errorText('internal_error'))
  })
})
