import { FADE_SECONDS, SleepTimer } from './sleepTimer'

/** A clock the test moves on, in milliseconds. */
function fakeClock() {
  let now = 1_000_000
  return { now: () => now, run: (seconds: number) => (now += seconds * 1000) }
}

/** Plays `seconds` in ticks of a quarter second, the way the player calls the timer; returns the last verdict and when it stopped. */
function play(timer: SleepTimer, clock: ReturnType<typeof fakeClock>, seconds: number, from = 0, speed = 1, playing = true) {
  let position = from
  let verdict = timer.tick({ playing, position, speed })
  let stoppedAt: number | null = null
  const volumes: number[] = []
  for (let elapsed = 0; elapsed < seconds; elapsed += 0.25) {
    clock.run(0.25)
    if (playing) position += 0.25 * speed
    verdict = timer.tick({ playing, position, speed })
    volumes.push(verdict.volume)
    if (verdict.stop) {
      stoppedAt = elapsed + 0.25
      break
    }
  }
  return { verdict, stoppedAt, position, volumes }
}

describe('the sleep timer', () => {
  it('stops after the minutes of listening, not before', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    timer.minutes(5)
    expect(timer.state()).toEqual({ kind: 'minutes', minutes: 5, left: 300 })
    const first = play(timer, clock, 299)
    expect(first.stoppedAt).toBeNull()
    expect(first.verdict.state).toMatchObject({ kind: 'minutes', left: 1 })
    const rest = play(timer, clock, 5, first.position)
    expect(rest.stoppedAt).toBe(1)
    expect(timer.active).toBe(false)
  })

  it('counts only while the recording plays: a pause stops the count', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    timer.minutes(10)
    play(timer, clock, 60)
    const paused = play(timer, clock, 3600, 60, 1, false)
    expect(paused.stoppedAt).toBeNull()
    expect(timer.state()).toMatchObject({ left: 540 })
  })

  it('lowers the sound in the last seconds, gently, and never to nothing before it stops', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    timer.minutes(5)
    const run = play(timer, clock, 400)
    const before = run.volumes.slice(0, (300 - FADE_SECONDS) * 4 - 1)
    expect(before.every((volume) => volume === 1)).toBe(true)
    const fade = run.volumes.slice((300 - FADE_SECONDS) * 4, -1)
    expect(fade.length).toBeGreaterThan(30)
    for (let i = 1; i < fade.length; i++) expect(fade[i]).toBeLessThanOrEqual(fade[i - 1])
    expect(Math.min(...fade)).toBeGreaterThanOrEqual(0.05)
    expect(run.verdict).toMatchObject({ stop: true, volume: 1 })
  })

  it('holds the minutes to 5 to 120', () => {
    const timer = new SleepTimer(() => 0)
    timer.minutes(2)
    expect(timer.state()).toMatchObject({ minutes: 5 })
    timer.minutes(500)
    expect(timer.state()).toMatchObject({ minutes: 120 })
    timer.minutes(Number.NaN)
    expect(timer.state()).toMatchObject({ minutes: 5 })
  })

  it('stops at the end of the chapter, with the time left at the speed of the player', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    timer.chapterEnd(100)
    expect(timer.state(40, 2)).toEqual({ kind: 'chapter', end: 100, left: 30 })
    const run = play(timer, clock, 60, 40, 2)
    expect(run.stoppedAt).toBe(30)
    expect(run.position).toBeGreaterThanOrEqual(99.9)
    expect(run.position).toBeLessThan(100.6)
  })

  it('does not take the next chapter for the end when it follows by itself, but follows a jump of the person', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    timer.chapterEnd(100)
    // The person jumps into the next chapter (100 to 250): the end moves with them.
    timer.jumped(250)
    const run = play(timer, clock, 400, 120)
    expect(run.stoppedAt).toBe(130)
    expect(timer.active).toBe(false)
  })

  it('a jump does nothing to a timer of minutes, and a timer that is off says nothing', () => {
    const clock = fakeClock()
    const timer = new SleepTimer(clock.now)
    expect(timer.tick({ playing: true, position: 0, speed: 1 })).toEqual({ volume: 1, stop: false, state: { kind: 'off' } })
    timer.minutes(5)
    timer.jumped(999)
    expect(timer.state()).toMatchObject({ kind: 'minutes' })
    timer.cancel()
    expect(timer.active).toBe(false)
    expect(timer.state()).toEqual({ kind: 'off' })
  })
})
