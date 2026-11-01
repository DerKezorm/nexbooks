import { addressIn, entryFinder } from './zipnames'

describe('which entry of the ZIP an address means', () => {
  it('takes the name as it is first, a name with a percent sign in it included', () => {
    const find = entryFinder(['OEBPS/Text/100% Done.htm', 'OEBPS/Text/100%25.htm', 'OEBPS/a.htm'])
    expect(find('OEBPS/Text/100% Done.htm')).toBe('OEBPS/Text/100% Done.htm')
    // The entry really is called "100%25.htm": the address is the name, not an escape of another one.
    expect(find('OEBPS/Text/100%25.htm')).toBe('OEBPS/Text/100%25.htm')
  })

  it('reads an address written as a URL: the reserved characters the library leaves alone, too', () => {
    const find = entryFinder(['OEBPS/Text/Part, Two; More & Co.htm', 'OEBPS/Text/My Part.htm'])
    expect(find('OEBPS/Text/Part%2C Two%3B More %26 Co.htm')).toBe('OEBPS/Text/Part, Two; More & Co.htm')
    expect(find('OEBPS/Text/Part%2C%20Two%3B%20More%20%26%20Co.htm')).toBe('OEBPS/Text/Part, Two; More & Co.htm')
    expect(find('OEBPS/Text/My%20Part.htm')).toBe('OEBPS/Text/My Part.htm')
  })

  it('knows nothing of an escape that is none, and of an entry that is not there', () => {
    const find = entryFinder(['OEBPS/a.htm'])
    expect(find('OEBPS/%E0%A4%A.htm')).toBeNull()
    expect(find('OEBPS/b.htm')).toBeNull()
    expect(find('')).toBeNull()
  })

  it('finds a name written in another Unicode form, letter case or with backslashes', () => {
    const composed = 'Caf' + String.fromCharCode(0xe9)
    const decomposed = 'Cafe' + String.fromCharCode(0x301)
    const back = ['OEBPS', 'Text', 'Two.xhtml'].join(String.fromCharCode(92))
    const find = entryFinder([`OEBPS/Text/${composed}.xhtml`, back])
    expect(decomposed).not.toBe(composed)
    expect(find(`OEBPS/Text/${decomposed}.xhtml`)).toBe(`OEBPS/Text/${composed}.xhtml`)
    expect(find(`oebps/text/${composed.toUpperCase()}.xhtml`)).toBe(`OEBPS/Text/${composed}.xhtml`)
    expect(find('OEBPS/Text/Two.xhtml')).toBe(back)
  })

  it('never chooses between two entries that both fit', () => {
    const find = entryFinder(['OEBPS/Text/A.htm', 'OEBPS/Text/a.htm'])
    expect(find('OEBPS/Text/A.htm')).toBe('OEBPS/Text/A.htm')
    // Both fit a name in other letters; none is taken.
    expect(find('OEBPS/TEXT/A.HTM')).toBeNull()
  })

  it('finds the one entry that ends in an address whose folder was lost, and not when two end in it', () => {
    const find = entryFinder(['OEBPS/Text/50%.htm', 'OEBPS/Text/b.htm', 'X/Text/b.htm'])
    expect(find('Text/50%.htm')).toBe('OEBPS/Text/50%.htm')
    expect(find('Text/b.htm')).toBeNull()
    // A bare name is no address to complete.
    expect(entryFinder(['OEBPS/cover.jpg'])('cover.jpg')).toBeNull()
  })

  it('does not look through hundreds of entries of one name', () => {
    const names = Array.from({ length: 80 }, (_unused, n) => `d${n}/page.htm`)
    names.push('only/Text/page.htm')
    expect(entryFinder(names)('Text/page.htm')).toBeNull()
  })

  it('writes an address as a path within the package folder, without fragment or query', () => {
    expect(addressIn('OEBPS', 'Text/One%20Two.htm#top')).toBe('OEBPS/Text/One%20Two.htm')
    expect(addressIn('OEBPS/Text', '../Images/a.png?x=1')).toBe('OEBPS/Images/a.png')
    expect(addressIn('', 'a.htm')).toBe('a.htm')
    expect(addressIn('', '../../a.htm')).toBe('a.htm')
  })
})
