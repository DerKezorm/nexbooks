/**
 * The spines against the design: the numbers its own code gave to 27 books of
 * its invented library (read from its page: `--t`, `--h`, `--fs`, the gilt, the band, the ink, the lean), held against the
 * functions the interface uses. The set holds the shortest and the longest titles, the narrowest and the widest books, a
 * recording alone, the books that lean, and two books of every genre.
 */
import type { WorkBrief } from '../api/client'
import { footName, genreClass, leans, spineColors, spineDims, titleSize } from './spine'

const TABLE = [
  { id: 144, title: 'Ebbespur', genre: 'krimi', color: '#5e2027', pages: 251, tall: 0.405077, audioOnly: false, t: '21px', h: '181px', fs: '15.0px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 126, title: 'Nebelspur', genre: 'krimi', color: '#1f304b', pages: 568, tall: 0.470039, audioOnly: false, t: '47px', h: '185px', fs: '15.0px', gold: '#e3c57f', band: '#d8cba9', ink: '#efe6d2', tilt: false },
  { id: 363, title: 'Das Geheimnis von Burg Krähenstein III', genre: 'kinder', color: '#a8616a', pages: 182, tall: 0.538828, audioOnly: false, t: '18px', h: '175px', fs: '10.5px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 373, title: 'Das Geheimnis von Burg Krähenstein', genre: 'kinder', color: '#8b4b2b', pages: 161, tall: 0.915599, audioOnly: false, t: '18px', h: '197px', fs: '10.5px', gold: '#e3c57f', band: '#b5463c', ink: '#efe6d2', tilt: false },
  { id: 371, title: 'Die kleine Schnecke', genre: 'kinder', color: '#a8616a', pages: 60, tall: 0.392328, audioOnly: false, t: '18px', h: '167px', fs: '10.5px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 380, title: 'Die mutige Maus', genre: 'kinder', color: '#8b4b2b', pages: 64, tall: 0.668191, audioOnly: false, t: '18px', h: '183px', fs: '13.5px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 208, title: 'The Ember Sea II', genre: 'fantasy', color: '#2f4b3b', pages: 726, tall: 0.834979, audioOnly: false, t: '54px', h: '206px', fs: '15.0px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 231, title: 'Die Krone der Ersten II', genre: 'fantasy', color: '#215258', pages: 676, tall: 0.272802, audioOnly: false, t: '54px', h: '174px', fs: '10.5px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 7, title: 'Das Echo am Deich', genre: 'roman', color: '#d9cba9', pages: 152, tall: 0.390831, audioOnly: true, t: '26px', h: '166px', fs: '10.5px', gold: '#6e5120', band: '#5b7c9c', ink: '#2a2420', tilt: true },
  { id: 19, title: 'Die Fährfrau', genre: 'roman', color: '#215258', pages: 204, tall: 0.181713, audioOnly: true, t: '26px', h: '166px', fs: '14.1px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 30, title: 'Gelbe Tage in Ostende', genre: 'roman', color: '#5e2027', pages: 238, tall: 0.655643, audioOnly: false, t: '20px', h: '196px', fs: '10.8px', gold: '#e3c57f', band: '#d8cba9', ink: '#efe6d2', tilt: true },
  { id: 3, title: 'The Quiet Harbor', genre: 'roman', color: '#5e2027', pages: 254, tall: 0.894381, audioOnly: false, t: '21px', h: '210px', fs: '15.0px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 4, title: 'Was die Möwen wissen II', genre: 'roman', color: '#5e2027', pages: 198, tall: 0.690274, audioOnly: false, t: '18px', h: '198px', fs: '10.5px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 112, title: 'The Ninth Witness', genre: 'krimi', color: '#5e2027', pages: 301, tall: 0.388915, audioOnly: false, t: '25px', h: '181px', fs: '10.5px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 113, title: 'The Drowned Key', genre: 'krimi', color: '#1d1c1f', pages: 375, tall: 0.016353, audioOnly: false, t: '31px', h: '159px', fs: '10.5px', gold: '#e3c57f', band: '#b5463c', ink: '#efe6d2', tilt: false },
  { id: 183, title: 'The Thorn Oath', genre: 'fantasy', color: '#a3762c', pages: 420, tall: 0.549177, audioOnly: false, t: '35px', h: '190px', fs: '15.0px', gold: '#6e5120', band: '#5b7c9c', ink: '#2a2420', tilt: false },
  { id: 184, title: 'Der Thron aus Eis IV', genre: 'fantasy', color: '#4f2b47', pages: 140, tall: 0.523535, audioOnly: false, t: '18px', h: '188px', fs: '10.6px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 235, title: 'Die Kolonie auf Merid', genre: 'scifi', color: '#e7ddc8', pages: 254, tall: 0.061054, audioOnly: false, t: '21px', h: '162px', fs: '10.5px', gold: '#6e5120', band: '#5b7c9c', ink: '#2a2420', tilt: false },
  { id: 236, title: 'The Last Transit', genre: 'scifi', color: '#3d4752', pages: 153, tall: 0.03098, audioOnly: false, t: '18px', h: '160px', fs: '10.5px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 271, title: 'Die Schwestern des Kaufmanns', genre: 'klassiker', color: '#1f304b', pages: 140, tall: 0.774259, audioOnly: false, t: '18px', h: '203px', fs: '10.5px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 272, title: 'Briefe aus Brügge', genre: 'klassiker', color: '#7d2f2b', pages: 324, tall: 0.310451, audioOnly: false, t: '27px', h: '176px', fs: '11.1px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
  { id: 311, title: 'Das Buch vom Gehen IV', genre: 'sachbuch', color: '#d9cba9', pages: 265, tall: 0.830261, audioOnly: false, t: '22px', h: '206px', fs: '11.7px', gold: '#6e5120', band: '#5b7c9c', ink: '#2a2420', tilt: false },
  { id: 313, title: 'Kleine Geschichte des Zuckers', genre: 'sachbuch', color: '#bf9234', pages: 427, tall: 0.816482, audioOnly: false, t: '36px', h: '205px', fs: '10.5px', gold: '#6e5120', band: '#b5463c', ink: '#2a2420', tilt: false },
  { id: 359, title: 'Die fliegende Ente', genre: 'kinder', color: '#bf9234', pages: 204, tall: 0.507494, audioOnly: false, t: '18px', h: '173px', fs: '10.5px', gold: '#6e5120', band: '#5b7c9c', ink: '#2a2420', tilt: false },
  { id: 360, title: 'Die mutige Laterne', genre: 'kinder', color: '#7c8c6b', pages: 148, tall: 0.315231, audioOnly: false, t: '18px', h: '162px', fs: '10.5px', gold: '#6e5120', band: '#c9993f', ink: '#2a2420', tilt: false },
  { id: 391, title: 'Gedichte über Gärten', genre: 'lyrik', color: '#4f2b47', pages: 320, tall: 0.012775, audioOnly: false, t: '27px', h: '159px', fs: '10.5px', gold: '#e3c57f', band: '#5b7c9c', ink: '#efe6d2', tilt: false },
  { id: 392, title: 'Moos. Gedichte', genre: 'lyrik', color: '#4f2b47', pages: 354, tall: 0.037407, audioOnly: false, t: '30px', h: '160px', fs: '11.3px', gold: '#e3c57f', band: '#c9993f', ink: '#efe6d2', tilt: false },
]

function brief(row: (typeof TABLE)[number]): WorkBrief {
  return {
    id: row.id, title: row.title, authors: [{ id: 1, name: 'Ada Feldkamp' }], series: null, genre: row.genre, genre_guess: false, color: row.color, cover_url: null,
    pages: row.pages, year: 2020, pattern: 0, tall: row.tall, gold: false, audio_only: row.audioOnly, added_at: '2026-10-01T10:00:00Z', editions: [],
  }
}

describe('the spine of a book, by the numbers of the design', () => {
  it.each(TABLE)('is as wide, as high and set in as large letters as the design has it: $title', (row) => {
    const work = brief(row)
    const { t, h } = spineDims(work)
    expect(`${t}px`).toBe(row.t)
    expect(`${h}px`).toBe(row.h)
    // A recording alone is a case, whatever its title: no letters are measured against it by the design.
    if (!row.audioOnly) expect(titleSize(work, h).toFixed(1) + 'px').toBe(row.fs)
  })

  it.each(TABLE)('wears the ink, the gilt and the band of the design, and leans where it leans: $title', (row) => {
    const colors = spineColors(brief(row))
    expect(colors).toEqual({ ink: row.ink, gold: row.gold, band: row.band })
    expect(leans(brief(row))).toBe(row.tilt)
  })
})

describe('the numbers behind the table', () => {
  const base: WorkBrief = brief(TABLE[0]!)

  it('makes a recording alone a case of 26 by 166, and a book as wide as its pages over 12, between 18 and 54', () => {
    expect(spineDims({ ...base, audio_only: true })).toEqual({ t: 26, h: 166 })
    expect(spineDims({ ...base, pages: 0 }).t).toBe(18)
    expect(spineDims({ ...base, pages: 215 }).t).toBe(18)
    expect(spineDims({ ...base, pages: 216 }).t).toBe(18)
    expect(spineDims({ ...base, pages: 400 }).t).toBe(33)
    expect(spineDims({ ...base, pages: 648 }).t).toBe(54)
    expect(spineDims({ ...base, pages: 5000 }).t).toBe(54)
  })

  it('is 158 high for the lowest, 216 for the tallest, and a book for children 14 lower', () => {
    expect(spineDims({ ...base, tall: 0 }).h).toBe(158)
    expect(spineDims({ ...base, tall: 1 }).h).toBe(216)
    expect(spineDims({ ...base, tall: 0.5, genre: 'kinder' }).h).toBe(173)
    expect(spineDims({ ...base, tall: 0.5, genre: 'roman' }).h).toBe(187)
  })

  it('keeps the letters between 10.5 and 15 pixels, and gives a crime novel less room', () => {
    expect(titleSize({ title: 'A', genre: 'roman' }, 200)).toBe(15)
    expect(titleSize({ title: 'x'.repeat(200), genre: 'roman' }, 200)).toBe(10.5)
    expect(titleSize({ title: 'x'.repeat(20), genre: 'roman' }, 190)).toBeGreaterThan(titleSize({ title: 'x'.repeat(20), genre: 'krimi' }, 190))
  })

  it('computes the size between the limits exactly as the design does: (height minus 78, or 110 for a crime novel) over 0.52 a letter', () => {
    // Values in the open range, where no limit hides a wrong constant.
    expect(titleSize({ title: 'x'.repeat(10), genre: 'roman' }, 140)).toBeCloseTo((140 - 78) / (10 * 0.52), 10)
    expect(titleSize({ title: 'x'.repeat(10), genre: 'krimi' }, 181)).toBeCloseTo((181 - 110) / (10 * 0.52), 10)
    expect(titleSize({ title: 'x'.repeat(10), genre: 'krimi' }, 181)).toBeCloseTo(13.6538, 3)
    expect(titleSize({ title: 'x'.repeat(10), genre: 'roman' }, 140)).toBeCloseTo(11.9231, 3)
    expect(titleSize({ title: 'x'.repeat(12), genre: 'krimi' }, 200)).toBeCloseTo(14.423, 2)
  })

  it('gives the dark ink to a light book and the light ink to a dark one, at a brightness of 0.45', () => {
    expect(spineColors({ ...base, color: '#d9cba9' }).ink).toBe('#2a2420')
    expect(spineColors({ ...base, color: '#5e2027' }).ink).toBe('#efe6d2')
    expect(spineColors({ ...base, color: '#7c8c6b' }).ink).toBe('#2a2420')
  })

  it('names the class of the genre, and the plain look for a book with none', () => {
    for (const genre of ['roman', 'krimi', 'fantasy', 'scifi', 'klassiker', 'sachbuch', 'kinder', 'lyrik']) expect(genreClass(genre)).toBe(`s-${genre}`)
    expect(genreClass('')).toBe('s-none')
  })

  it('stands the last name of the first author at the foot, and nothing for nobody', () => {
    expect(footName({ authors: [{ id: 1, name: 'Mara von Lindqvist' }, { id: 2, name: 'B' }] })).toBe('Lindqvist')
    expect(footName({ authors: [] })).toBe('')
  })
})

describe('the room reserved for a section that is not drawn yet', () => {
  it('grows with the books, and a phone needs more rows of less height', async () => {
    const { sectionHeight } = await import('./shelf')
    expect(sectionHeight(1, 1440)).toBe(40 + 18 + 254)
    // 535 books of 34 px on a line of 1,288 px: 15 rows.
    expect(sectionHeight(535, 1440)).toBe(40 + 18 + 15 * 254)
    expect(sectionHeight(535, 390)).toBeGreaterThan(sectionHeight(535, 1440))
    expect(sectionHeight(0, 1440)).toBe(sectionHeight(1, 1440))
  })
})
