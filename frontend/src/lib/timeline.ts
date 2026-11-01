/**
 * The line of time of a recording. A recording may be one file (an M4B with chapters inside) or many (MP3 parts, the
 * folders of a CD): the player lays the parts end to end and speaks of one place in seconds from the beginning of all of them.
 * Every jump, every chapter and every place that is saved is a time on this line; which file holds it, and where in that file,
 * is worked out here.
 *
 * The lengths are the ones the scanner measured (`FileOut.duration`); the chapters of the server already start where the parts
 * before them end (`backend/app/services/catalog.py`). A part a browser finds a little longer or shorter than measured changes
 * nothing of the line: the time inside a part is held to the part.
 */

/** One file of the recording on the line: where it starts and how long it is, in seconds. */
export type Part = { id: number; format: string; size: number; start: number; duration: number }
/** A chapter on the line: from `start` to `end` (the start of the next, or the end of the recording). */
export type Span = { number: number; title: string; start: number; end: number }

type FileLike = { id: number; format: string; size: number; part: number; duration: number }
type ChapterLike = { number: number; title: string; start: number }

/** The server counts in whole milliseconds (`start_ms`, `duration_ms`); so does the line, or its sums drift from the server's. */
const millis = (seconds: number): number => Math.round(seconds * 1000)

/** How near a time may be to the start of a part to belong to it: far below a millisecond, far above the error of a float. */
const BORDER = 0.0005

/**
 * The parts in the order of their number, each starting where the one before it ends. The lengths are added up in whole
 * milliseconds and divided once, so a start is exactly the number the server's `start_ms / 1000` gives (floating point sums of
 * lengths such as 2.99 s drift in the last bits, and a chapter that begins at a border would land at the end of the part before).
 */
export function partsOf(files: readonly FileLike[]): Part[] {
  let startMs = 0
  return [...files]
    .sort((a, b) => a.part - b.part || a.id - b.id)
    .map((file) => {
      const durationMs = Math.max(0, Number.isFinite(file.duration) ? millis(file.duration) : 0)
      const part = { id: file.id, format: file.format, size: file.size, start: startMs / 1000, duration: durationMs / 1000 }
      startMs += durationMs
      return part
    })
}

export function totalOf(parts: readonly Part[]): number {
  const last = parts[parts.length - 1]
  return last ? millis(last.start + last.duration) / 1000 : 0
}

/** A time held to the line: never before its start, never past its end, never not a number. */
export function clampTime(seconds: number, total: number): number {
  if (!Number.isFinite(seconds)) return 0
  return Math.min(Math.max(seconds, 0), Math.max(total, 0))
}

/**
 * Which part holds a time, and where in it. A time on the border of two parts is the beginning of the later one (the earlier
 * one has ended there), also when the two numbers differ by the error of a float; the end of the recording is the end of the
 * last part.
 */
export function locate(parts: readonly Part[], seconds: number): { index: number; offset: number } {
  if (parts.length === 0) return { index: -1, offset: 0 }
  const time = clampTime(seconds, totalOf(parts))
  for (let index = parts.length - 1; index >= 0; index--) {
    const part = parts[index]
    if (time < part.start - BORDER) continue
    // A part of no length (the scanner could not measure it) is never the place: it is stepped over.
    if (part.duration === 0 && index < parts.length - 1) continue
    return { index, offset: Math.min(Math.max(time - part.start, 0), part.duration) }
  }
  return { index: 0, offset: 0 }
}

/** How close to the end of a part a jump may not aim: there the part is over before the browser has it. */
export const END_GUARD = 0.05

/**
 * Where to point an element for a time: like `locate`, but never at the end of a part that has a successor (a file that is
 * asked for its last moments ends at once, and some browsers repeat or stall there); that time is the beginning of the next.
 */
export function aim(parts: readonly Part[], seconds: number): { index: number; offset: number; time: number } {
  let { index, offset } = locate(parts, seconds)
  if (index < 0) return { index, offset, time: 0 }
  while (index < parts.length - 1 && offset >= parts[index].duration - END_GUARD) {
    index += 1
    offset = 0
  }
  return { index, offset, time: timeOf(parts, index, offset) }
}

/** The time on the line of a place inside a part. */
export function timeOf(parts: readonly Part[], index: number, offset: number): number {
  const part = parts[index]
  if (!part) return 0
  return part.start + Math.min(Math.max(offset, 0), part.duration)
}

