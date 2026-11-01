/**
 * Which recordings this browser plays. nexbooks plays the files as they lie in the library and changes none of
 * them on the server, so a format the browser does not know cannot be heard here: the player says so, plainly, and offers the
 * file for download (an app on the device plays it).
 *
 * Measured in Playwright's builds under Windows: Chromium and Firefox play MP3, M4A/M4B (AAC), FLAC, Ogg Vorbis
 * and Opus; WebKit plays MP3 and M4A/M4B, says nothing for Ogg, and says "probably" for FLAC but never loads it. A browser's
 * answer is a first look only: the player also gives up on a file that does not load in time (`LOAD_TIMEOUT_MS`).
 */

/** What a browser is asked (`canPlayType`): the type and, where it matters, the codec inside. */
export const PROBE: Record<string, string> = {
  mp3: 'audio/mpeg',
  m4b: 'audio/mp4; codecs="mp4a.40.2"',
  m4a: 'audio/mp4; codecs="mp4a.40.2"',
  flac: 'audio/flac',
  ogg: 'audio/ogg; codecs="vorbis"',
  opus: 'audio/ogg; codecs="opus"',
}

/** How long a part may take to say how long it is before the player gives up on it and says so. */
export const LOAD_TIMEOUT_MS = 20_000

/** The formats among `formats` this browser says it cannot play at all (an empty answer). */
export function unplayable(formats: readonly string[], probe: (type: string) => string): string[] {
  const out: string[] = []
  for (const format of formats) {
    const type = PROBE[format.toLowerCase()]
    if (!type || probe(type) === '') {
      if (!out.includes(format)) out.push(format)
    }
  }
  return out
}

/** A probe with the browser's own audio element; a browser without one plays nothing. */
export function browserProbe(): (type: string) => string {
  try {
    const element = document.createElement('audio')
    return (type) => element.canPlayType(type)
  } catch {
    return () => ''
  }
}

/** Why a part could not be played, as the player says it. */
export type PlayFailure = 'unsupported' | 'stalled' | 'network' | 'broken' | 'browser'

/**
 * Whether this is WebKit (Safari, and every browser on an iPhone or iPad): it says "not supported" (code 4) for a file it
 * cannot fetch as well as for one it cannot decode, so it never tells a damaged file from a refused request (a build that loads
 * media without the cookies of the page, a file that is gone). It is not told "damaged" then.
 */
export function isWebKit(agent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent): boolean {
  if (/iPhone|iPad|iPod/.test(agent)) return true
  return /AppleWebKit/.test(agent) && /Safari\//.test(agent) && !/Chrome|Chromium|Edg\/|Android|Firefox/.test(agent)
}

/** The reason of an `error` event of an audio element (its `MediaError.code`). */
export function failureOf(code: number | undefined, supported: boolean, webkit: boolean = isWebKit()): PlayFailure {
  if (code === 2) return 'network'
  if (code === 4) return supported ? (webkit ? 'browser' : 'broken') : 'unsupported'
  return 'broken'
}
