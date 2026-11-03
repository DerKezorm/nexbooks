/**
 * The player without a browser that plays (jsdom): the card of a recording with the person's place, "Listen on" opening the big
 * player at that place, the question when another device is further along (and nothing saved until it is answered), the
 * speed kept with the account, a bookmark deleted only after the question, and the player gone when the person signs out. The
 * audio elements are jsdom's; the test says when the browser "knows" a part (`loadedmetadata`). (Real playing is in
 * `e2e/player.spec.ts`.)
 */
import { act } from 'react'
import { MemoryRouter } from 'react-router-dom'

import type { BookmarkOut, EditionOut, Me, ProgressOut } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { recording, workOut, edition as editionOf, work } from '../../test/books'
import { Page, Server } from '../../test/harness'
import { idle } from '../../test/wait'
import { PlayerProvider, usePlayer } from '../../state/player'
import { Twins } from '../Twins'
import { PlayerChrome } from './PlayerChrome'

const me = { id: 7, name: 'jule', display_name: 'Jule', role: 'member', language: 'de', profile: { theme: 'light', view: 'shelf', player_speed: 100, player_back: 15, player_ahead: 30, player_rewind: true } } as unknown as Me
const auth: { me: Me | null; setMe: ReturnType<typeof vi.fn> } = { me, setMe: vi.fn() }
vi.mock('../../state/auth', () => ({ useAuth: () => auth }))

const server = new Server()
const page = new Page()

const HOURS_14 = 14 * 3600 + 3 * 60
const chapters = Array.from({ length: 38 }, (_, n) => ({ number: n + 1, title: `Kapitel ${n + 1}`, start: Math.round((n * HOURS_14) / 38), target: '', depth: 0 }))
const audio = (): EditionOut =>
  editionOf(recording(5, { duration: HOURS_14, formats: ['mp3'] }), {
    files: [
      { id: 50, format: 'mp3', path: 'a/01.mp3', size: 1000, part: 1, duration: HOURS_14 / 2, sha256: 'a'.repeat(64), protected: false },
      { id: 51, format: 'mp3', path: 'a/02.mp3', size: 1000, part: 2, duration: HOURS_14 / 2, sha256: 'b'.repeat(64), protected: false },
    ],
    chapters,
  })

function place(over: Partial<ProgressOut> = {}): ProgressOut {
  return {
    edition_id: 5, work_id: 1, locator: 'time:8040.000', position: 8040, fraction: 8040 / HOURS_14, unit: 'time', chapter: 7, chapter_count: 38, chapter_title: 'Kapitel 7',
    device_key: 'other-device', device: 'Firefox · Linux', moved_at: '2026-10-13T11:00:00Z', received_at: '2026-10-13T11:00:01Z', age: 60, ...over,
  }
}
const mark = (id: number, position: number, note = ''): BookmarkOut => ({ id, edition_id: 5, locator: `time:${position}.000`, position, fraction: position / HOURS_14, chapter_title: 'Kapitel 2', note, created_at: '2026-10-12T10:00:00Z' })

function serve(options: { progress?: ProgressOut | null; bookmarks?: BookmarkOut[] } = {}) {
  server.answers = {
    'GET /api/v1/editions/5': { work: work(1, { title: 'Der lange Weg', editions: [recording(5)] }), edition: audio() },
    'GET /api/v1/editions/5/progress': options.progress === undefined ? place() : options.progress,
    'GET /api/v1/editions/5/bookmarks': options.bookmarks ?? [],
    'PUT /api/v1/editions/5/progress': { applied: true, progress: place({ device_key: 'me' }) },
    'PUT /api/v1/me/preferences': { ...me.profile, player_speed: 150 },
    'DELETE /api/v1/bookmarks/31': 204,
  }
  server.fails = {}
  server.install()
}

const playerWork = () => workOut(1, [recording(5, { duration: HOURS_14 })], { title: 'Der lange Weg' })

async function show(places: ProgressOut[] = [place()]) {
  await page.show(
    <PlayerProvider>
      <Twins work={playerWork()} places={places} />
      <PlayerChrome />
    </PlayerProvider>,
  )
}

