import type { ProgressIn, ProgressOut } from '../api/client'
import type { Place } from './readFrame'
import { chooseStart, deviceKey, forgetLocal, loadLocal, ProgressSaver, saveLocal, whereOf, clearLocal, type LocalPlace } from './readerProgress'

const NOW = Date.parse('2026-10-13T12:00:00Z')

function server(over: Partial<ProgressOut> = {}): ProgressOut {
  return {
    edition_id: 1, work_id: 1, locator: 'epubcfi(/6/20)', position: null, fraction: 0.6, unit: 'chapter', chapter: 16, chapter_count: 38, chapter_title: 'Sixteen',
    device_key: 'other-device', device: 'Pixel 8', moved_at: '2026-10-13T11:00:00Z', received_at: '2026-10-13T11:00:01Z', age: 3600, ...over,
  }
}
const local = (over: Partial<LocalPlace> = {}): LocalPlace => ({ locator: 'epubcfi(/6/4)', fraction: 0.1, at: NOW - 7_200_000, ...over })
const place = (over: Partial<Place> = {}): Place => ({ locator: 'epubcfi(/6/8)', fraction: 0.2, tocId: 3, unit: 'chapter', chapter: 4, chapterCount: 38, chapterTitle: 'Four', ...over })

describe('where a book opens', () => {
  it('at the beginning when nothing is known', () => {
    expect(chooseStart(null, null, 'me')).toEqual({ start: { locator: null, fraction: null }, ask: null })
  })

  it('at the place of the server when this device has none, with no question: a book goes on on a new device', () => {
    expect(chooseStart(server(), null, 'me')).toEqual({ start: { locator: 'epubcfi(/6/20)', fraction: 0.6 }, ask: null })
  })

  it('at the place of this device when the server has none', () => {
    expect(chooseStart(null, local(), 'me')).toEqual({ start: { locator: 'epubcfi(/6/4)', fraction: 0.1 }, ask: null })
  })

  it('at the place the server has from this very device', () => {
    const mine = server({ device_key: 'me', age: 3600 })
    expect(chooseStart(mine, local({ at: NOW - 7_200_000 }), 'me', NOW)).toEqual({ start: { locator: 'epubcfi(/6/20)', fraction: 0.6 }, ask: null })
  })

  it('at the copy kept here when it is newer than what the server heard from this device (a report that did not get through)', () => {
    const mine = server({ device_key: 'me', age: 7200 })
    expect(chooseStart(mine, local({ at: NOW - 60_000 }), 'me', NOW).start.locator).toBe('epubcfi(/6/4)')
  })

  it('at the own place, with the question, when another device is further along and newer', () => {
    const answer = chooseStart(server(), local(), 'me', NOW)
    expect(answer.start.locator).toBe('epubcfi(/6/4)')
    expect(answer.ask?.device).toBe('Pixel 8')
  })

  it('no question when the other device is further back, or not newer, or by a hair', () => {
    expect(chooseStart(server({ fraction: 0.05 }), local(), 'me', NOW).ask).toBeNull()
    expect(chooseStart(server({ age: 14_400 }), local({ at: NOW - 60_000 }), 'me', NOW).ask).toBeNull()
    expect(chooseStart(server({ fraction: 0.102 }), local(), 'me', NOW).ask).toBeNull()
  })

  it('never moves the person away from the place this device left', () => {
    expect(chooseStart(server(), local({ at: NOW - 60_000, fraction: 0.9 }), 'me', NOW).start.fraction).toBe(0.9)
  })

  it('does not care what the clock of this device says: only how long ago, on its own clock', () => {
    // The same two places as in the test of the question above, on a device whose clock is three hours ahead, or a day behind.
    for (const shift of [3 * 3_600_000, -86_400_000]) {
      const clock = NOW + shift
      const asked = chooseStart(server({ age: 3600 }), local({ at: clock - 7_200_000 }), 'me', clock)
      expect(asked.ask?.device, String(shift)).toBe('Pixel 8')
      const notNewer = chooseStart(server({ age: 14_400 }), local({ at: clock - 60_000 }), 'me', clock)
      expect(notNewer.ask, String(shift)).toBeNull()
      const mine = chooseStart(server({ device_key: 'me', age: 7200 }), local({ at: clock - 60_000 }), 'me', clock)
      expect(mine.start.locator, String(shift)).toBe('epubcfi(/6/4)')
    }
  })
})

