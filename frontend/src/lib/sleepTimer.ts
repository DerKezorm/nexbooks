/**
 * The sleep timer of the player: stops after a number of minutes of listening (5 to 120), or at the end of the
 * chapter, and lowers the sound gently before it stops.
 *
 * - **Minutes count only while the recording plays.** A pause stops the count; the time on the face is what is left to hear.
 * - **The end of the chapter** is the end of the chapter that is playing when the timer is set; a jump to another chapter (by the
 *   person) moves the end to that chapter's end. The chapter that follows on by itself is never the new end: the timer stops
 *   where the chapter ended.
 * - Nothing else extends it: no shaking, no tapping (the browser gives no motion sensors to a page without asking, and a page
 *   should not ask for that).
 *
 * The clock is put in (`now`), so that the tests run the time forward without waiting.
 */

export const SLEEP_MINUTES = [5, 10, 15, 30, 45, 60, 90, 120]
export const SLEEP_MIN = 5
export const SLEEP_MAX = 120
/** How many seconds before the end the sound goes down, for minutes and for the end of a chapter. */
export const FADE_SECONDS = 10

export type SleepState =
  | { kind: 'off' }
  | { kind: 'minutes'; minutes: number; left: number }
  | { kind: 'chapter'; end: number; left: number }

/** What a tick of the player tells the timer, and what it answers: the volume to play at, whether to stop now. */
export type Tick = { playing: boolean; position: number; speed: number }
export type Verdict = { volume: number; stop: boolean; state: SleepState }

export class SleepTimer {
  private mode: { kind: 'off' } | { kind: 'minutes'; minutes: number; heard: number } | { kind: 'chapter'; end: number } = { kind: 'off' }
  private last: number | null = null
  private lastLeft = 0

  constructor(private readonly now: () => number = () => performance.now()) {}

  /** Stops after `minutes` of listening, 5 to 120 (anything else is held to that). */
  minutes(minutes: number): void {
    const held = Math.min(SLEEP_MAX, Math.max(SLEEP_MIN, Math.round(Number.isFinite(minutes) ? minutes : SLEEP_MIN)))
    this.mode = { kind: 'minutes', minutes: held, heard: 0 }
    this.last = null
    this.lastLeft = held * 60
  }

  /** Stops at `end`, the end of the chapter that plays now (seconds on the line of the recording). */
  chapterEnd(end: number): void {
    this.mode = { kind: 'chapter', end }
    this.last = null
  }

  /** The person jumped: at the end of a chapter, the timer now stops at the end of the chapter the jump led to. */
  jumped(chapterEnd: number): void {
    if (this.mode.kind === 'chapter') this.mode = { kind: 'chapter', end: chapterEnd }
  }

  cancel(): void {
    this.mode = { kind: 'off' }
    this.last = null
  }

  get active(): boolean {
    return this.mode.kind !== 'off'
  }

  /** What is left now, without counting anything (for the face, while nothing plays). */
  state(position = 0, speed = 1): SleepState {
    if (this.mode.kind === 'minutes') return { kind: 'minutes', minutes: this.mode.minutes, left: Math.max(0, this.mode.minutes * 60 - this.mode.heard) }
    if (this.mode.kind === 'chapter') return { kind: 'chapter', end: this.mode.end, left: Math.max(0, (this.mode.end - position) / Math.max(speed, 0.01)) }
    return { kind: 'off' }
  }

  /**
   * Called a few times a second by the player. Counts the time that played since the last call, and says at what volume to
   * play (1 until the last `FADE_SECONDS`, then down to almost nothing) and whether to stop. After a stop the timer is off.
   */
  tick({ playing, position, speed }: Tick): Verdict {
    const at = this.now()
    const since = this.last === null ? 0 : Math.max(0, (at - this.last) / 1000)
    this.last = at
    if (this.mode.kind === 'off') return { volume: 1, stop: false, state: { kind: 'off' } }
    let left: number
    if (this.mode.kind === 'minutes') {
      if (playing) this.mode.heard += since
      left = this.mode.minutes * 60 - this.mode.heard
    } else {
      // The chapter that follows by itself does not become the new end: only a jump of the person moves it (`jumped`).
      left = (this.mode.end - position) / Math.max(speed, 0.01)
    }
    this.lastLeft = Math.max(0, left)
    if (left <= 0.05) {
      const state = this.state(position, speed)
      this.cancel()
      return { volume: 1, stop: true, state }
    }
    const volume = left >= FADE_SECONDS ? 1 : Math.max(0.05, left / FADE_SECONDS)
    return { volume, stop: false, state: this.state(position, speed) }
  }

  /** Seconds left at the last tick. */
  get left(): number {
    return this.lastLeft
  }
}
