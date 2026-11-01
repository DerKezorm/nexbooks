/**
 * Where the person is in a book, and how that is kept: saved now and then while they read and once more when they leave,
 * and found again when the book is opened, on this device or on another.
 *
 * - The server keeps the newest report of all devices (`backend/app/services/progress.py`). A device says how long ago the
 *   reader was at a place, not what time its clock shows, so a clock that is wrong keeps no other device out.
 * - This device keeps a copy of its own last place (`localStorage`, if the browser lets it, per person and forgotten at sign-out), so that a book opens where it was
 *   left even when the server has not heard yet.
 * - A book that is only opened saves nothing: the place is saved after the person has moved (turned a page, jumped, scrolled),
 *   and not before the question of another device's place is answered (`chooseStart`): else a device that was only opened
 *   would take the place away from the one that read.
 */
import type { ProgressIn, ProgressOut } from '../api/client'
import { furtherAlong, newDeviceKey, type Place } from './readFrame'

const DEVICE_KEY = 'nexbooks.device'

/** The key this browser makes up for itself and reports with every place; the same on every report. */
export function deviceKey(): string {
  try {
    const stored = localStorage.getItem(DEVICE_KEY)
    if (stored && /^[A-Za-z0-9_-]{4,40}$/.test(stored)) return stored
    const made = newDeviceKey()
    localStorage.setItem(DEVICE_KEY, made)
    return made
  } catch {
    // No storage: a key for this page only. A reload counts as another device, which only costs a question.
    return memory
  }
}
const memory = newDeviceKey()

export type Start = { locator: string | null; fraction: number | null }
export type LocalPlace = { locator: string; fraction: number; at: number }

/** Where this device keeps a place, **per person**: a family tablet is signed in by several people, one after the other, and the
 * place that one of them left in a book is not the place of another (and tells how far they are). */
const PREFIX = 'nexbooks.read.'
const localName = (userId: number, editionId: number) => `${PREFIX}${userId}.${editionId}`

/** Forgets every place this device keeps, of everybody (and the ones kept before they were kept per person). Called when
 * somebody signs out, so that the next person on the device finds nothing of the last. */
export function clearLocal(): void {
  try {
    const names: string[] = []
    for (let index = 0; index < localStorage.length; index++) {
      const name = localStorage.key(index)
      if (name?.startsWith(PREFIX)) names.push(name)
    }
    for (const name of names) localStorage.removeItem(name)
  } catch {
    // No storage, nothing kept.
  }
}

export function loadLocal(userId: number, editionId: number): LocalPlace | null {
  try {
    const text = localStorage.getItem(localName(userId, editionId))
    if (!text) return null
    const value = JSON.parse(text) as Partial<LocalPlace>
    if (typeof value.locator === 'string' && typeof value.fraction === 'number' && typeof value.at === 'number') return value as LocalPlace
  } catch {
    // Nothing kept, or nothing readable: as if there were none.
  }
  return null
}

export function saveLocal(userId: number, editionId: number, place: LocalPlace): void {
  try {
    localStorage.setItem(localName(userId, editionId), JSON.stringify(place))
  } catch {
    // Then the book opens at the server's place next time.
  }
}

export function forgetLocal(userId: number, editionId: number): void {
  try {
    localStorage.removeItem(localName(userId, editionId))
  } catch {
    // Nothing to forget.
  }
}

/** The recording the player held last on this device, per person: after a reload the small player comes back with it,
 * paused. It goes with every other place at sign-out (`clearLocal`). */
const playerName = (userId: number) => `${PREFIX}${userId}.player`

export function rememberPlayer(userId: number, editionId: number | null): void {
  try {
    if (editionId === null) localStorage.removeItem(playerName(userId))
    else localStorage.setItem(playerName(userId), String(editionId))
  } catch {
    // Then the player is not there after a reload; the book is still a tap away.
  }
}

