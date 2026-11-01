import type { CompareRow } from '../api/client'
import { contradictions, folderText, groupRows, GROUPS, jumpPoints } from './reviewFacts'

const row = (field: CompareRow['field'], state: CompareRow['state']): CompareRow => ({ field, file: 'a', source: 'b', state, unit: '' })

describe('the groups of a comparison', () => {
  it('put what speaks against a proposal first and keep a contradiction apart from silence', () => {
    expect(GROUPS).toEqual(['differs', 'similar', 'agrees', 'file_silent', 'source_silent'])
    const groups = groupRows([row('title', 'agrees'), row('isbn', 'file_silent'), row('year', 'differs'), row('authors', 'similar'), row('publisher', 'source_silent'), row('language', 'differs')])
    expect(groups.differs.map((item) => item.field)).toEqual(['year', 'language'])
    expect(groups.file_silent.map((item) => item.field)).toEqual(['isbn'])
    expect(groups.source_silent.map((item) => item.field)).toEqual(['publisher'])
    expect(groups.agrees.length + groups.similar.length).toBe(2)
  })

  it('count only what the file contradicts', () => {
    expect(contradictions([row('title', 'agrees'), row('isbn', 'file_silent'), row('year', 'differs')])).toBe(1)
    expect(contradictions([])).toBe(0)
  })
})

describe('the places of a short listen', () => {
  it('are the start, a third and the middle, in whole seconds', () => {
    expect(jumpPoints(3600)).toEqual([{ key: 'start', at: 0 }, { key: 'third', at: 1200 }, { key: 'middle', at: 1800 }, { key: 'end', at: 3580 }])
    expect(jumpPoints(100)).toEqual([{ key: 'start', at: 0 }, { key: 'third', at: 33 }, { key: 'middle', at: 50 }, { key: 'end', at: 80 }])
    expect(jumpPoints(12).at(-1)).toEqual({ key: 'end', at: 0 })
  })

  it('stay at the start for a recording of no length', () => {
    for (const total of [0, -5, Number.NaN]) expect(jumpPoints(total).map((point) => point.at)).toEqual([0, 0, 0, 0])
  })
})

describe('the folder of a file', () => {
  it('is named, and the top of the library has its own words', () => {
    expect(folderText('Lindqvist/Obstgarten', 'oben')).toBe('Lindqvist/Obstgarten')
    expect(folderText('', 'oben')).toBe('oben')
  })
})
