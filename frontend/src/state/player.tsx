/**
 * The player of recordings, for the whole app: it stands above the pages, so that what plays goes on while the
 * person moves between the shelf, the page of a book, Discover and the stack. Small at the bottom (`MiniPlayer`), or over the
 * whole window in the colours of the book (`FullPlayer`); the way back of the browser folds the big one up again.
 *
 * - **Where it starts.** As the reader does (`lib/readerProgress.ts`): this device's own place, else the server's; when another
 *   device is further along and newer, the person is asked, and nothing of this device is saved until they answered. A
 *   recording that is only opened saves nothing: the place is saved once the person played or jumped.
 * - **What is saved.** The place (`time:…`, seconds over all the parts), the chapter and how far in the whole recording, now and
 *   then while it plays, at once when it is paused, closed or the page is left. Only the place of the recording: the e-book of
 *   the same book keeps its own.
 * - **Choices** (speed, the jumps, ten seconds back after a longer pause) are the person's, in the profile. The volume is this
 *   device's (`lib/playerVolume.ts`).
 * - **The lock screen** (Media Session): title, author, cover (nexbooks' own address), the chapter as the title of the track,
 *   the buttons ahead and back jump, the bar on the lock screen is the chapter.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError, authApi, contentUrl, readingApi, type BookmarkOut, type EditionOut, type Profile, type ProgressOut, type WorkBrief } from '../api/client'
import { browserProbe, PROBE, unplayable } from '../lib/audioFormats'
import { errorText } from '../lib/errors'
import { PlayerEngine, type EngineState } from '../lib/playerEngine'
import { clampVolume, loadVolume, saveVolume, volumeSettable, type Volume } from '../lib/playerVolume'
import { deviceName } from '../lib/readFrame'
import { chooseStart, deviceKey, loadLocal, ProgressSaver, rememberedPlayer, rememberPlayer, saveLocal } from '../lib/readerProgress'
import { SleepTimer, type SleepState } from '../lib/sleepTimer'
import { chapterAt, clampSpeed, nextChapterStart, partsOf, previousChapterStart, resumeFrom, secondsOf, spansOf, speedFromProfile, speedToProfile, timeLocator, totalOf, type Part, type Span } from '../lib/timeline'
import { useAuth } from './auth'

export type PlayerBook = {
  work: WorkBrief
  edition: EditionOut
  parts: Part[]
  spans: Span[]
  total: number
  /** The formats of the parts this browser says it cannot play at all. */
  unsupported: string[]
}

export type Choices = { back: number; ahead: number; rewind: boolean }

/** What changes while it plays: read by the faces of the player only, so that a page around them is not drawn four times a second. */
export type PlayerLive = {
  book: PlayerBook | null
  loading: boolean
  engine: EngineState
  speed: number
  /** The volume of this device; `settable` is false where only the buttons of the device set it (Safari on iPhone and iPad). */
  volume: Volume & { settable: boolean }
  sleep: SleepState
  hint: ProgressOut | null
  expanded: boolean
  choices: Choices
  bookmarks: BookmarkOut[]
  /** A short sentence of the player ("Lesezeichen gesetzt."), gone after a moment. */
  said: string | null
  /** The chapter the place is in (index into `book.spans`). */
  chapter: number
}

export type PlayerActions = {
  open: (editionId: number, options?: { expand?: boolean; play?: boolean }) => Promise<void>
  toggle: () => void
  play: () => void
  pause: () => void
  seek: (seconds: number) => void
  skip: (seconds: number) => void
  chapterBack: () => void
  chapterAhead: () => void
  setSpeed: (speed: number) => void
  setVolume: (level: number) => void
  toggleMute: () => void
  sleepFor: (minutes: number) => void
  sleepAtChapterEnd: () => void
  sleepOff: () => void
  expand: () => void
  collapse: () => void
  close: () => void
  jumpToHint: () => void
  stayHere: () => void
  setChoices: (change: Partial<Choices>) => void
  addBookmark: () => Promise<BookmarkOut | null>
  saveNote: (id: number, note: string) => Promise<boolean>
  removeBookmark: (id: number) => Promise<void>
  retry: () => void
  /** Goes past the part that does not play, to the next one. */
  skipPart: () => void
}

