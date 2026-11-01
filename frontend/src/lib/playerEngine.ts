/**
 * The engine of the player: two audio elements that play the parts of a recording as one line of time.
 *
 * - One element plays; the other loads the next part shortly before the playing one ends (`PRELOAD_SECONDS`), so that the next
 *   part starts the moment the last one ends (`ended`), without a gap to load in.
 * - A jump inside the playing part moves that element; a jump into another part points an element at that part and moves it
 *   once the browser knows the part (`loadedmetadata`; Safari forgets a place set before that). Jumps that follow one another
 *   quickly leave the last one standing.
 * - Nothing is changed on the server: the files are played as they lie in the library, with `Range`
 *   (`GET /api/v1/files/{id}/content`).
 *
 * The elements are put in (`MediaLike`), so that the tests play a recording without a browser that plays sound.
 */
import { failureOf, LOAD_TIMEOUT_MS, type PlayFailure } from './audioFormats'
import { aim, clampTime, timeOf, totalOf, type Part } from './timeline'

/** The part of an audio element the engine uses. */
export type MediaLike = {
  src: string
  currentTime: number
  readonly duration: number
  readonly paused: boolean
  readonly ended: boolean
  playbackRate: number
  defaultPlaybackRate: number
  volume: number
  preload: string
  readonly error: { code: number } | null
  play(): Promise<void>
  pause(): void
  load(): void
  removeAttribute(name: string): void
  addEventListener(type: string, listener: () => void): void
  removeEventListener(type: string, listener: () => void): void
}

/** How long before the end of a part the next one starts to load. */
export const PRELOAD_SECONDS = 45

export type EngineState = {
  /** Seconds on the line of the recording. */
  position: number
  playing: boolean
  /** The browser waits for data (a slow network, a jump far ahead). */
  waiting: boolean
  /** The end of the last part was reached. */
  finished: boolean
  failure: PlayFailure | null
  /** The part the failure is about (from 0), null when it is not about one part (the whole format, or no failure). */
  failedPart: number | null
  /** The part that plays, from 0. */
  part: number
}

type Options = {
  urlOf: (part: Part) => string
  /** Whether the browser said it can play a format at all (an `error` with code 4 is then a broken file, not a format). */
  supported: (part: Part) => boolean
  onChange: () => void
  /** The clock of the timeouts, put in by the tests. */
  setTimer?: (run: () => void, ms: number) => unknown
  clearTimer?: (timer: unknown) => void
}

type Slot = { media: MediaLike; part: number; ready: boolean }

export class PlayerEngine {
  private parts: Part[] = []
  private slots: [Slot, Slot]
  private active = 0
  /** The place to move to once the active element knows its part. */
  private pending: number | null = null
  private want = false
  private speed = 1
  private volume = 1
  private stateNow: EngineState = { position: 0, playing: false, waiting: false, finished: false, failure: null, failedPart: null, part: 0 }
  private loadTimer: unknown = null
  /** The part that failed while it loaded ahead: not asked for again until its turn comes (or a new place is chosen). */
  private failedAhead: number | null = null
  private readonly listeners: (() => void)[] = []
  private disposed = false

  constructor(a: MediaLike, b: MediaLike, private readonly options: Options) {
    this.slots = [
      { media: a, part: -1, ready: false },
      { media: b, part: -1, ready: false },
    ]
    for (const index of [0, 1] as const) this.listen(index)
  }

  // ---- What it plays ------------------------------------------------------------------------------------------------------

  /** A recording, opened at `seconds`, paused. */
  load(parts: Part[], seconds: number): void {
    this.stop()
    this.parts = parts
    this.want = false
    const { index, offset, time } = aim(parts, clampTime(seconds, totalOf(parts)))
    this.stateNow = { position: time, playing: false, waiting: false, finished: false, failure: null, failedPart: null, part: Math.max(index, 0) }
    if (index < 0) return
    this.point(this.active, index, offset)
    this.emit()
  }

  /** Stops and lets go of the files (the player is closed). */
  stop(): void {
    this.clearLoadTimer()
    for (const slot of this.slots) {
      // Marked first: what the element says while it lets go (an `emptied`, an `abort`) is no longer about a part.
      slot.part = -1
      slot.ready = false
      slot.media.pause()
      slot.media.removeAttribute('src')
      slot.media.load()
    }
    this.pending = null
    this.want = false
    this.parts = []
    this.stateNow = { ...this.stateNow, playing: false, waiting: false }
  }

  get state(): EngineState {
    return this.stateNow
  }

  get total(): number {
    return totalOf(this.parts)
  }

  /** Where it is now, read from the element (the state is only renewed on the element's events). */
  get position(): number {
    if (this.pending !== null) return this.stateNow.position
    const slot = this.slots[this.active]
    if (slot.part < 0 || !slot.ready) return this.stateNow.position
    return timeOf(this.parts, slot.part, slot.media.currentTime)
  }