const audios = () => [...document.querySelectorAll<HTMLAudioElement>('[data-testid="player-audio"]')]

/** The browser has read the head of the part the element holds. */
async function known(element: HTMLAudioElement, duration = HOURS_14 / 2) {
  Object.defineProperty(element, 'duration', { configurable: true, get: () => duration })
  await act(async () => {
    element.dispatchEvent(new Event('loadedmetadata'))
    await Promise.resolve()
  })
}

async function click(target: Element | null | undefined) {
  expect(target).toBeTruthy()
  await act(async () => {
    ;(target as HTMLElement).click()
    await Promise.resolve()
  })
  await idle()
}

let kept: Map<string, string>
let played: number

beforeEach(async () => {
  await changeLanguage('de', false)
  auth.me = me
  kept = new Map()
  played = 0
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
    key: (index: number) => [...kept.keys()][index] ?? null,
    get length() {
      return kept.size
    },
  })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
  window.HTMLElement.prototype.scrollIntoView = () => undefined
  // jsdom has elements for sound but plays nothing: playing is a promise that is kept, and `paused` follows it.
  vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    played += 1
    Object.defineProperty(this, 'paused', { configurable: true, get: () => false })
    return Promise.resolve()
  })
  vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    Object.defineProperty(this, 'paused', { configurable: true, get: () => true })
  })
  vi.spyOn(window.HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
  vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) => (type.startsWith('audio/mpeg') ? 'probably' : ''))
})
afterEach(() => {
  page.close()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the card of the recording', () => {
  it('says where the person is in its words, with the bar, and offers to listen on', async () => {
    serve()
    await show()
    const card = document.querySelector('.twin[data-kind="audio"]') as HTMLElement
    expect(card.querySelector('.tw-pos')!.textContent).toBe('2 Std 14 Min von 14 Std 3 Min · 16 %')
    expect(card.querySelector('.tw-pos b')!.textContent).toBe('2 Std 14 Min')
    expect((card.querySelector('.bar2 i') as HTMLElement).style.width).toBe('16%')
    expect(card.querySelector('.go')!.textContent).toBe('Weiterhören')
  })

  it('says "Hören" and "Noch nicht begonnen" before the first time, and the place of the e-book is not its place', async () => {
    serve({ progress: null })
    const ebookPlace = place({ edition_id: 9, locator: 'epubcfi(/6/4)', position: null, unit: 'chapter', fraction: 0.8 })
    await show([ebookPlace])
    const card = document.querySelector('.twin[data-kind="audio"]') as HTMLElement
    expect(card.querySelector('.tw-pos')!.textContent).toBe('Noch nicht begonnen')
    expect(card.querySelector('.go')!.textContent).toBe('Hören')
  })
})