/**
 * The chapters as spans on the line, in order. A recording without chapters is one chapter with the title given; a chapter
 * that starts past the end, or at the same time as the one before, is left out (it could not be reached).
 */
export function spansOf(chapters: readonly ChapterLike[], total: number, fallbackTitle: string): Span[] {
  const sorted = [...chapters].filter((chapter) => Number.isFinite(chapter.start)).sort((a, b) => a.start - b.start || a.number - b.number)
  const kept: ChapterLike[] = []
  for (const chapter of sorted) {
    const start = Math.max(0, chapter.start)
    if (total > 0 && start >= total) continue
    if (kept.length && start - kept[kept.length - 1].start < 0.001) continue
    kept.push({ ...chapter, start })
  }
  if (kept.length === 0) return [{ number: 1, title: fallbackTitle, start: 0, end: total }]
  // Whatever lies before the first chapter belongs to it.
  kept[0] = { ...kept[0], start: 0 }
  return kept.map((chapter, index) => ({
    number: index + 1,
    title: chapter.title || fallbackTitle,
    start: chapter.start,
    end: index + 1 < kept.length ? kept[index + 1].start : Math.max(total, chapter.start),
  }))
}

/** The index of the chapter a time is in. */
export function chapterAt(spans: readonly Span[], seconds: number): number {
  if (spans.length === 0) return -1
  // A hair before a chapter's start counts as the chapter already: a jump to it lands there and a player may stop a little short.
  const time = seconds + 0.05
  for (let index = spans.length - 1; index >= 0; index--) if (time >= spans[index].start) return index
  return 0
}

/**
 * Where the "back to the chapter" button leads: the start of this chapter, or, within the first few seconds of it, the start
 * of the one before (the way every player does it).
 */
export function previousChapterStart(spans: readonly Span[], seconds: number, grace = 3): number {
  const index = chapterAt(spans, seconds)
  if (index < 0) return 0
  if (seconds - spans[index].start > grace || index === 0) return spans[index].start
  return spans[index - 1].start
}

export function nextChapterStart(spans: readonly Span[], seconds: number): number | null {
  const index = chapterAt(spans, seconds)
  return index >= 0 && index + 1 < spans.length ? spans[index + 1].start : null
}

// ---- Speed ---------------------------------------------------------------------------------------------------------------

export const SPEED_MIN = 0.5
export const SPEED_MAX = 3
export const SPEED_STEP = 0.05
export const SPEED_PRESETS = [1, 1.25, 1.5, 2]

/** A speed the player offers: 0.5 to 3.0, in steps of 0.05. */
export function clampSpeed(speed: number): number {
  if (!Number.isFinite(speed)) return 1
  const stepped = Math.round(speed / SPEED_STEP) * SPEED_STEP
  return Math.round(Math.min(SPEED_MAX, Math.max(SPEED_MIN, stepped)) * 100) / 100
}

/** The profile keeps the speed in hundredths (a whole number): 1.25 is 125. */
export const speedFromProfile = (value: unknown): number => (typeof value === 'number' ? clampSpeed(value / 100) : 1)
export const speedToProfile = (speed: number): number => Math.round(clampSpeed(speed) * 100)

/** How long a stretch of the recording takes to hear at a speed (what "noch 2 Std" says at 1.5×). */
export function heardIn(seconds: number, speed: number): number {
  return Math.max(0, seconds) / clampSpeed(speed)
}

// ---- Places as text --------------------------------------------------------------------------------------------------------

/** The place in a recording as the server keeps it. */
export function timeLocator(seconds: number): string {
  return `time:${Math.max(0, seconds).toFixed(3)}`
}

/** The seconds of a place in a recording, null for anything else. */
export function secondsOf(locator: string | null | undefined): number | null {
  const found = /^time:(\d{1,7})(?:\.(\d{1,3}))?$/.exec(locator ?? '')
  if (!found) return null
  return Number(found[1]) + Number((found[2] ?? '0').padEnd(3, '0')) / 1000
}

/** "1:02:03" or "12:03": a time on the face of a player. Never negative, never a fraction of a second. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0))
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = whole % 60
  const two = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`
}

/**
 * Where to start again after a pause: ten seconds back when the pause was long (`after`, five minutes), so that the sentence
 * that was cut off is heard again; never before the beginning. Only this device's clock is read.
 */
export function resumeFrom(seconds: number, pausedFor: number | null, enabled: boolean, after = 5 * 60_000, back = 10): number {
  if (!enabled || pausedFor === null || pausedFor < after) return seconds
  return Math.max(0, seconds - back)
}
