import {
  aim,
  chapterAt,
  clampSpeed,
  clock,
  heardIn,
  locate,
  nextChapterStart,
  partsOf,
  previousChapterStart,
  resumeFrom,
  secondsOf,
  spansOf,
  speedFromProfile,
  speedToProfile,
  timeLocator,
  timeOf,
  totalOf,
} from './timeline'

const file = (id: number, part: number, duration: number) => ({ id, format: 'mp3', size: 1000, part, duration })

describe('the parts of a recording on one line of time', () => {
  const parts = partsOf([file(13, 3, 30), file(11, 1, 20), file(12, 2, 25.5)])

  it('lies the parts end to end in the order of their number', () => {
    expect(parts.map((part) => [part.id, part.start, part.duration])).toEqual([
      [11, 0, 20],
      [12, 20, 25.5],
      [13, 45.5, 30],
    ])
    expect(totalOf(parts)).toBe(75.5)
    expect(totalOf([])).toBe(0)
  })

  it('finds the part and the place in it for any time, the border being the start of the later part', () => {
    expect(locate(parts, 0)).toEqual({ index: 0, offset: 0 })
    expect(locate(parts, 19.99).index).toBe(0)
    expect(locate(parts, 20)).toEqual({ index: 1, offset: 0 })
    expect(locate(parts, 30)).toEqual({ index: 1, offset: 10 })
    expect(locate(parts, 45.5)).toEqual({ index: 2, offset: 0 })
    expect(locate(parts, 75.5)).toEqual({ index: 2, offset: 30 })
  })

  it('holds a time to the line: nothing before the start, nothing past the end, nothing that is not a number', () => {
    expect(locate(parts, -5)).toEqual({ index: 0, offset: 0 })
    expect(locate(parts, 999)).toEqual({ index: 2, offset: 30 })
    expect(locate(parts, Number.NaN)).toEqual({ index: 0, offset: 0 })
    expect(locate([], 3)).toEqual({ index: -1, offset: 0 })
  })

  it('turns a place in a part back into a time, held to the part', () => {
    expect(timeOf(parts, 1, 5)).toBe(25)
    expect(timeOf(parts, 2, 99)).toBe(75.5)
    expect(timeOf(parts, 0, -1)).toBe(0)
    expect(timeOf(parts, 7, 1)).toBe(0)
  })

  it('steps over a part the scanner could not measure', () => {
    const odd = partsOf([file(1, 1, 10), file(2, 2, 0), file(3, 3, 10)])
    expect(locate(odd, 10)).toEqual({ index: 2, offset: 0 })
  })

  it('takes a jump forward and back across a border as a time of the other part (round trip)', () => {
    for (const time of [0, 3.25, 19.5, 20, 44.9, 45.5, 60, 75.5]) {
      const { index, offset } = locate(parts, time)
      expect(timeOf(parts, index, offset)).toBeCloseTo(time, 6)
    }
  })
})

describe('chapters', () => {
  const chapters = [
    { number: 2, title: 'The Pier', start: 600 },
    { number: 1, title: 'Arrival', start: 0 },
    { number: 3, title: 'Night', start: 1500 },
  ]
  const spans = spansOf(chapters, 2400, 'Book')

  it('are spans from their start to the next start, the last to the end', () => {
    expect(spans).toEqual([
      { number: 1, title: 'Arrival', start: 0, end: 600 },
      { number: 2, title: 'The Pier', start: 600, end: 1500 },
      { number: 3, title: 'Night', start: 1500, end: 2400 },
    ])
  })

  it('without any, the recording is one chapter with the title of the book', () => {
    expect(spansOf([], 300, 'Book')).toEqual([{ number: 1, title: 'Book', start: 0, end: 300 }])
  })

  it('leave out what cannot be reached and give the first one what lies before it', () => {
    const odd = spansOf([
      { number: 1, title: 'A', start: 5 },
      { number: 2, title: 'B', start: 5 },
      { number: 3, title: '', start: 100 },
      { number: 4, title: 'Past', start: 400 },
    ], 300, 'Book')
    expect(odd.map((span) => [span.title, span.start, span.end])).toEqual([
      ['A', 0, 100],
      ['Book', 100, 300],
    ])
  })

  it('know which one a time is in, a hair before a start counting as the next', () => {
    expect(chapterAt(spans, 0)).toBe(0)
    expect(chapterAt(spans, 599.9)).toBe(0)
    expect(chapterAt(spans, 599.97)).toBe(1)
    expect(chapterAt(spans, 1500)).toBe(2)
    expect(chapterAt(spans, 2400)).toBe(2)
    expect(chapterAt([], 3)).toBe(-1)
  })

  it('lead back to the start of the chapter, or to the one before within its first seconds', () => {
    expect(previousChapterStart(spans, 900)).toBe(600)
    expect(previousChapterStart(spans, 602)).toBe(0)
    expect(previousChapterStart(spans, 1)).toBe(0)
    expect(nextChapterStart(spans, 900)).toBe(1500)
    expect(nextChapterStart(spans, 2000)).toBeNull()
  })
})

