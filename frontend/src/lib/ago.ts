/**
 * "gerade eben", "vor 2 Stunden", "gestern", "vor 5 Tagen": how long ago a moment was, in the language of the page.
 * `now` comes in from outside so that a test sets the clock.
 */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

export function ago(moment: string, language: string, justNow: string, now: number = Date.now()): string {
  const passed = now - new Date(moment).getTime()
  if (!Number.isFinite(passed) || passed < MINUTE) return justNow
  const say = new Intl.RelativeTimeFormat(language, { numeric: 'auto' })
  if (passed < HOUR) return say.format(-Math.floor(passed / MINUTE), 'minute')
  if (passed < DAY) return say.format(-Math.floor(passed / HOUR), 'hour')
  return say.format(-Math.floor(passed / DAY), 'day')
}

/** "heute", "gestern", "vor 3 Tagen": the day of a moment, counted in whole days. */
export function daysAgo(moment: string, language: string, now: number = Date.now()): string {
  const start = (time: number) => {
    const day = new Date(time)
    return new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime()
  }
  const days = Math.round((start(now) - start(new Date(moment).getTime())) / DAY)
  return new Intl.RelativeTimeFormat(language, { numeric: 'auto' }).format(-Math.max(0, days), 'day')
}

/** "in 11 Minuten", "in 2 Stunden", "morgen": how long until a moment, in the language of the page; `now` when it has come. */
export function inTime(moment: string, language: string, now: string, current: number = Date.now()): string {
  const left = new Date(moment).getTime() - current
  if (!Number.isFinite(left) || left < MINUTE) return now
  const say = new Intl.RelativeTimeFormat(language, { numeric: 'auto' })
  if (left < HOUR) return say.format(Math.ceil(left / MINUTE), 'minute')
  if (left < DAY) return say.format(Math.round(left / HOUR), 'hour')
  return say.format(Math.round(left / DAY), 'day')
}

/** The name of a time zone when the browser knows it, else nothing (the browser's own zone then). */
function knownZone(zone: string | undefined): string | undefined {
  if (!zone) return undefined
  try {
    new Intl.DateTimeFormat('en', { timeZone: zone })
    return zone
  } catch {
    return undefined
  }
}

/**
 * "14:30" for a moment today, "Fr., 14:30" for another day: when something goes on, in the language of the page. `zone`
 * is the time zone to say it in (the instance's, for a limit of the day that ends at its midnight); the browser's own
 * by default.
 */
export function whenText(moment: string, language: string, current: number = Date.now(), zone?: string): string {
  const at = new Date(moment)
  if (!Number.isFinite(at.getTime())) return ''
  const timeZone = knownZone(zone)
  const day = (time: Date) => time.toLocaleDateString('en-CA', { timeZone })
  const same = day(at) === day(new Date(current))
  return at.toLocaleString(language, { ...(same ? {} : { weekday: 'short' }), hour: '2-digit', minute: '2-digit', timeZone })
}

/**
 * "00:00": the clock time of a moment in a named time zone, without a day (the zone of the instance for a limit that ends at
 * its midnight: it says "at 00:00", never "at Sun, 00:00"). Nothing for a moment that is no time.
 */
export function timeIn(moment: string, language: string, zone: string | undefined): string {
  const at = new Date(moment)
  if (!Number.isFinite(at.getTime())) return ''
  return at.toLocaleTimeString(language, { hour: '2-digit', minute: '2-digit', timeZone: knownZone(zone) })
}