export function rememberedPlayer(userId: number): number | null {
  try {
    const value = Number(localStorage.getItem(playerName(userId)))
    return Number.isInteger(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

/**
 * Where a book opens, and whether the person is told of another place. `server`: where the server says the person is (from
 * any device), `local`: where this device last was, `device`: the key of this device, `received`: when (by this device's clock)
 * the answer of the server came.
 *
 * Only this device's clock is read. The server says how long ago its place was reached (`age`), this device knows when it was
 * at its own; both become a time on this clock. A server whose time of day differs from this device's changes nothing.
 *
 * - Nothing known: the beginning.
 * - Only this device knows (or the server's place is from this device): there.
 * - Only the server knows (a device that has not read this book): the server's place, the way a book continues on a new device.
 * - Both know, and the server's place is from another device: this device's own place, and, when the other is further along and
 *   newer, a question whether to jump there. Opening a book never moves the person away from where this device left it.
 */
export function chooseStart(server: ProgressOut | null, local: LocalPlace | null, device: string, received: number = Date.now()): { start: Start; ask: ProgressOut | null } {
  const serverAt = server ? received - server.age * 1000 : 0
  const ofServer: Start = server ? { locator: server.locator, fraction: server.fraction } : { locator: null, fraction: null }
  const ofLocal: Start = local ? { locator: local.locator, fraction: local.fraction } : { locator: null, fraction: null }
  if (!server && !local) return { start: ofLocal, ask: null }
  if (!local) return { start: ofServer, ask: null }
  if (!server) return { start: ofLocal, ask: null }
  if (server.device_key === device) {
    // The server's place is this device's own; a newer copy kept here (the last report did not get through) wins.
    return local.at > serverAt ? { start: ofLocal, ask: null } : { start: ofServer, ask: null }
  }
  const newer = serverAt > local.at
  return { start: ofLocal, ask: newer && furtherAlong(server.fraction, local.fraction) ? server : null }
}

/** What to call a place in the question ("Kapitel 16", "Seite 12", "40 %"). */
export function whereOf(place: Pick<ProgressOut, 'chapter' | 'unit' | 'fraction'>): { key: 'chapter' | 'page' | 'percent'; n: number } {
  if (place.chapter > 0) return { key: place.unit === 'page' ? 'page' : 'chapter', n: place.chapter }
  return { key: 'percent', n: Math.round(place.fraction * 100) }
}

type Result = { applied: boolean; progress: ProgressOut }

/**
 * Saves the place now and then. A place is reported at most once in `interval` ms (the last one of that time), at once when
 * the page is left (`flush`), and again after a failure. The age the server is told is how long ago the reader was at the
 * place, worked out at the moment of sending.
 */
export class ProgressSaver {
  private pending: { place: Place; at: number } | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private sending = false
  private again = false
  /** Reports are held back (a question to the person is open); `release` lets them go. */
  private held = false

  constructor(
    private readonly send: (body: ProgressIn, keepalive: boolean) => Promise<Result>,
    private readonly device: { key: string; name: string },
    private readonly onResult: (result: Result) => void = () => undefined,
    private readonly options: { interval?: number; retry?: number; now?: () => number } = {},
  ) {}

  private now(): number {
    return (this.options.now ?? Date.now)()
  }

  hold(): void {
    this.held = true
  }

  release(): void {
    this.held = false
    if (this.pending) this.schedule()
  }

  report(place: Place): void {
    this.pending = { place, at: this.now() }
    if (!this.held) this.schedule()
  }

  private schedule(delay = this.options.interval ?? 4000): void {
    if (this.timer !== null || this.held) return
    this.timer = setTimeout(() => {
      this.timer = null
      void this.flush(false)
    }, delay)
  }

  /** Sends what waits. `leaving`: the page is going, the request is made to outlive it. */
  async flush(leaving: boolean): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (!this.pending || this.held) return
    if (this.sending) {
      this.again = true
      return
    }
    const { place, at } = this.pending
    this.sending = true
    try {
      const result = await this.send(
        {
          locator: place.locator,
          fraction: place.fraction,
          unit: place.unit,
          chapter: place.chapter,
          chapter_count: place.chapterCount,
          chapter_title: place.chapterTitle,
          device_key: this.device.key,
          device: this.device.name,
          age: Math.max(0, (this.now() - at) / 1000),
        },
        leaving,
      )
      // Only what was sent is done: a place reported meanwhile still waits.
      if (this.pending && this.pending.at === at) this.pending = null
      this.onResult(result)
    } catch {
      // Not saved: it waits and is sent again a little later (or when the page is left).
      if (!leaving) this.schedule(this.options.retry ?? 10_000)
    } finally {
      this.sending = false
      if (this.again) {
        this.again = false
        void this.flush(leaving)
      } else if (this.pending && this.timer === null && !leaving) {
        this.schedule()
      }
    }
  }

  get waiting(): boolean {
    return this.pending !== null
  }

  stop(): void {
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
  }
}