describe('opening the player', () => {
  it('opens the big player at the place of the server, in the part that holds it, and plays', async () => {
    serve()
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const full = document.querySelector('[data-testid="full-player"]') as HTMLElement
    expect(full).toBeTruthy()
    // 8040 s lie in the first of two parts of 7 h 1.5 min; chapter 7 of 38 starts at 7 * 1331 s.
    expect(audios()[0].getAttribute('src')).toBe('/api/v1/files/50/content')
    await known(audios()[0])
    expect(audios()[0].currentTime).toBe(8040)
    expect(document.querySelector('[data-testid="player-chapter-of"]')!.textContent).toBe('Kapitel 7 von 38')
    expect(played).toBeGreaterThan(0)
    expect(full.getAttribute('data-playing')).toBe('yes')
  })

  it('asks when another device is further along, plays nothing over the question and saves nothing until it is answered', async () => {
    serve()
    // This device was at 600 s two hours ago; the other device is at 8040 s, a minute ago.
    kept.set('nexbooks.read.7.5', JSON.stringify({ locator: 'time:600.000', fraction: 0.01, at: Date.now() - 7_200_000 }))
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const hint = document.querySelector('[data-testid="player-hint"]') as HTMLElement
    expect(hint.textContent).toContain('Auf Firefox · Linux bist du weiter: bei 2:14:00. Dorthin springen?')
    await known(audios()[0])
    // Its own place first.
    expect(audios()[0].currentTime).toBe(600)
    expect(document.querySelector('[data-testid="full-player"]')!.getAttribute('data-playing')).toBe('no')
    expect(server.changes()).toEqual([])
    await click(document.querySelector('[data-testid="player-hint-jump"]'))
    expect(document.querySelector('[data-testid="player-hint"]')).toBeNull()
    expect(audios()[0].currentTime).toBe(8040)
    // The jump is the person's: their place is saved at once (it is paused).
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
    await idle()
    const saved = server.calls.filter((call) => call.method === 'PUT' && call.url === '/api/v1/editions/5/progress')
    expect(saved.map((call) => call.body?.locator)).toEqual(['time:8040.000'])
    expect(saved[0].body).toMatchObject({ unit: 'time', chapter: 7, chapter_count: 38 })
  })

  it('a recording only opened (after a reload) saves nothing', async () => {
    serve()
    kept.set('nexbooks.read.7.player', '5')
    await show()
    expect(document.querySelector('[data-testid="mini-player"]')).toBeTruthy()
    await known(audios()[0])
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 50)))
    expect(server.changes()).toEqual([])
    expect(played).toBe(0)
  })

  it('a format this browser does not play is said at once, with the file to download, and nothing is asked for', async () => {
    serve()
    vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation(() => '')
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const failed = document.querySelector('[data-testid="player-failed"]') as HTMLElement
    expect(failed.getAttribute('data-reason')).toBe('unsupported')
    expect(failed.textContent).toContain('Dieser Browser spielt MP3 nicht ab.')
    expect(audios().every((element) => !element.getAttribute('src'))).toBe(true)
    expect(failed.querySelectorAll('a[download]').length).toBe(2)
  })
})