describe('what a place is called in the question', () => {
  it('says the chapter, the page, or the percentage', () => {
    expect(whereOf({ chapter: 16, unit: 'chapter', fraction: 0.4 })).toEqual({ key: 'chapter', n: 16 })
    expect(whereOf({ chapter: 12, unit: 'page', fraction: 0.4 })).toEqual({ key: 'page', n: 12 })
    expect(whereOf({ chapter: 0, unit: 'chapter', fraction: 0.404 })).toEqual({ key: 'percent', n: 40 })
  })
})

describe('what this device keeps', () => {
  beforeEach(() => {
    const kept = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => kept.get(key) ?? null,
      setItem: (key: string, value: string) => void kept.set(key, value),
      removeItem: (key: string) => void kept.delete(key),
      key: (index: number) => Array.from(kept.keys())[index] ?? null,
      get length() {
        return kept.size
      },
    })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('keeps its own place per book, and forgets it', () => {
    expect(loadLocal(1, 7)).toBeNull()
    saveLocal(1, 7, local())
    expect(loadLocal(1, 7)).toEqual(local())
    expect(loadLocal(1, 8)).toBeNull()
    forgetLocal(1, 7)
    expect(loadLocal(1, 7)).toBeNull()
  })

  it('keeps the places of two people on one device apart', () => {
    saveLocal(1, 7, local({ fraction: 0.71, locator: 'epubcfi(/6/40)' }))
    // The second person in the same browser has not read this book: nothing of the first person's place is theirs.
    expect(loadLocal(2, 7)).toBeNull()
    saveLocal(2, 7, local({ fraction: 0.05, locator: 'epubcfi(/6/2)' }))
    expect(loadLocal(1, 7)?.fraction).toBe(0.71)
    expect(loadLocal(2, 7)?.fraction).toBe(0.05)
  })

  it('forgets every place of everybody, and the ones kept before places were kept per person, and nothing else', () => {
    saveLocal(1, 7, local())
    saveLocal(2, 8, local())
    localStorage.setItem('nexbooks.read.9', JSON.stringify(local()))
    localStorage.setItem('nexbooks.theme', 'dark')
    const key = deviceKey()
    clearLocal()
    expect(loadLocal(1, 7)).toBeNull()
    expect(loadLocal(2, 8)).toBeNull()
    expect(localStorage.getItem('nexbooks.read.9')).toBeNull()
    expect(localStorage.getItem('nexbooks.theme')).toBe('dark')
    expect(deviceKey()).toBe(key)
  })

  it('takes nothing for a place that is not one', () => {
    localStorage.setItem('nexbooks.read.1.7', '{"locator": 5}')
    expect(loadLocal(1, 7)).toBeNull()
    localStorage.setItem('nexbooks.read.1.7', 'not json')
    expect(loadLocal(1, 7)).toBeNull()
  })

  it('has one key for itself, of the shape the server takes, the same every time', () => {
    const key = deviceKey()
    expect(key).toMatch(/^[A-Za-z0-9_-]{4,40}$/)
    expect(deviceKey()).toBe(key)
    localStorage.setItem('nexbooks.device', 'bad key!')
    expect(deviceKey()).toMatch(/^[A-Za-z0-9_-]{4,40}$/)
  })

  it('works without storage (the browser refuses): a key for this page only, no crash', () => {
    const refuse = () => {
      throw new Error('blocked')
    }
    vi.stubGlobal('localStorage', { getItem: refuse, setItem: refuse, removeItem: refuse })
    expect(deviceKey()).toMatch(/^[A-Za-z0-9_-]{4,40}$/)
    expect(loadLocal(1, 1)).toBeNull()
    expect(() => saveLocal(1, 1, local())).not.toThrow()
    expect(() => forgetLocal(1, 1)).not.toThrow()
    expect(() => clearLocal()).not.toThrow()
  })
})

