import { PlayerEngine, PRELOAD_SECONDS, type MediaLike } from './playerEngine'
import { partsOf } from './timeline'

/** An audio element as the engine sees it, moved by the test: it knows a part when `known` is called, plays when `run` is. */
class FakeMedia implements MediaLike {
  src = ''
  currentTime = 0
  duration = Number.NaN
  paused = true
  playbackRate = 1
  defaultPlaybackRate = 1
  volume = 1
  preload = ''
  error: { code: number } | null = null
  ended = false
  loads = 0
  refuse = false
  private handlers = new Map<string, Set<() => void>>()

  play(): Promise<void> {
    if (this.refuse) return Promise.reject(new DOMException('not allowed', 'NotAllowedError'))
    if (this.paused) {
      this.paused = false
      this.fire('playing')
    }
    return Promise.resolve()
  }
  pause(): void {
    if (!this.paused) {
      this.paused = true
      this.fire('pause')
    }
  }
  load(): void {
    this.loads += 1
    // A new address stops the element without a `pause` of its own, and forgets the place.
    this.paused = true
    this.currentTime = 0
    this.duration = Number.NaN
    this.ended = false
  }
  removeAttribute(name: string): void {
    if (name === 'src') this.src = ''
  }
  addEventListener(type: string, listener: () => void): void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(listener)
  }
  removeEventListener(type: string, listener: () => void): void {
    this.handlers.get(type)?.delete(listener)
  }
  fire(type: string): void {
    for (const listener of [...(this.handlers.get(type) ?? [])]) listener()
  }
  /** The browser has read the part's head. */
  known(duration: number): void {
    this.duration = duration
    this.fire('loadedmetadata')
  }
  /** Plays on for `seconds`. */
  run(seconds: number): void {
    if (this.paused) return
    this.currentTime = Math.min(this.currentTime + seconds * this.playbackRate, this.duration)
    this.fire('timeupdate')
    if (this.currentTime >= this.duration) {
      this.ended = true
      this.paused = true
      this.fire('pause')
      this.fire('ended')
    }
  }
}

const files = [
  { id: 1, format: 'mp3', size: 10, part: 1, duration: 100 },
  { id: 2, format: 'mp3', size: 10, part: 2, duration: 100 },
  { id: 3, format: 'mp3', size: 10, part: 3, duration: 100 },
]

function setup(options: { supported?: boolean } = {}) {
  const a = new FakeMedia()
  const b = new FakeMedia()
  const timers: { run: () => void; ms: number; cleared: boolean }[] = []
  let changes = 0
  const engine = new PlayerEngine(a, b, {
    urlOf: (part) => `/api/v1/files/${part.id}/content`,
    supported: () => options.supported ?? true,
    onChange: () => (changes += 1),
    setTimer: (run, ms) => {
      const timer = { run, ms, cleared: false }
      timers.push(timer)
      return timer
    },
    clearTimer: (timer) => ((timer as { cleared: boolean }).cleared = true),
  })
  const playing = () => [a, b].find((media) => !media.paused) ?? null
  const holding = (id: number) => [a, b].find((media) => media.src === `/api/v1/files/${id}/content`) ?? null
  return { engine, a, b, timers, playing, holding, changes: () => changes }
}