describe('a part that does not play', () => {
  async function breakFirstPart() {
    serve({ progress: null })
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const element = audios()[0]
    Object.defineProperty(element, 'error', { configurable: true, get: () => ({ code: 4 }) })
    await act(async () => {
      element.dispatchEvent(new Event('error'))
      await Promise.resolve()
    })
    return element
  }

  it('names the part, offers to skip it, and a jump to a chapter of another part sets the message aside', async () => {
    await breakFirstPart()
    const failed = document.querySelector('[data-testid="player-failed"]') as HTMLElement
    expect(failed.getAttribute('data-reason')).toBe('broken')
    expect(document.querySelector('[data-testid="player-failed-part"]')?.textContent).toBe('Teil 1 von 2')
    expect(document.querySelector('[data-testid="player-skip-part"]')).toBeTruthy()
    expect((document.querySelector('[data-testid="player-play"]') as HTMLButtonElement).disabled).toBe(true)
    // Chapter 20 lies in the second part.
    await click(document.querySelector('[data-testid="open-chapters"]'))
    await click(document.querySelectorAll('[data-testid="chapter-item"]')[19])
    expect(document.querySelector('[data-testid="player-failed"]')).toBeNull()
    expect(audios().some((element) => element.getAttribute('src') === '/api/v1/files/51/content')).toBe(true)
    expect((document.querySelector('[data-testid="player-play"]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('skipping goes to the next part, and the last part has nothing to skip to', async () => {
    await breakFirstPart()
    await click(document.querySelector('[data-testid="player-skip-part"]'))
    expect(document.querySelector('[data-testid="player-failed"]')).toBeNull()
    const second = audios().find((element) => element.getAttribute('src') === '/api/v1/files/51/content')!
    expect(second).toBeTruthy()
    Object.defineProperty(second, 'error', { configurable: true, get: () => ({ code: 4 }) })
    await act(async () => {
      second.dispatchEvent(new Event('error'))
      await Promise.resolve()
    })
    expect(document.querySelector('[data-testid="player-failed-part"]')?.textContent).toBe('Teil 2 von 2')
    expect(document.querySelector('[data-testid="player-skip-part"]')).toBeNull()
  })
})

describe('saving the place', () => {
  it('saves nothing of a recording the person has not played, also when it is opened while another one plays', async () => {
    serve()
    // A second recording (edition 6) with a place of its own on the server: 700 s, from another device, long ago.
    server.answers['GET /api/v1/editions/6'] = {
      work: work(2, { title: 'Das zweite Buch', editions: [recording(6)] }),
      edition: { ...audio(), id: 6, files: audio().files.map((file) => ({ ...file, id: file.id + 10 })) },
    }
    server.answers['GET /api/v1/editions/6/progress'] = place({ edition_id: 6, work_id: 2, locator: 'time:700.000', position: 700, age: 86_400 * 3, device_key: 'me' })
    server.answers['GET /api/v1/editions/6/bookmarks'] = []
    server.answers['PUT /api/v1/editions/6/progress'] = { applied: true, progress: place({ edition_id: 6, device_key: 'me' }) }
    function SecondBook() {
      const player = usePlayer()
      return (
        <button type="button" data-testid="open-second" onClick={() => void player.open(6)}>
          second
        </button>
      )
    }
    await page.show(
      <PlayerProvider>
        <Twins work={playerWork()} places={[place()]} />
        <SecondBook />
        <PlayerChrome />
      </PlayerProvider>,
    )
    await click(document.querySelector('[data-testid="listen"]'))
    await known(audios()[0])
    expect(document.querySelector('[data-testid="full-player"]')!.getAttribute('data-playing')).toBe('yes')
    // The first one plays; the second is opened, not played. Its place on the server is the one of the other device and stays.
    await click(document.querySelector('[data-testid="open-second"]'))
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 60)))
    await idle()
    await known(audios()[0], HOURS_14 / 2)
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 60)))
    await idle()
    const saved = (edition: number) => server.calls.filter((call) => call.method === 'PUT' && call.url === `/api/v1/editions/${edition}/progress`)
    // The first one's place was kept when it was left (it was heard); nothing was sent for the second one.
    expect(saved(5).length).toBeGreaterThanOrEqual(1)
    expect(saved(6)).toEqual([])
    expect(kept.get('nexbooks.read.7.6')).toBeUndefined()
  })

  it('saves the place of a pause once, and leaving the page later does not report it again as new', async () => {
    serve()
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    await known(audios()[0])
    audios()[0].currentTime = 8100
    // Paused by the person: the place of the pause is sent at once.
    await click(document.querySelector('[data-testid="player-play"]'))
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 20)))
    await idle()
    const saves = () => server.calls.filter((call) => call.method === 'PUT' && call.url === '/api/v1/editions/5/progress')
    expect(saves().map((call) => call.body?.locator)).toEqual(['time:8100.000'])
    // Later the page goes (a closed tab): nothing new to say. A report "now" would make the old place the newest of all devices.
    await act(async () => {
      window.dispatchEvent(new Event('pagehide'))
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    await idle()
    expect(saves()).toHaveLength(1)
    expect(JSON.parse(kept.get('nexbooks.read.7.5') ?? '{}').locator).toBe('time:8100.000')
  })
})