describe('chapters that run over several parts', () => {
  // Six parts of 6 s, three chapters: "Opening" over two parts, "Crossing" over three, "Finale" in one (the server puts
  // the files of one chapter together and starts the chapter in the first of them).
  const parts = partsOf([1, 2, 3, 4, 5, 6].map((n) => file(20 + n, n, 6)))
  const spans = spansOf(
    [
      { number: 1, title: 'Opening', start: 0 },
      { number: 2, title: 'Crossing', start: 12 },
      { number: 3, title: 'Finale', start: 30 },
    ],
    totalOf(parts),
    'Book',
  )

  it('hold the borders of the parts inside them: a time in the second part is still the first chapter', () => {
    expect(spans.map((span) => [span.title, span.start, span.end])).toEqual([
      ['Opening', 0, 12],
      ['Crossing', 12, 30],
      ['Finale', 30, 36],
    ])
    expect(chapterAt(spans, 8)).toBe(0)
    expect(locate(parts, 8).index).toBe(1)
    expect(chapterAt(spans, 23)).toBe(1)
    expect(locate(parts, 23).index).toBe(3)
  })

  it('lead ahead and back from chapter to chapter, across any number of parts', () => {
    expect(nextChapterStart(spans, 8)).toBe(12)
    expect(nextChapterStart(spans, 14)).toBe(30)
    expect(previousChapterStart(spans, 25)).toBe(12)
    expect(previousChapterStart(spans, 31)).toBe(12)
    expect(previousChapterStart(spans, 9)).toBe(0)
    expect(locate(parts, nextChapterStart(spans, 14)!).index).toBe(5)
  })
})

describe('the speed', () => {
  it('lies between 0.5 and 3.0 in steps of 0.05', () => {
    expect(clampSpeed(1.27)).toBe(1.25)
    expect(clampSpeed(1.28)).toBe(1.3)
    expect(clampSpeed(0.1)).toBe(0.5)
    expect(clampSpeed(9)).toBe(3)
    expect(clampSpeed(Number.NaN)).toBe(1)
  })

  it('is kept in the profile as hundredths', () => {
    expect(speedToProfile(1.35)).toBe(135)
    expect(speedFromProfile(135)).toBe(1.35)
    expect(speedFromProfile(undefined)).toBe(1)
    expect(speedFromProfile(12)).toBe(0.5)
  })

  it('makes the time left shorter as it says on the face', () => {
    expect(heardIn(3600, 1.5)).toBe(2400)
    expect(heardIn(3600, 2)).toBe(1800)
    expect(heardIn(-5, 1)).toBe(0)
  })
})

describe('a place as text', () => {
  it('is a time with three places after the point, and back', () => {
    expect(timeLocator(8040.5)).toBe('time:8040.500')
    expect(timeLocator(-3)).toBe('time:0.000')
    expect(secondsOf('time:8040.500')).toBe(8040.5)
    expect(secondsOf('time:12')).toBe(12)
    expect(secondsOf('time:1.5')).toBe(1.5)
    for (const wrong of ['epubcfi(/6/4)', 'time:', 'time:-1', 'time:1.2345', 'time:1e3', null, undefined]) expect(secondsOf(wrong)).toBeNull()
  })

  it('is shown as minutes and seconds, with hours when there are any', () => {
    expect(clock(0)).toBe('0:00')
    expect(clock(65.9)).toBe('1:05')
    expect(clock(3723)).toBe('1:02:03')
    expect(clock(-4)).toBe('0:00')
    expect(clock(Number.NaN)).toBe('0:00')
  })
})

describe('going on after a pause', () => {
  it('goes back ten seconds after a pause of five minutes or more, if the person wants it', () => {
    expect(resumeFrom(100, 5 * 60_000, true)).toBe(90)
    expect(resumeFrom(100, 5 * 60_000 - 1, true)).toBe(100)
    expect(resumeFrom(100, 3_600_000, false)).toBe(100)
    expect(resumeFrom(100, null, true)).toBe(100)
    expect(resumeFrom(4, 3_600_000, true)).toBe(0)
  })
})

// The server counts the start of every chapter in whole milliseconds (`start_ms / 1000`); the line must agree with it to the bit,
// or a chapter that begins where a file begins is put at the very end of the file before (and a browser repeats that file).
describe('a chapter that begins where a part begins', () => {
  const recording = (count: number, ms: number) => {
    const files = Array.from({ length: count }, (_, index) => ({ id: index + 1, format: 'mp3', size: 1, part: index + 1, duration: ms / 1000 }))
    // What the server sends: the start of the chapter of part n is n * ms (whole milliseconds) over a thousand.
    const starts = Array.from({ length: count }, (_, index) => (index * ms) / 1000)
    return { parts: partsOf(files), starts }
  }

  it.each([
    [200, 2990],
    [80, 1300],
    [57, 7010],
    [333, 1110],
  ])('puts each of %i chapters of %i ms at the beginning of its own part', (count, ms) => {
    const { parts, starts } = recording(count, ms)
    const wrong = starts.map((start, index) => (locate(parts, start).index === index && locate(parts, start).offset === 0 ? -1 : index)).filter((index) => index >= 0)
    expect(wrong).toEqual([])
    expect(parts.map((part, index) => part.start === starts[index]).every(Boolean)).toBe(true)
    expect(totalOf(parts)).toBe((count * ms) / 1000)
  })

  it('takes a time a hair before a border for the later part, and never aims at the end of a part that has a successor', () => {
    const { parts } = recording(5, 2990)
    expect(locate(parts, 2.99 - 0.0001)).toEqual({ index: 1, offset: 0 })
    expect(locate(parts, 2.99 - 0.002).index).toBe(0)
    expect(aim(parts, 2.99 - 0.02)).toEqual({ index: 1, offset: 0, time: 2.99 })
    expect(aim(parts, 2.99 - 0.2).index).toBe(0)
    // The end of the recording is the end of the last part, not a part past it.
    expect(aim(parts, 99)).toMatchObject({ index: 4, time: 14.95 })
    expect(aim(parts, 99).offset).toBeCloseTo(2.99, 9)
    expect(aim([], 3)).toMatchObject({ index: -1, time: 0 })
  })
})
