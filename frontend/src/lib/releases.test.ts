import { sizeText } from './releases'

describe('the size of a release', () => {
  it('is written as the design writes it', () => {
    expect(sizeText(2_300_000, 'de')).toBe('2,3 MB')
    expect(sizeText(642_400_000, 'de')).toBe('642 MB')
    expect(sizeText(1_642_000_000, 'de')).toBe('1,6 GB')
    expect(sizeText(9_990_000, 'en')).toBe('10.0 MB')
    expect(sizeText(10_000_000, 'en')).toBe('10 MB')
  })
})
