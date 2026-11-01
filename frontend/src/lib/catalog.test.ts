/**
 * The rules of the author page: which works a language shows, the sections by kind and by year, what "Alle
 * fehlenden beschaffen" takes, the marks of the editions, and the authors of a search.
 */
import type { AuthorHit } from '../api/client'
import { catalogWork, edition, halloran } from '../test/catalog'
import { work } from '../test/books'
import { ALL, authorHref, defaultLanguage, editionIn, languageOptions, markOf, missing, peopleOf, sections, soon, visible } from './catalog'

const filter = { language: 'de', order: 'kind' as const, look: 'covers' as const, hideOwned: false }

describe('the language', () => {
  it('is the person\'s own first, then the author\'s, then "all"', () => {
    const catalog = halloran()
    expect(languageOptions(catalog, 'de')).toEqual(['de', 'en', ALL])
    expect(defaultLanguage(catalog, 'de')).toBe('de')
    // A language no work came out in is no choice: the author's own is then the first.
    expect(languageOptions(catalog, 'fr')).toEqual(['en', 'de', ALL])
    expect(defaultLanguage(catalog, 'fr')).toBe('en')
  })

  it('leaves out a work that never came out in it, and counts it', () => {
    const { shown, hiddenLanguage } = visible(halloran().works, filter)
    expect(hiddenLanguage).toBe(1)
    expect(shown.map((w) => w.key)).not.toContain('tide')
    expect(visible(halloran().works, { ...filter, language: ALL }).hiddenLanguage).toBe(0)
  })

  it('shows a work in "all" in its original language, and a book of the person\'s in every language', () => {
    const tide = halloran().works.find((w) => w.key === 'tide')!
    expect(editionIn(tide, ALL)!.title).toBe('Tidewater')
    expect(editionIn(tide, 'de')).toBeNull()
    const mine = catalogWork('own-1', [edition('', 'Ohne Sprache')], { token: null, work: work(1), have_ebook: true })
    expect(editionIn(mine, 'de')!.title).toBe('Ohne Sprache')
  })
})

describe('the sections', () => {
  it('by kind: every series in its order, then the kinds, the pen name and last what is unsure', () => {
    const found = sections(halloran().works, filter)
    expect(found.map((s) => s.id)).toEqual(['s:Wrenfield', 'k:roman', 'k:erz', 'p:Richard Vane', 'unsure'])
    expect(found[0]!.works.map((w) => w.key)).toEqual(['ash', 'doors'])
    expect(found[1]!.works.map((w) => w.key)).toEqual(['salt', 'soot', 'keeper'])
  })

  it('by year: the decades, newest first, and what is unsure last', () => {
    const found = sections(halloran().works, { ...filter, order: 'year' })
    expect(found.map((s) => s.id)).toEqual(['d:2020', 'd:1980', 'd:1970', 'unsure'])
    // The German years count in German (the stories came out in German in 2026).
    expect(found[0]!.works.map((w) => w.key)).toEqual(['keeper', 'roses'])
  })

  it('hide what the person has when asked, and say how many', () => {
    const works = halloran().works.map((w) => (w.key === 'soot' ? { ...w, work: work(9), have_ebook: true } : w))
    const { shown, hiddenOwned } = visible(works, { ...filter, hideOwned: true })
    expect(hiddenOwned).toBe(1)
    expect(shown.some((w) => w.key === 'soot')).toBe(false)
  })

  it('make a series a section only from two works of it on', () => {
    // Soot alone in a series of its own (a source named one book of it): it stands with the novels.
    const works = halloran().works.map((w) => (w.key === 'soot' ? { ...w, series: 'Russ', series_pos: 1 } : w))
    const found = sections(works, filter)
    expect(found.map((s) => s.id)).toEqual(['s:Wrenfield', 'k:roman', 'k:erz', 'p:Richard Vane', 'unsure'])
    expect(found[1]!.works.map((w) => w.key)).toContain('soot')
    // With a second volume it is a section.
    const two = works.map((w) => (w.key === 'salt' ? { ...w, series: 'Russ', series_pos: 2 } : w))
    expect(sections(two, filter).map((s) => s.id)).toContain('s:Russ')
  })

  it('counts the volumes of a series the filter hides', () => {
    const works = halloran().works.map((w) => (w.key === 'doors' ? { ...w, editions: w.editions.filter((e) => e.language === 'en') } : w))
    const series = sections(works, filter).find((s) => s.type === 'series')!
    expect(series.type === 'series' && series.hidden).toBe(1)
  })
})

describe('"Alle fehlenden beschaffen"', () => {
  it('takes the sure works the person has not and nobody asked for, in the language', () => {
    const works = halloran().works.map((w) => (w.key === 'soot' ? { ...w, work: work(9) } : w))
    const asked = (w: { key: string }) => w.key === 'salt'
    expect(missing(works, 'de', asked).map((w) => w.key)).toEqual(['roses', 'ash', 'doors', 'rain', 'keeper'])
    expect(missing(works, 'en', asked).map((w) => w.key)).toContain('tide')
  })

  it('leaves out a work a book on the shelf may be, for a person to look at first', () => {
    const works = halloran().works.map((w) => (w.key === 'roses' ? { ...w, maybe_have: work(9) } : w))
    expect(missing(works, 'de', () => false).map((w) => w.key)).not.toContain('roses')
    expect(missing(halloran().works, 'de', () => false).map((w) => w.key)).toContain('roses')
  })

  it('the works to come are the announced ones that are sure', () => {
    expect(soon(halloran().works).map((w) => w.key)).toEqual(['keeper'])
  })
})

describe('the marks of an edition', () => {
  it('say on the shelf, wanted, exists or not known', () => {
    const salt = halloran().works[0]!
    const german = editionIn(salt, 'de')
    expect(markOf({ ...salt, have_ebook: true }, german, 'ebook', false)).toBe('have')
    expect(markOf(salt, german, 'ebook', true)).toBe('want')
    expect(markOf(salt, german, 'audio', false)).toBe('avail')
    expect(markOf(salt, editionIn(salt, 'en'), 'audio', false)).toBe('none')
  })
})

describe('the authors of a search', () => {
  const hit = (name: string, over: Partial<AuthorHit> = {}): AuthorHit => ({ name, person_id: null, token: null, alias: '', works: null, on_shelf: 0, soon: 0, photo_url: null, genre: '', ...over })

  it('are the person\'s own first, without the same author twice, four at most', () => {
    const mine = [hit('Edmund Halloran', { person_id: 3 })]
    const theirs = [hit('Edmund Halloran', { token: 't1', person_id: 3 }), hit('edmund halloran', { token: 't2' }), hit('Ida Falkner', { token: 't3' }), hit('B', { token: 'b' }), hit('C', { token: 'c' }), hit('D', { token: 'd' })]
    expect(peopleOf(mine, theirs).map((h) => h.name)).toEqual(['Edmund Halloran', 'Ida Falkner', 'B', 'C'])
  })

  it('lead to the person of the libraries, else to the catalogue', () => {
    expect(authorHref(hit('A', { person_id: 3, token: 'x' }))).toBe('/person/3')
    expect(authorHref(hit('A', { token: 'a b' }))).toBe('/author/a%20b')
    expect(authorHref(hit('A'))).toBeNull()
  })
})
