/** How long ago and how long until a moment is said, in the language of the page. */

import { ago, daysAgo, inTime, timeIn, whenText } from './ago'

const NOW = new Date('2026-10-16T12:00:00Z').getTime()

describe('ago', () => {
  it('says just now under a minute, then minutes, hours and days', () => {
    expect(ago('2026-10-16T11:59:30Z', 'de', 'gerade eben', NOW)).toBe('gerade eben')
    expect(ago('2026-10-16T11:56:00Z', 'de', 'gerade eben', NOW)).toBe('vor 4 Minuten')
    expect(ago('2026-10-16T10:00:00Z', 'en', 'just now', NOW)).toBe('2 hours ago')
    expect(ago('2026-10-13T12:00:00Z', 'de', 'gerade eben', NOW)).toBe('vor 3 Tagen')
  })

  it('counts whole days', () => {
    expect(daysAgo('2026-10-16T01:00:00Z', 'de', NOW)).toBe('heute')
  })
})

describe('inTime', () => {
  it('says now for a moment that is here or past, then minutes, hours and days ahead', () => {
    expect(inTime('2026-10-16T11:00:00Z', 'de', 'jetzt', NOW)).toBe('jetzt')
    expect(inTime('2026-10-16T12:00:30Z', 'de', 'jetzt', NOW)).toBe('jetzt')
    expect(inTime('2026-10-16T12:11:00Z', 'de', 'jetzt', NOW)).toBe('in 11 Minuten')
    expect(inTime('2026-10-16T15:00:00Z', 'en', 'now', NOW)).toBe('in 3 hours')
    expect(inTime('2026-10-17T12:00:00Z', 'de', 'jetzt', NOW)).toBe('morgen')
    expect(inTime('2026-10-20T12:00:00Z', 'de', 'jetzt', NOW)).toBe('in 4 Tagen')
  })

  it('does not throw on a text that is no moment', () => {
    expect(inTime('nonsense', 'de', 'jetzt', NOW)).toBe('jetzt')
  })

  it('rounds the minutes up and the hours and the days to the nearest', () => {
    // 59 seconds past the minute: still a minute ahead, never "now".
    expect(inTime('2026-10-16T12:01:01Z', 'en', 'now', NOW)).toBe('in 2 minutes')
    expect(inTime('2026-10-16T12:00:59Z', 'en', 'now', NOW)).toBe('now')
    // An hour and 29 minutes is one hour, an hour and 31 minutes is two.
    expect(inTime('2026-10-16T13:29:00Z', 'en', 'now', NOW)).toBe('in 1 hour')
    expect(inTime('2026-10-16T13:31:00Z', 'en', 'now', NOW)).toBe('in 2 hours')
    // A day and 11 hours is one day, a day and 13 hours is two.
    expect(inTime('2026-10-17T23:00:00Z', 'en', 'now', NOW)).toBe('tomorrow')
    expect(inTime('2026-10-18T01:00:00Z', 'en', 'now', NOW)).toBe('in 2 days')
  })
})

describe('timeIn', () => {
  it('gives the clock time in the zone it is given and never a day with it', () => {
    const midnight = '2026-10-16T22:00:00Z'
    expect(timeIn(midnight, 'de', 'Europe/Berlin')).toBe('00:00')
    expect(timeIn(midnight, 'de', 'Pacific/Auckland')).toBe('11:00')
    expect(timeIn(midnight, 'de', 'Not/AZone')).toMatch(/^\d{2}:\d{2}$/)
    expect(timeIn('nonsense', 'de', 'Europe/Berlin')).toBe('')
  })
})

describe('whenText', () => {
  it('gives the time of a moment today and the day with it on another day', () => {
    const today = new Date(NOW + 2 * 3_600_000).toISOString()
    const later = new Date(NOW + 30 * 3_600_000).toISOString()
    expect(whenText(today, 'de', NOW)).toMatch(/^\d{2}:\d{2}$/)
    expect(whenText(later, 'de', NOW)).toMatch(/^\p{L}+\.?,? \d{2}:\d{2}$/u)
    expect(whenText('nonsense', 'de', NOW)).toBe('')
  })

  it("says it in the time zone it is given, and in the browser's own when that is none the browser knows", () => {
    const moment = '2026-10-16T22:00:00Z'
    // 22:00 UTC is 11:00 the next morning in Auckland, the same day there as NOW (01:00): no day named.
    expect(whenText(moment, 'de', NOW, 'Pacific/Auckland')).toBe('11:00')
    expect(whenText(moment, 'de', NOW, 'Europe/Berlin')).toMatch(/^\p{L}+\.?,? 00:00$/u)
    expect(whenText(moment, 'de', NOW, 'Not/AZone')).toBe(whenText(moment, 'de', NOW))
    // The day is told apart in that zone: 22:00 UTC is the next morning in Auckland, so it is not "today" there.
    expect(whenText('2026-10-16T22:00:00Z', 'de', new Date('2026-10-16T21:00:00Z').getTime(), 'Pacific/Auckland')).toMatch(/^\d{2}:\d{2}$/)
  })
})