  // ---- What the person does ------------------------------------------------------------------------------------------------

  play(): void {
    if (this.parts.length === 0) return
    this.want = true
    if (this.stateNow.finished) {
      this.seek(0)
      this.stateNow = { ...this.stateNow, finished: false }
    }
    if (this.stateNow.failure) return
    const slot = this.slots[this.active]
    if (slot.ready && this.pending === null) this.start(slot)
    this.stateNow = { ...this.stateNow, playing: true }
    this.emit()
  }

  /**
   * Called in the person's tap, before anything is fetched: a browser that wants a tap for sound (Safari) lets an element play
   * later only if `play` was called on it during a tap. The elements are started and stopped at once; they hold no part yet.
   */
  unlock(): void {
    for (const slot of this.slots) {
      if (slot.part >= 0) continue
      try {
        void slot.media.play()?.catch(() => undefined)
        slot.media.pause()
      } catch {
        // A browser that refuses it outright: the play button of the player is a tap of its own.
      }
    }
  }

  pause(): void {
    this.want = false
    for (const slot of this.slots) slot.media.pause()
    this.stateNow = { ...this.stateNow, playing: false, waiting: false, position: this.position }
    this.emit()
  }

  /** Moves to a time on the line (held to it). */
  seek(seconds: number): void {
    if (this.parts.length === 0) return
    // Never at the very end of a part that has a successor: that is the beginning of the next one.
    const { index, offset, time } = aim(this.parts, clampTime(seconds, this.total))
    const current = this.slots[this.active]
    const failed = this.stateNow.failure
    if (failed && failed !== 'network' && this.stateNow.failedPart === index) {
      // Still the part that does not play: only the place moves, the message stays (until another part is asked for).
      this.stateNow = { ...this.stateNow, position: time, finished: false, part: index }
      this.emit()
      return
    }
    if (failed) {
      // A jump to another part (or after a break of the network) is a new try, and the person wants to hear it.
      this.clearLoadTimer()
      this.want = true
      this.stateNow = { ...this.stateNow, failure: null, failedPart: null }
    }
    this.stateNow = { ...this.stateNow, position: time, finished: false, part: index }
    if (current.part === index && current.ready && this.pending === null && !failed) {
      current.media.currentTime = offset
    } else {
      const other = 1 - this.active
      const spare = this.slots[other]
      if (spare.part === index && spare.ready) {
        // The part was loaded already (the next one, or the last one played): no new request. The element that played is
        // stopped once it is no longer the active one, so that its `pause` is not taken for the person's.
        this.active = other
        current.media.pause()
        spare.media.currentTime = offset
        this.pending = null
        this.clearLoadTimer()
        if (this.want) this.start(spare)
      } else {
        // A new address stops the element by itself (without a `pause` of its own).
        this.point(this.active, index, offset)
      }
    }
    this.emit()
  }

  setSpeed(speed: number): void {
    this.speed = speed
    for (const slot of this.slots) {
      slot.media.defaultPlaybackRate = speed
      slot.media.playbackRate = speed
    }
  }

  setVolume(volume: number): void {
    // Only the playing element: the one that waits starts at the volume of the moment it takes over (`start`).
    this.volume = Math.min(1, Math.max(0, volume))
    this.slots[this.active].media.volume = this.volume
  }

  /** Goes past the part that does not play, to the beginning of the next one (the person asked for it). */
  skipFailedPart(): void {
    const failed = this.stateNow.failedPart
    if (failed === null || failed + 1 >= this.parts.length) return
    this.seek(this.parts[failed + 1].start)
  }

  /** Tries the part again after a failure of the network. */
  retry(): void {
    const time = this.stateNow.position
    this.stateNow = { ...this.stateNow, failure: null, failedPart: null }
    const { index, offset } = aim(this.parts, time)
    this.point(this.active, index, offset)
    this.emit()
  }

  dispose(): void {
    this.stop()
    this.disposed = true
    for (const remove of this.listeners) remove()
  }

  // ---- Inside ------------------------------------------------------------------------------------------------------------------

  /** Points an element at a part and remembers where in it to go once it is known. */
  private point(slotIndex: number, partIndex: number, offset: number): void {
    const slot = this.slots[slotIndex]
    const part = this.parts[partIndex]
    this.failedAhead = null
    this.active = slotIndex
    this.pending = offset
    slot.part = partIndex
    slot.ready = false
    slot.media.preload = 'auto'
    slot.media.defaultPlaybackRate = this.speed
    slot.media.src = this.options.urlOf(part)
    slot.media.load()
    this.stateNow = { ...this.stateNow, waiting: true }
    this.armLoadTimer(slot)
  }

  private start(slot: Slot): void {
    slot.media.playbackRate = this.speed
    slot.media.volume = this.volume
    void slot.media.play().catch(() => {
      // Refused (no tap of the person yet) or broken off by a new place: the state follows the element's own events.
      if (slot === this.slots[this.active] && slot.media.paused) {
        this.want = false
        this.stateNow = { ...this.stateNow, playing: false, waiting: false }
        this.emit()
      }
    })
  }