describe('saving the place', () => {
  let clock = NOW
  let sent: { body: ProgressIn; keepalive: boolean }[]
  let failing = false
  let results: boolean[]

  const make = (options: { interval?: number; retry?: number } = {}) =>
    new ProgressSaver(
      async (body, keepalive) => {
        if (failing) throw new Error('offline')
        sent.push({ body, keepalive })
        return { applied: true, progress: server() }
      },
      { key: 'me', name: 'Chrome · Linux' },
      (result) => results.push(result.applied),
      { now: () => clock, ...options },
    )

  beforeEach(() => {
    vi.useFakeTimers()
    clock = NOW
    sent = []
    failing = false
    results = []
  })
  afterEach(() => vi.useRealTimers())

  const advance = async (ms: number) => {
    clock += ms
    await vi.advanceTimersByTimeAsync(ms)
  }

  it('reports once in a while, the last place of that time', async () => {
    const saver = make({ interval: 4000 })
    saver.report(place({ fraction: 0.1 }))
    await advance(1000)
    saver.report(place({ fraction: 0.2 }))
    await advance(1000)
    saver.report(place({ fraction: 0.3 }))
    expect(sent).toHaveLength(0)
    await advance(2100)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.fraction).toBe(0.3)
    expect(sent[0].body).toMatchObject({ device_key: 'me', device: 'Chrome · Linux', unit: 'chapter', chapter: 4, chapter_count: 38, chapter_title: 'Four', locator: 'epubcfi(/6/8)' })
    expect(sent[0].keepalive).toBe(false)
    expect(results).toEqual([true])
    // Nothing waits any more: no second report without a new place.
    await advance(20_000)
    expect(sent).toHaveLength(1)
    expect(saver.waiting).toBe(false)
  })

  it('says how long ago the reader was at the place, not what time it is', async () => {
    const saver = make({ interval: 4000 })
    saver.report(place())
    await advance(4000)
    expect(sent[0].body.age).toBeCloseTo(4, 0)
    // Offline: the age grows while the place waits.
    failing = true
    saver.report(place({ fraction: 0.4 }))
    await advance(4000)
    expect(sent).toHaveLength(1)
    failing = false
    await advance(10_000)
    expect(sent).toHaveLength(2)
    expect(sent[1].body.fraction).toBe(0.4)
    expect(sent[1].body.age).toBeGreaterThan(10)
  })

  it('sends at once when the page is left, with a request made to outlive the page', async () => {
    const saver = make()
    saver.report(place({ fraction: 0.5 }))
    await saver.flush(true)
    expect(sent).toHaveLength(1)
    expect(sent[0].keepalive).toBe(true)
    expect(sent[0].body.age).toBeLessThan(1)
    // Nothing to send twice.
    await saver.flush(true)
    expect(sent).toHaveLength(1)
  })

  it('holds back while a question is open, and sends what waits when it is answered', async () => {
    const saver = make()
    saver.hold()
    saver.report(place({ fraction: 0.15 }))
    await advance(30_000)
    await saver.flush(true)
    expect(sent).toHaveLength(0)
    expect(saver.waiting).toBe(true)
    saver.release()
    await advance(4100)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.fraction).toBe(0.15)
  })

  it('keeps what was not sent and tries again, and sends only the newest place after a failure', async () => {
    const saver = make({ interval: 4000, retry: 10_000 })
    failing = true
    saver.report(place({ fraction: 0.1 }))
    await advance(4100)
    saver.report(place({ fraction: 0.2 }))
    await advance(10_000)
    expect(sent).toHaveLength(0)
    failing = false
    await advance(10_100)
    expect(sent).toHaveLength(1)
    expect(sent[0].body.fraction).toBe(0.2)
  })

  it('does not lose a place reported while another is on its way', async () => {
    let release: () => void = () => undefined
    const slow = new ProgressSaver(
      (body, keepalive) => {
        sent.push({ body, keepalive })
        return new Promise((resolve) => {
          release = () => resolve({ applied: true, progress: server() })
        })
      },
      { key: 'me', name: 'x' },
      () => undefined,
      { now: () => clock, interval: 1000 },
    )
    slow.report(place({ fraction: 0.1 }))
    await advance(1100)
    expect(sent).toHaveLength(1)
    slow.report(place({ fraction: 0.5 }))
    release()
    await advance(1100)
    release()
    await advance(10)
    expect(sent.map((one) => one.body.fraction)).toEqual([0.1, 0.5])
  })

  it('tells what the server answered: a report that was not the newest', async () => {
    const saver = new ProgressSaver(async () => ({ applied: false, progress: server() }), { key: 'me', name: 'x' }, (result) => results.push(result.applied), { now: () => clock })
    saver.report(place())
    await saver.flush(false)
    expect(results).toEqual([false])
  })
})