describe('the engine of the player', () => {
  it('opens at a place inside the second part, and moves there once the browser knows the part', () => {
    const { engine, a } = setup()
    engine.load(partsOf(files), 150)
    expect(a.src).toBe('/api/v1/files/2/content')
    expect(engine.state).toMatchObject({ position: 150, part: 1, waiting: true, playing: false })
    a.known(100)
    expect(a.currentTime).toBe(50)
    expect(engine.position).toBe(150)
    expect(engine.state.waiting).toBe(false)
  })

  it('plays one part after the other as one line, the next one loaded before the first ends', () => {
    const { engine, a, b, playing, holding } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    expect(playing()).toBe(a)
    a.run(100 - PRELOAD_SECONDS - 1)
    expect(b.src).toBe('')
    a.run(2)
    // Shortly before the end the next part is asked for, in the other element.
    expect(b.src).toBe('/api/v1/files/2/content')
    b.known(100)
    const loads = b.loads
    a.run(PRELOAD_SECONDS)
    // The first part ended: the second plays at once from the element that loaded it, without a new request.
    expect(playing()).toBe(b)
    expect(b.loads).toBe(loads)
    expect(b.currentTime).toBe(0)
    expect(engine.state).toMatchObject({ playing: true, part: 1 })
    b.run(10)
    expect(engine.position).toBe(110)
    expect(holding(3)).toBeNull()
  })

  it('jumps across the border of two parts and back, and the time on the line is the one asked for', () => {
    const { engine, a, playing, holding } = setup()
    engine.load(partsOf(files), 90)
    a.known(100)
    engine.play()
    engine.seek(105)
    const second = holding(2)!
    expect(second).not.toBeNull()
    second.known(100)
    expect(second.currentTime).toBe(5)
    expect(engine.position).toBe(105)
    expect(playing()).toBe(second)
    // Back over the border: into the first part again.
    engine.seek(95)
    const first = holding(1)!
    first.known(100)
    expect(first.currentTime).toBe(95)
    expect(engine.position).toBe(95)
    expect(engine.state.playing).toBe(true)
    // Far ahead, into the third.
    engine.seek(299)
    holding(3)!.known(100)
    expect(engine.position).toBe(299)
  })

  it('a jump to the part the other element holds already costs no new request', () => {
    const { engine, a, b } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    a.run(60)
    b.known(100)
    const loads = b.loads
    engine.seek(120)
    expect(b.loads).toBe(loads)
    expect(b.currentTime).toBe(20)
    expect(b.paused).toBe(false)
    expect(a.paused).toBe(true)
    // The first element's own `pause` (it was put away) is not taken for the person's.
    expect(engine.state.playing).toBe(true)
  })

  it('quick jumps one after the other leave the last one standing', () => {
    const { engine, a, holding } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.seek(150)
    engine.seek(250)
    engine.seek(160)
    const element = holding(2)!
    element.known(100)
    expect(element.currentTime).toBe(60)
    expect(engine.position).toBe(160)
  })

  it('stops at the end of the last part and starts again from the beginning when played once more', () => {
    const { engine, a } = setup()
    engine.load(partsOf(files.slice(0, 1)), 95)
    a.known(100)
    engine.play()
    a.run(10)
    expect(engine.state).toMatchObject({ finished: true, playing: false, position: 100 })
    engine.play()
    expect(engine.position).toBe(0)
    expect(engine.state.finished).toBe(false)
  })

  it('a pause from outside (the system took the sound) is followed, and a pause of the engine is not taken for one', () => {
    const { engine, a } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    a.pause()
    expect(engine.state.playing).toBe(false)
    engine.play()
    expect(engine.state.playing).toBe(true)
  })

  it('a `pause` of the browser that comes after the person pressed play again does not stop the face', () => {
    const { engine, a } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    engine.pause()
    engine.play()
    expect(a.paused).toBe(false)
    a.fire('pause')
    expect(engine.state.playing).toBe(true)
  })

  it('a `playing` of the browser that comes after the person paused does not start the face again', () => {
    const { engine, a } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    engine.seek(50)
    engine.pause()
    // The element said `playing` for the jump only now, while it stands.
    a.fire('playing')
    expect(engine.state.playing).toBe(false)
    expect(a.paused).toBe(true)
  })

  it('a play the browser refuses (no tap yet) leaves the player paused', async () => {
    const { engine, a } = setup()
    engine.load(partsOf(files), 0)
    a.refuse = true
    a.known(100)
    engine.play()
    await Promise.resolve()
    await Promise.resolve()
    expect(engine.state.playing).toBe(false)
  })

  it('the speed holds for both elements and for the next part', () => {
    const { engine, a, b } = setup()
    engine.setSpeed(1.5)
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    expect(a.playbackRate).toBe(1.5)
    // Forty seconds of the clock are sixty of the recording.
    a.run(40)
    expect(engine.position).toBe(60)
    expect(b.src).toBe('/api/v1/files/2/content')
    expect(b.defaultPlaybackRate).toBe(1.5)
  })

  it('a part the browser does not load in time is given up, with the reason', () => {
    const { engine, timers } = setup()
    engine.load(partsOf(files), 0)
    expect(timers).toHaveLength(1)
    timers[0].run()
    expect(engine.state.failure).toBe('stalled')
  })

  it('an error says whether the format is unknown to the browser or the file is broken, and the network can be tried again', () => {
    const one = setup({ supported: false })
    one.engine.load(partsOf(files), 0)
    one.a.error = { code: 4 }
    one.a.fire('error')
    expect(one.engine.state.failure).toBe('unsupported')
    const two = setup()
    two.engine.load(partsOf(files), 0)
    two.a.error = { code: 4 }
    two.a.fire('error')
    expect(two.engine.state.failure).toBe('broken')
    const three = setup()
    three.engine.load(partsOf(files), 30)
    three.a.error = { code: 2 }
    three.a.fire('error')
    expect(three.engine.state.failure).toBe('network')
    three.engine.retry()
    expect(three.engine.state.failure).toBeNull()
    three.a.known(100)
    expect(three.a.currentTime).toBe(30)
  })

  it('a broken part is named, and a jump to another part is a new try that plays', () => {
    const { engine, a, holding } = setup()
    engine.load(partsOf(files), 150)
    a.error = { code: 4 }
    a.fire('error')
    expect(engine.state).toMatchObject({ failure: 'broken', failedPart: 1, playing: false })
    // The same part again (a nudge inside it): the message stays.
    engine.seek(160)
    expect(engine.state).toMatchObject({ failure: 'broken', failedPart: 1, position: 160 })
    // A whole other part: the failure is gone, the part is asked for and plays as soon as the browser knows it.
    engine.seek(250)
    expect(engine.state).toMatchObject({ failure: null, failedPart: null, part: 2 })
    const third = holding(3)!
    expect(third).not.toBeNull()
    third.error = null
    third.known(100)
    expect(third.currentTime).toBe(50)
    expect(third.paused).toBe(false)
    expect(engine.state.playing).toBe(true)
  })

  it('skips the broken part when asked, to the beginning of the next, and not past the last', () => {
    const { engine, a, holding } = setup()
    engine.load(partsOf(files), 120)
    a.error = { code: 4 }
    a.fire('error')
    engine.skipFailedPart()
    expect(engine.state).toMatchObject({ failure: null, part: 2, position: 200 })
    const third = holding(3)!
    third.known(100)
    expect(third.currentTime).toBe(0)
    expect(third.paused).toBe(false)
    // The last part broken: nothing to skip to.
    third.error = { code: 4 }
    third.fire('error')
    expect(engine.state).toMatchObject({ failure: 'broken', failedPart: 2 })
    engine.skipFailedPart()
    expect(engine.state).toMatchObject({ failure: 'broken', failedPart: 2 })
  })

  it('a format the browser does not know is not the matter of one part, and a break of the network is tried again by a jump', () => {
    const unknown = setup({ supported: false })
    unknown.engine.load(partsOf(files), 0)
    unknown.a.error = { code: 4 }
    unknown.a.fire('error')
    expect(unknown.engine.state).toMatchObject({ failure: 'unsupported', failedPart: null })
    const net = setup()
    net.engine.load(partsOf(files), 30)
    net.a.error = { code: 2 }
    net.a.fire('error')
    expect(net.engine.state).toMatchObject({ failure: 'network', failedPart: 0 })
    net.engine.seek(40)
    expect(net.engine.state.failure).toBeNull()
    net.a.known(100)
    expect(net.a.currentTime).toBe(40)
    expect(net.a.paused).toBe(false)
  })

  it('a failure of the part loaded ahead does not stop what plays', () => {
    const { engine, a, b } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    a.run(70)
    b.error = { code: 2 }
    b.fire('error')
    expect(engine.state.failure).toBeNull()
    expect(engine.state.playing).toBe(true)
  })

  it('a part that failed while it loaded ahead is not asked for again at every step, only when its turn comes', () => {
    const { engine, a, b } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    const before = a.loads + b.loads
    a.run(70)
    expect(b.src).toBe('/api/v1/files/2/content')
    expect(a.loads + b.loads).toBe(before + 1)
    b.error = { code: 4 }
    b.fire('error')
    for (let step = 0; step < 20; step++) a.run(1)
    expect(a.loads + b.loads).toBe(before + 1)
    // Its turn: the part is asked for once more, and its failure is named now.
    a.run(20)
    expect(a.loads + b.loads).toBe(before + 2)
    const failing = [a, b].find((media) => media.src.includes('/files/2/'))!
    failing.error = { code: 4 }
    failing.fire('error')
    expect(engine.state).toMatchObject({ failure: 'broken', failedPart: 1 })
    // Back to the beginning and on towards the end of the first part: the second is tried ahead once more.
    engine.seek(0)
    const first = [a, b].find((media) => media.src.includes('/files/1/'))!
    first.error = null
    first.known(100)
    expect(engine.state).toMatchObject({ failure: null, playing: true })
    const again = a.loads + b.loads
    first.run(70)
    expect(a.loads + b.loads).toBe(again + 1)
    expect([a, b].some((media) => media !== first && media.src.includes('/files/2/'))).toBe(true)
  })

  it('closing lets go of the files', () => {
    const { engine, a, b } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.play()
    engine.stop()
    expect([a.src, b.src]).toEqual(['', ''])
    expect(a.paused).toBe(true)
  })

  it('opens a chapter that begins on the border of two parts at the beginning of the later part, also after a pause', () => {
    const many = Array.from({ length: 80 }, (_, index) => ({ id: index + 1, format: 'flac', size: 1, part: index + 1, duration: 1.3 }))
    const { engine, a, holding } = setup()
    engine.load(partsOf(many), 0)
    a.known(1.3)
    // Chapter 4 starts where part 4 starts: 3 x 1300 ms, as the server counts.
    engine.seek((3 * 1300) / 1000)
    expect(holding(4)).not.toBeNull()
    // Part 3 (the one before) is never asked for.
    expect(holding(3)).toBeNull()
    const second = holding(4)!
    second.known(1.3)
    expect(second.currentTime).toBe(0)
    expect(engine.state.part).toBe(3)
    engine.play()
    expect(second.currentTime).toBe(0)
  })

  it('does not aim at the last moments of a part that has a successor: that is the beginning of the next', () => {
    const { engine, a, holding } = setup()
    engine.load(partsOf(files), 0)
    a.known(100)
    engine.seek(99.98)
    const next = holding(2)!
    expect(next).not.toBeNull()
    next.known(100)
    expect(next.currentTime).toBe(0)
    expect(engine.state).toMatchObject({ part: 1, position: 100 })
  })
})