  /** Loads the part after the playing one into the other element. */
  private preloadNext(): void {
    const playing = this.slots[this.active]
    const next = playing.part + 1
    if (next >= this.parts.length || next === this.failedAhead) return
    const spare = this.slots[1 - this.active]
    if (spare.part === next) return
    spare.part = next
    spare.ready = false
    spare.media.preload = 'auto'
    spare.media.defaultPlaybackRate = this.speed
    spare.media.src = this.options.urlOf(this.parts[next])
    spare.media.load()
  }

  private armLoadTimer(slot: Slot): void {
    this.clearLoadTimer()
    const set = this.options.setTimer ?? ((run: () => void, ms: number) => setTimeout(run, ms))
    this.loadTimer = set(() => {
      this.loadTimer = null
      if (!slot.ready && slot === this.slots[this.active] && !this.disposed) this.fail('stalled')
    }, LOAD_TIMEOUT_MS)
  }

  private clearLoadTimer(): void {
    if (this.loadTimer === null) return
    const clear = this.options.clearTimer ?? ((timer: unknown) => clearTimeout(timer as ReturnType<typeof setTimeout>))
    clear(this.loadTimer)
    this.loadTimer = null
  }

  private fail(failure: PlayFailure): void {
    this.want = false
    for (const slot of this.slots) slot.media.pause()
    const slot = this.slots[this.active]
    // A format the browser does not know is the matter of every part of that format; a broken file, of that one.
    const failedPart = failure === 'unsupported' || slot.part < 0 ? null : slot.part
    this.stateNow = { ...this.stateNow, failure, failedPart, playing: false, waiting: false }
    this.emit()
  }

  private listen(index: 0 | 1): void {
    const slot = this.slots[index]
    const on = (type: string, handler: () => void) => {
      slot.media.addEventListener(type, handler)
      this.listeners.push(() => slot.media.removeEventListener(type, handler))
    }
    const isActive = () => this.slots[this.active] === slot
    on('loadedmetadata', () => {
      if (slot.part < 0) return
      slot.ready = true
      if (!isActive()) return
      this.clearLoadTimer()
      if (this.pending !== null) {
        slot.media.currentTime = Math.min(this.pending, Number.isFinite(slot.media.duration) ? slot.media.duration : this.pending)
        this.pending = null
      }
      this.stateNow = { ...this.stateNow, waiting: false }
      if (this.want) this.start(slot)
      this.emit()
    })
    on('timeupdate', () => {
      if (!isActive() || this.pending !== null || slot.part < 0) return
      const part = this.parts[slot.part]
      this.stateNow = { ...this.stateNow, position: this.position, part: slot.part }
      if (part && part.duration - slot.media.currentTime < PRELOAD_SECONDS) this.preloadNext()
      this.emit()
    })
    on('playing', () => {
      // A `playing` that was on its way when the person paused (after a jump the browser says it once it has data) changes
      // nothing: what counts is what the person asked for last.
      if (!isActive() || !this.want || slot.media.paused) return
      this.stateNow = { ...this.stateNow, playing: true, waiting: false }
      this.emit()
    })
    on('waiting', () => {
      if (!isActive() || !this.want) return
      this.stateNow = { ...this.stateNow, waiting: true }
      this.emit()
    })
    on('pause', () => {
      // A pause the engine did not ask for (the system took the sound, a headset was pulled): the state follows. An element
      // that is not paused any more is the `pause` of an earlier press on its way: the person pressed play since.
      if (!isActive() || slot.media.ended || !this.want || !slot.media.paused) return
      this.want = false
      this.stateNow = { ...this.stateNow, playing: false, waiting: false, position: this.position }
      this.emit()
    })
    on('ended', () => {
      if (!isActive()) return
      const next = slot.part + 1
      if (next < this.parts.length) {
        // The next part, at its beginning: from the element that loaded it already, else from a new request.
        this.stateNow = { ...this.stateNow, position: this.parts[next].start, part: next }
        this.seek(this.parts[next].start)
      } else {
        this.want = false
        this.stateNow = { ...this.stateNow, playing: false, waiting: false, finished: true, position: this.total }
        this.emit()
      }
    })
    on('error', () => {
      if (slot.part < 0) return
      if (!isActive()) {
        // The next part failed while it loaded ahead: it is tried again when its turn comes, not at every `timeupdate`
        // until then.
        this.failedAhead = slot.part
        slot.part = -1
        slot.ready = false
        return
      }
      this.clearLoadTimer()
      this.fail(failureOf(slot.media.error?.code, this.options.supported(this.parts[slot.part])))
    })
  }

  private emit(): void {
    if (!this.disposed) this.options.onChange()
  }
}
