import { figure, formatsLabel, inkFor, languageChip, luminance, patternFor } from './books'

describe('what a cover colour wears', () => {
  it('puts dark text on a light cover and light text on a dark one, by the rule of the design (brightness above 0.45)', () => {
    expect(inkFor('#d9cba9')).toBe('#2a2420')
    expect(inkFor('#e7ddc8')).toBe('#2a2420')
    expect(inkFor('#5e2027')).toBe('#efe6d2')
    expect(inkFor('#1d1c1f')).toBe('#efe6d2')
    // Right at the edge: 0.45 is not above 0.45.
    expect(luminance('#737373')).toBeCloseTo(0.451, 2)
    expect(inkFor('#737373')).toBe('#2a2420')
    expect(inkFor('#717171')).toBe('#efe6d2')
  })

  it('knows a colour that is none as black rather than failing', () => {
    expect(luminance('')).toBe(0)
    expect(inkFor('nonsense')).toBe('#efe6d2')
  })
})

describe('the drawn cover', () => {
  it('has five patterns, each drawn in the colour of the book, and the number is taken modulo five', () => {
    const looks = [0, 1, 2, 3, 4].map((pattern) => JSON.stringify(patternFor('#5e2027', pattern)))
    expect(new Set(looks).size).toBe(5)
    expect(patternFor('#5e2027', 7)).toEqual(patternFor('#5e2027', 2))
    expect(String(patternFor('#5e2027', 0).boxShadow)).toContain('#5e2027')
    // A light cover gets a dark accent, a dark one a light accent.
    expect(String(patternFor('#d9cba9', 1).backgroundImage)).toContain('rgba(0,0,0,.13)')
    expect(String(patternFor('#5e2027', 1).backgroundImage)).toContain('rgba(255,255,255,.12)')
  })
})

describe('the little words', () => {
  it('writes the formats as one chip and the language as two capital letters', () => {
    expect(formatsLabel({ formats: ['epub', 'azw3', 'mobi'] })).toBe('EPUB · AZW3 · MOBI')
    expect(formatsLabel({ formats: [] })).toBe('')
    expect(languageChip('de')).toBe('DE')
    expect(languageChip('deu')).toBe('DE')
    expect(languageChip('')).toBe('')
  })

  it('writes a figure with the separator of the language', () => {
    expect(figure(2000, 'en')).toBe('2,000')
    expect(figure(2000, 'de')).toBe('2.000')
  })
})