const ActionsContext = createContext<PlayerActions | null>(null)
const LiveContext = createContext<PlayerLive | null>(null)

/** How often the place is sent while it plays (and at once on a pause): a browser that is ended without a word (no
 * `pagehide`) loses at most this much of what was heard. One small request in five seconds is nothing for the server. */
const SAVE_INTERVAL_MS = 5_000
/** How often this device keeps its own copy of the place while it plays. */
const LOCAL_INTERVAL_MS = 2_000
/** Two bookmarks closer than this are one (a double click, a second press at the same sentence). */
const SAME_MARK_SECONDS = 2

const IDLE: EngineState = { position: 0, playing: false, waiting: false, finished: false, failure: null, failedPart: null, part: 0 }

function choicesOf(profile: Profile | undefined): Choices {
  return {
    back: typeof profile?.player_back === 'number' ? profile.player_back : 15,
    ahead: typeof profile?.player_ahead === 'number' ? profile.player_ahead : 30,
    rewind: profile?.player_rewind !== false,
  }
}

/** A small store the engine writes and React reads (`useSyncExternalStore`). */
function makeStore<T>(first: T) {
  let value = first
  const listeners = new Set<() => void>()
  return {
    get: () => value,
    set: (next: T) => {
      value = next
      for (const listener of listeners) listener()
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { me, setMe } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const first = useRef<HTMLAudioElement>(null)
  const second = useRef<HTMLAudioElement>(null)

  const [book, setBook] = useState<PlayerBook | null>(null)
  const [loading, setLoading] = useState(false)
  const [speed, setSpeedState] = useState(() => speedFromProfile(me?.profile?.player_speed))
  const [volume, setVolumeState] = useState<Volume>(loadVolume)
  const [settable] = useState(volumeSettable)
  const [choices, setChoicesState] = useState<Choices>(() => choicesOf(me?.profile))
  const [hint, setHint] = useState<ProgressOut | null>(null)
  const [bookmarks, setBookmarks] = useState<BookmarkOut[]>([])
  const [said, setSaid] = useState<string | null>(null)
  const [sleep, setSleep] = useState<SleepState>({ kind: 'off' })

  const store = useRef(makeStore<EngineState>(IDLE))
  const engineState = useSyncExternalStore(store.current.subscribe, store.current.get, store.current.get)
  const engine = useRef<PlayerEngine | null>(null)
  const timer = useRef(new SleepTimer())
  const saver = useRef<ProgressSaver | null>(null)
  const bookRef = useRef<PlayerBook | null>(null)
  bookRef.current = book
  const meRef = useRef(me)
  meRef.current = me
  const choicesRef = useRef(choices)
  choicesRef.current = choices
  const speedRef = useRef(speed)
  speedRef.current = speed
  const volumeRef = useRef(volume)
  volumeRef.current = volume
  const hintRef = useRef(hint)
  hintRef.current = hint
  /** The place is saved only after the person played or jumped. */
  const armed = useRef(false)
  /** When this device last heard the recording (a pause, or the place it opened at), by its own clock: for the step back. */
  const heardAt = useRef<number | null>(null)
  const localAt = useRef(0)
  const opening = useRef(0)
  /** The latest ways to open and fold the big view (they change with the address; `open` and `close` are made once). */
  const expandRef = useRef<() => void>(() => undefined)
  const collapseRef = useRef<() => void>(() => undefined)
  const playRef = useRef<() => void>(() => undefined)
  const device = useMemo(() => ({ key: deviceKey(), name: deviceName(navigator.userAgent, navigator.platform, navigator.maxTouchPoints) }), [])

  // ---- The engine ---------------------------------------------------------------------------------------------------------------
  useEffect(() => {
    if (!first.current || !second.current) return
    const made = new PlayerEngine(first.current, second.current, {
      urlOf: (part) => contentUrl(part.id),
      supported: (part) => browserProbe()(PROBE[part.format] ?? '') !== '',
      onChange: () => store.current.set(made.state),
    })
    made.setSpeed(speedRef.current)
    made.setVolume(volumeRef.current.muted ? 0 : volumeRef.current.level)
    engine.current = made
    return () => {
      made.dispose()
      engine.current = null
    }
  }, [])

  // The person's choices come with the account, and go with it.
  const profileSpeed = me?.profile?.player_speed
  useEffect(() => {
    const next = speedFromProfile(profileSpeed)
    setSpeedState(next)
    engine.current?.setSpeed(next)
  }, [profileSpeed])
  const profileBack = me?.profile?.player_back
  const profileAhead = me?.profile?.player_ahead
  const profileRewind = me?.profile?.player_rewind
  useEffect(() => {
    setChoicesState(choicesOf({ player_back: profileBack, player_ahead: profileAhead, player_rewind: profileRewind } as Profile))
  }, [profileBack, profileAhead, profileRewind])

  const chapter = book ? Math.max(0, chapterAt(book.spans, engineState.position)) : 0

  // ---- Saving the place -----------------------------------------------------------------------------------------------------------
  const report = useCallback((position: number) => {
    const current = bookRef.current
    const keep = saver.current
    if (!current || !keep || !armed.current) return
    const index = Math.max(0, chapterAt(current.spans, position))
    const span = current.spans[index]
    const fraction = current.total > 0 ? Math.min(1, Math.max(0, position / current.total)) : 0
    keep.report({
      locator: timeLocator(position),
      fraction,
      tocId: -1,
      unit: 'time',
      chapter: index + 1,
      chapterCount: current.spans.length,
      chapterTitle: span?.title ?? '',
    })
    const now = Date.now()
    if (now - localAt.current > LOCAL_INTERVAL_MS && meRef.current) {
      localAt.current = now
      saveLocal(meRef.current.id, current.edition.id, { locator: timeLocator(position), fraction, at: now })
    }
  }, [])

  /** This device's own copy of the place, at once (after a pause, a jump, before the page goes). */
  const keepLocal = useCallback((position: number) => {
    const current = bookRef.current
    if (!current || !meRef.current || !armed.current) return
    localAt.current = Date.now()
    saveLocal(meRef.current.id, current.edition.id, { locator: timeLocator(position), fraction: current.total > 0 ? position / current.total : 0, at: localAt.current })
  }, [])

  /**
   * Sends what waits. While it plays, the place of this moment is reported first; while it is paused nothing new is reported:
   * the place stays the one of the pause, with the time of the pause. (Reporting it again "now" would make an old place the
   * newest, and take the place of a device that listened on meanwhile.)
   */
  const flush = useCallback(
    (leaving: boolean) => {
      const running = engine.current
      if (running && store.current.get().playing) {
        report(running.position)
        keepLocal(running.position)
      }
      void saver.current?.flush(leaving)
    },
    [report, keepLocal],
  )

  // While it plays, the place is reported with every move of the engine (the saver sends it every ten seconds).
  useEffect(() => {
    if (engineState.playing) report(engineState.position)
  }, [engineState.position, engineState.playing, report])

  // Paused (by the person, the system, the end, the sleep timer): the place of the pause is saved at once, and the moment kept
  // for the step back.
  const wasPlaying = useRef(false)
  useEffect(() => {
    if (wasPlaying.current && !engineState.playing) {
      heardAt.current = Date.now()
      const position = engine.current?.position ?? engineState.position
      report(position)
      keepLocal(position)
      void saver.current?.flush(false)
    }
    wasPlaying.current = engineState.playing
    // Only when it stops playing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineState.playing, report, keepLocal])

  useEffect(() => {
    const hide = () => {
      if (document.visibilityState === 'hidden') flush(true)
    }
    const leave = () => flush(true)
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', leave)
    return () => {
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', leave)
    }
  }, [flush])

  // ---- Opening and closing ------------------------------------------------------------------------------------------------------
  const say = useCallback((text: string) => setSaid(text), [])
  useEffect(() => {
    if (!said) return
    const gone = window.setTimeout(() => setSaid(null), 3500)
    return () => window.clearTimeout(gone)
  }, [said])

  const shut = useCallback(() => {
    flush(false)
    saver.current?.stop()
    saver.current = null
    engine.current?.stop()
    timer.current.cancel()
    setSleep({ kind: 'off' })
    store.current.set(IDLE)
    setBook(null)
    setHint(null)
    setBookmarks([])
    armed.current = false
    heardAt.current = null
  }, [flush])

  const open = useCallback(
    async (editionId: number, options: { expand?: boolean; play?: boolean; quiet?: boolean } = {}) => {
      const user = meRef.current
      if (!user) return
      // Still in the person's tap: the elements may play later, also after the answers of the server (Safari).
      if (options.play) engine.current?.unlock()
      if (options.expand) expandRef.current()
      const current = bookRef.current
      if (current && current.edition.id === editionId) {
        // An open question of another device's place is answered first (the big view shows it), not played over.
        if (options.play && !hintRef.current) playRef.current()
        else if (hintRef.current) expandRef.current()
        return
      }
      const ticket = ++opening.current
      setLoading(true)
      try {
        const [found, server, marks] = await Promise.all([readingApi.edition(editionId), readingApi.progress(editionId), readingApi.bookmarks(editionId).catch(() => [])])
        if (ticket !== opening.current) return
        if (found.edition.kind !== 'audio') throw new ApiError(404, 'not_found')
        const received = Date.now()
        if (bookRef.current) shut()
        const parts = partsOf(found.edition.files)
        const total = totalOf(parts) || found.edition.duration
        const spans = spansOf(found.edition.chapters, total, found.work.title)
        const unsupported = unplayable([...new Set(parts.map((part) => part.format))], browserProbe())
        const next: PlayerBook = { work: found.work, edition: found.edition, parts, spans, total, unsupported }
        const local = loadLocal(user.id, editionId)
        const opened = chooseStart(server, local, device.key, received)
        const start = secondsOf(opened.start.locator) ?? 0
        // When this device last heard the book: its own copy, else the server's place counted back on this device's clock.
        heardAt.current = local ? local.at : server ? received - server.age * 1000 : null
        armed.current = false
        saver.current = new ProgressSaver(
          (body, keep) => readingApi.save(editionId, body, keep),
          device,
          (result) => {
            // A newer place from another device was there: the person is told, and nothing of ours is saved until they answer.
            const ours = engine.current?.position ?? 0
            const theirs = result.progress.position ?? 0
            if (!result.applied && result.progress.device_key !== device.key && theirs - ours > 5) {
              setHint(result.progress)
              saver.current?.hold()
            }
          },
          { interval: SAVE_INTERVAL_MS },
        )
        if (opened.ask) saver.current.hold()
        setHint(opened.ask)
        setBookmarks(Array.isArray(marks) ? marks : [])
        setBook(next)
        bookRef.current = next
        rememberPlayer(user.id, editionId)
        if (parts.length > 0 && parts.every((part) => unsupported.includes(part.format))) {
          // Nothing of it can be played here: said at once, without asking for the file.
          store.current.set({ ...IDLE, position: start, failure: 'unsupported' })
        } else {
          engine.current?.load(parts, start)
          if (options.play && !opened.ask) playRef.current()
        }
      } catch (error) {
        if (ticket !== opening.current) return
        if (options.quiet) {
          // The book of the last visit is gone (or out of reach): the player does not come back with it.
          rememberPlayer(user.id, null)
        } else {
          say(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
        }
      } finally {
        if (ticket === opening.current) setLoading(false)
      }
    },
    [device, shut, say],
  )

  const close = useCallback(() => {
    const user = meRef.current
    opening.current += 1
    collapseRef.current()
    shut()
    if (user) rememberPlayer(user.id, null)
  }, [shut])

  // Signed out (here or because the session ended): nothing plays on for the next person.
  const userId = me?.id ?? null
  useEffect(() => {
    if (userId === null && bookRef.current) {
      opening.current += 1
      shut()
    }
  }, [userId, shut])

  // After a reload the small player comes back with the last recording of this person on this device, paused.
  const restored = useRef<number | null>(null)
  useEffect(() => {
    if (userId === null || restored.current === userId) return
    restored.current = userId
    const last = rememberedPlayer(userId)
    if (last !== null && !bookRef.current) void open(last, { quiet: true })
  }, [userId, open])

  // ---- Playing ----------------------------------------------------------------------------------------------------------------------
  const closeHint = useCallback(() => {
    setHint(null)
    saver.current?.release()
  }, [])

  const play = useCallback(() => {
    const running = engine.current
    if (!running || !bookRef.current) return
    // Playing is the answer "here" to the question of another device's place.
    if (hintRef.current) closeHint()
    const pausedFor = heardAt.current === null ? null : Date.now() - heardAt.current
    const position = running.position
    const from = resumeFrom(position, pausedFor, choicesRef.current.rewind)
    if (from !== position) running.seek(from)
    heardAt.current = null
    armed.current = true
    running.play()
  }, [closeHint])

  playRef.current = play
  const pause = useCallback(() => engine.current?.pause(), [])
  const toggle = useCallback(() => {
    if (store.current.get().playing) pause()
    else play()
  }, [play, pause])

  const seek = useCallback(
    (seconds: number) => {
      const running = engine.current
      const current = bookRef.current
      if (!running || !current) return
      armed.current = true
      heardAt.current = null
      if (hintRef.current) closeHint()
      running.seek(seconds)
      // A jump of the person moves the end of a timer "at the end of the chapter" to the end of the chapter it led to.
      const index = Math.max(0, chapterAt(current.spans, seconds))
      timer.current.jumped(current.spans[index]?.end ?? current.total)
      // A jump of the person is a place of this moment, also while it is paused.
      report(running.position)
      keepLocal(running.position)
      if (!store.current.get().playing) void saver.current?.flush(false)
    },
    [closeHint, report, keepLocal],
  )

  const skip = useCallback((delta: number) => seek((engine.current?.position ?? 0) + delta), [seek])
  const chapterBack = useCallback(() => {
    const current = bookRef.current
    if (current) seek(previousChapterStart(current.spans, engine.current?.position ?? 0))
  }, [seek])
  const chapterAhead = useCallback(() => {
    const current = bookRef.current
    if (!current) return
    const next = nextChapterStart(current.spans, engine.current?.position ?? 0)
    if (next !== null) seek(next)
  }, [seek])

  // The speed is saved with the account a moment after the last change (a slider sends many).
  const speedSave = useRef<ReturnType<typeof setTimeout> | null>(null)
  const setSpeed = useCallback(
    (value: number) => {
      const next = clampSpeed(value)
      setSpeedState(next)
      speedRef.current = next
      engine.current?.setSpeed(next)
      if (speedSave.current) clearTimeout(speedSave.current)
      speedSave.current = setTimeout(() => {
        speedSave.current = null
        authApi.preferences({ player_speed: speedToProfile(next) }).then(
          (profile) => meRef.current && setMe({ ...meRef.current, profile }),
          () => undefined,
        )
      }, 600)
    },
    [setMe],
  )

  const changeVolume = useCallback((next: Volume) => {
    setVolumeState(next)
    volumeRef.current = next
    engine.current?.setVolume(next.muted ? 0 : next.level)
    saveVolume(next)
  }, [])
  // Moving the slider is a wish to hear: it ends a mute, and a mute at level 0 comes back at a quarter.
  const setVolume = useCallback((value: number) => changeVolume({ level: clampVolume(value), muted: false }), [changeVolume])
  const toggleMute = useCallback(() => {
    const now = volumeRef.current
    if (now.muted || now.level === 0) changeVolume({ level: now.level === 0 ? 0.25 : now.level, muted: false })
    else changeVolume({ ...now, muted: true })
  }, [changeVolume])

  const setChoices = useCallback(
    (change: Partial<Choices>) => {
      setChoicesState((now) => ({ ...now, ...change }))
      const body: Partial<Profile> = {}
      if (change.back !== undefined) body.player_back = change.back
      if (change.ahead !== undefined) body.player_ahead = change.ahead
      if (change.rewind !== undefined) body.player_rewind = change.rewind
      authApi.preferences(body).then(
        (profile) => meRef.current && setMe({ ...meRef.current, profile }),
        () => undefined,
      )
    },
    [setMe],
  )

  // ---- The sleep timer ----------------------------------------------------------------------------------------------------------------
  // A beat four times a second, and one with every move of the recording: a browser slows the clocks of a page in the background
  // down (to once a minute in Chrome), but goes on telling the page where its sound is.
  const sleepBeat = useCallback(() => {
    const running = engine.current
    if (!running || !timer.current.active) return
    const state = store.current.get()
    const verdict = timer.current.tick({ playing: state.playing, position: running.position, speed: speedRef.current })
    running.setFade(verdict.volume)
    if (verdict.stop) {
      running.pause()
      running.setFade(1)
      // At the end of a chapter the place is its end, so that the next start is the next chapter's.
      if (verdict.state.kind === 'chapter') running.seek(verdict.state.end)
      setSleep({ kind: 'off' })
      say(t('player.sleep.done'))
      return
    }
    setSleep(verdict.state)
  }, [say, t])
  useEffect(() => {
    if (sleep.kind === 'off') return
    const beat = window.setInterval(sleepBeat, 250)
    return () => window.clearInterval(beat)
  }, [sleep.kind, sleepBeat])
  useEffect(() => {
    if (sleep.kind !== 'off') sleepBeat()
    // With every move of the recording.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineState.position])

  const sleepFor = useCallback((minutes: number) => {
    timer.current.minutes(minutes)
    engine.current?.setFade(1)
    setSleep(timer.current.state())
  }, [])
  const sleepAtChapterEnd = useCallback(() => {
    const current = bookRef.current
    if (!current) return
    const position = engine.current?.position ?? 0
    const index = Math.max(0, chapterAt(current.spans, position))
    timer.current.chapterEnd(current.spans[index]?.end ?? current.total)
    engine.current?.setFade(1)
    setSleep(timer.current.state(position, speedRef.current))
  }, [])
  const sleepOff = useCallback(() => {
    timer.current.cancel()
    engine.current?.setFade(1)
    setSleep({ kind: 'off' })
  }, [])

  // ---- The question of another device ----------------------------------------------------------------------------------------------
  const jumpToHint = useCallback(() => {
    const target = hintRef.current
    closeHint()
    if (target?.position !== null && target?.position !== undefined) seek(target.position)
  }, [closeHint, seek])
  const stayHere = useCallback(() => closeHint(), [closeHint])

  // ---- Bookmarks ------------------------------------------------------------------------------------------------------------------------
  const marking = useRef(false)
  const bookmarksRef = useRef(bookmarks)
  bookmarksRef.current = bookmarks
  const addBookmark = useCallback(async (): Promise<BookmarkOut | null> => {
    const current = bookRef.current
    const running = engine.current
    if (!current || !running || marking.current) return null
    const position = running.position
    const near = bookmarksRef.current.find((mark) => Math.abs((mark.position ?? -1e9) - position) < SAME_MARK_SECONDS)
    if (near) {
      say(t('player.marks.here'))
      return near
    }
    marking.current = true
    try {
      const index = Math.max(0, chapterAt(current.spans, position))
      const made = await readingApi.addBookmark(current.edition.id, {
        locator: timeLocator(position),
        fraction: current.total > 0 ? Math.min(1, position / current.total) : 0,
        chapter_title: current.spans[index]?.title ?? '',
      })
      setBookmarks((list) => [...list.filter((mark) => mark.id !== made.id), made].sort((a, b) => (a.position ?? 0) - (b.position ?? 0) || a.id - b.id))
      say(t('player.marks.set'))
      return made
    } catch (error) {
      say(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
      return null
    } finally {
      marking.current = false
    }
  }, [say, t])

  const saveNote = useCallback(
    async (id: number, note: string): Promise<boolean> => {
      try {
        const changed = await readingApi.changeNote(id, note)
        setBookmarks((list) => list.map((mark) => (mark.id === id ? changed : mark)))
        return true
      } catch (error) {
        say(error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error'))
        return false
      }
    },
    [say],
  )

  const removeBookmark = useCallback(async (id: number) => {
    await readingApi.removeBookmark(id)
    setBookmarks((list) => list.filter((mark) => mark.id !== id))
  }, [])

  // ---- The big view: a step in the history, so that the way back of the browser folds it up ---------------------------------------
  // Open while the history entry says so and there is a recording (or one on its way). A reload in the big view opens it again;
  // a link to another page leaves it folded.
  const locationState = location.state as Record<string, unknown> | null
  const locationPlayer = Boolean(locationState?.player)
  const expanded = locationPlayer && (book !== null || loading)
  // A second press before the first step is in the history (a double click on "Listen") makes no second step: with two of
  // them one "back" would fold nothing up. The lock ends when the step is there, and after a moment in any case.
  const expanding = useRef(false)
  useEffect(() => {
    expanding.current = false
  }, [locationPlayer])
  const expand = useCallback(() => {
    if (locationPlayer || expanding.current) return
    expanding.current = true
    window.setTimeout(() => (expanding.current = false), 800)
    navigate(location.pathname + location.search, { state: { ...(locationState ?? {}), player: true } })
  }, [navigate, location.pathname, location.search, locationState, locationPlayer])
  const collapse = useCallback(() => {
    // The step was made by `expand`: going back takes it away, and the page is as it was.
    if (locationPlayer) navigate(-1)
  }, [navigate, locationPlayer])
  expandRef.current = expand
  collapseRef.current = collapse

  const retry = useCallback(() => engine.current?.retry(), [])
  const skipPart = useCallback(() => {
    const running = engine.current
    if (!running) return
    // The person goes on past the broken part: that is a place of its own to keep, and it is heard at once.
    armed.current = true
    heardAt.current = null
    running.skipFailedPart()
    report(running.position)
    keepLocal(running.position)
  }, [report, keepLocal])

  // ---- The lock screen (Media Session) ---------------------------------------------------------------------------------------------------
  const span = book?.spans[chapter]
  useEffect(() => {
    const session = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined
    if (!session || typeof MediaMetadata === 'undefined') return
    if (!book) {
      session.metadata = null
      return
    }
    const artwork = book.work.cover_url ? [{ src: new URL(book.work.cover_url, window.location.origin).href, sizes: '512x512', type: 'image/webp' }] : []
    session.metadata = new MediaMetadata({
      title: span?.title || book.work.title,
      artist: book.work.authors.map((author) => author.name).join(', '),
      album: book.work.title,
      artwork,
    })
  }, [book, span?.title])

  const actions = useMemo<PlayerActions>(
    () => ({ open, toggle, play, pause, seek, skip, chapterBack, chapterAhead, setSpeed, setVolume, toggleMute, sleepFor, sleepAtChapterEnd, sleepOff, expand, collapse, close, jumpToHint, stayHere, setChoices, addBookmark, saveNote, removeBookmark, retry, skipPart }),
    [open, toggle, play, pause, seek, skip, chapterBack, chapterAhead, setSpeed, setVolume, toggleMute, sleepFor, sleepAtChapterEnd, sleepOff, expand, collapse, close, jumpToHint, stayHere, setChoices, addBookmark, saveNote, removeBookmark, retry, skipPart],
  )

  useEffect(() => {
    const session = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined
    if (!session || !book) return
    const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
      ['play', () => play()],
      ['pause', () => pause()],
      ['seekbackward', (details) => skip(-(details.seekOffset ?? choicesRef.current.back))],
      ['seekforward', (details) => skip(details.seekOffset ?? choicesRef.current.ahead)],
      // The buttons ahead and back of headphones and the lock screen jump, they do not change the book.
      ['previoustrack', () => skip(-choicesRef.current.back)],
      ['nexttrack', () => skip(choicesRef.current.ahead)],
      [
        'seekto',
        (details) => {
          const current = bookRef.current
          if (!current || details.seekTime === undefined) return
          const index = Math.max(0, chapterAt(current.spans, engine.current?.position ?? 0))
          seek((current.spans[index]?.start ?? 0) + details.seekTime)
        },
      ],
    ]
    for (const [action, handler] of handlers) {
      try {
        session.setActionHandler(action, handler)
      } catch {
        // A browser that does not know an action.
      }
    }
    return () => {
      for (const [action] of handlers) {
        try {
          session.setActionHandler(action, null)
        } catch {
          // As above.
        }
      }
    }
  }, [book, play, pause, skip, seek])

  useEffect(() => {
    const session = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined
    if (!session) return
    session.playbackState = !book ? 'none' : engineState.playing ? 'playing' : 'paused'
    if (!book || !span || typeof session.setPositionState !== 'function') return
    const duration = Math.max(0.1, span.end - span.start)
    try {
      session.setPositionState({ duration, playbackRate: speed, position: Math.min(duration, Math.max(0, engineState.position - span.start)) })
    } catch {
      // A position the browser does not take (a length it measured differently): the lock screen keeps the last one.
    }
  }, [book, span, engineState.playing, engineState.position, speed])

  const live = useMemo<PlayerLive>(
    () => ({ book, loading, engine: engineState, speed, volume: { ...volume, settable }, sleep, hint, expanded, choices, bookmarks, said, chapter }),
    [book, loading, engineState, speed, volume, settable, sleep, hint, expanded, choices, bookmarks, said, chapter],
  )

  return (
    <ActionsContext.Provider value={actions}>
      <LiveContext.Provider value={live}>
        {children}
        {/* The two elements that play: one the part that plays, one the next part, loaded before it is needed. */}
        <audio ref={first} hidden preload="none" data-testid="player-audio" />
        <audio ref={second} hidden preload="none" data-testid="player-audio" />
      </LiveContext.Provider>
    </ActionsContext.Provider>
  )
}

/** Outside the provider (a part of a page drawn alone, in a test) there is no player: nothing plays and nothing is asked. */
const nothing = async () => undefined
const NO_ACTIONS: PlayerActions = {
  open: nothing, toggle: () => undefined, play: () => undefined, pause: () => undefined, seek: () => undefined, skip: () => undefined,
  chapterBack: () => undefined, chapterAhead: () => undefined, setSpeed: () => undefined, setVolume: () => undefined, toggleMute: () => undefined, sleepFor: () => undefined,
  sleepAtChapterEnd: () => undefined, sleepOff: () => undefined, expand: () => undefined, collapse: () => undefined, close: () => undefined,
  jumpToHint: () => undefined, stayHere: () => undefined, setChoices: () => undefined, addBookmark: async () => null,
  saveNote: async () => false, removeBookmark: nothing, retry: () => undefined, skipPart: () => undefined,
}
const NO_LIVE: PlayerLive = {
  book: null, loading: false, engine: IDLE, speed: 1, volume: { level: 1, muted: false, settable: false }, sleep: { kind: 'off' }, hint: null, expanded: false,
  choices: { back: 15, ahead: 30, rewind: true }, bookmarks: [], said: null, chapter: 0,
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePlayer(): PlayerActions {
  return useContext(ActionsContext) ?? NO_ACTIONS
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePlayerLive(): PlayerLive {
  return useContext(LiveContext) ?? NO_LIVE
}