describe('in the big player', () => {
  it('keeps the speed with the account, as hundredths', async () => {
    serve()
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    await click(document.querySelector('[data-testid="open-speed"]'))
    await click(document.querySelector('[data-testid="speed-1.5"]'))
    expect(audios()[0].playbackRate).toBe(1.5)
    expect(document.querySelector('[data-testid="speed-value"]')!.textContent).toBe('1,5×')
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 700)))
    await idle()
    expect(server.called('PUT', '/api/v1/me/preferences')?.body).toEqual({ player_speed: 150 })
  })

  it('keeps the volume on this device, mutes and comes back at the level it had, and asks the server nothing', async () => {
    serve()
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const slider = document.querySelector<HTMLInputElement>('[data-testid="volume-slider"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(slider, '0.4')
      slider.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(audios()[0].volume).toBeCloseTo(0.4)
    expect(document.querySelector('[data-testid="volume-value"]')!.textContent).toBe('40 %')
    expect(JSON.parse(kept.get('nexbooks.player.volume') ?? '{}')).toEqual({ level: 0.4, muted: false })
    await click(document.querySelector('[data-testid="volume-mute"]'))
    expect(audios()[0].volume).toBe(0)
    expect(document.querySelector('[data-testid="volume-value"]')!.textContent).toBe('0 %')
    await click(document.querySelector('[data-testid="volume-mute"]'))
    expect(audios()[0].volume).toBeCloseTo(0.4)
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 700)))
    await idle()
    expect(server.called('PUT', '/api/v1/me/preferences')).toBeUndefined()
  })

  it('deletes a bookmark only after the question, and says that it cannot be undone', async () => {
    serve({ bookmarks: [mark(31, 1500, 'die Brücke')] })
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    await click(document.querySelector('[data-testid="open-marks"]'))
    expect(document.querySelector('[data-testid="mark-note"]')!.textContent).toBe('die Brücke')
    await click(document.querySelector('[data-testid="mark-delete"]'))
    const dialog = document.querySelector('[role="dialog"][aria-label="Lesezeichen löschen?"]') as HTMLElement
    expect(dialog.textContent).toContain('Das lässt sich nicht zurückholen.')
    expect(server.changes()).not.toContain('DELETE /api/v1/bookmarks/31')
    await click(page.button('Abbrechen', dialog))
    expect(server.changes()).not.toContain('DELETE /api/v1/bookmarks/31')
    expect(document.querySelectorAll('[data-testid="mark-item"]').length).toBe(1)
    await click(document.querySelector('[data-testid="mark-delete"]'))
    const again = document.querySelector('[role="dialog"][aria-label="Lesezeichen löschen?"]') as HTMLElement
    await click(page.button('Löschen', again))
    expect(server.changes()).toContain('DELETE /api/v1/bookmarks/31')
    expect(document.querySelectorAll('[data-testid="mark-item"]').length).toBe(0)
  })

  it('enters the recording as finished with one press, and takes the entry back only after the question', async () => {
    const entry = { id: 3, work_id: 1, edition_id: 5, kind: 'listened', finished_on: '2026-10-13', source: 'hand', created_at: '2026-10-13T10:00:00Z' }
    serve()
    server.answers['GET /api/v1/history?work=1'] = []
    server.answers['POST /api/v1/editions/5/finished'] = entry
    server.answers['DELETE /api/v1/history/3'] = 204
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    const button = () => document.querySelector('[data-testid="player-finish"]') as HTMLButtonElement
    expect(button().getAttribute('aria-pressed')).toBe('false')
    await act(async () => {
      button().click()
      button().click()
    })
    await idle()
    expect(server.changes().filter((c) => c.includes('finished'))).toEqual(['POST /api/v1/editions/5/finished'])
    expect(button().getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelector('[data-testid="player-finish-note"]')!.textContent).toBe('Als beendet eingetragen.')
    await click(button())
    const dialog = document.querySelector('[role="dialog"][aria-label="Als nicht beendet markieren?"]') as HTMLElement
    expect(dialog.textContent).toContain('Du kannst das Buch jederzeit wieder als beendet markieren.')
    expect(server.changes().filter((c) => c.includes('history'))).toEqual([])
    await click(page.button('Nicht beendet', dialog))
    expect(server.changes().filter((c) => c.includes('history'))).toEqual(['DELETE /api/v1/history/3'])
    expect(button().getAttribute('aria-pressed')).toBe('false')
  })

  it('goes when the person signs out, and nothing plays on for the next one', async () => {
    serve()
    await show()
    await click(document.querySelector('[data-testid="listen"]'))
    await known(audios()[0])
    expect(document.querySelector('[data-testid="full-player"]')).toBeTruthy()
    // The same page drawn again, now without a person (the session ended).
    auth.me = null
    await act(async () =>
      page.root.render(
        <MemoryRouter initialEntries={['/']}>
          <PlayerProvider>
            <Twins work={playerWork()} places={[]} />
            <PlayerChrome />
          </PlayerProvider>
        </MemoryRouter>,
      ),
    )
    await idle()
    expect(audios().every((element) => !element.getAttribute('src'))).toBe(true)
    expect(document.querySelector('[data-testid="full-player"]')).toBeNull()
    expect(document.querySelector('[data-testid="mini-player"]')).toBeNull()
  })
})
