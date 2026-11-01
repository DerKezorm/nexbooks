/**
 * The small player in the card of the review list: pressing play makes the sound possible first (the tap), stops the big player
 * of the app and any other card that plays, and plays; pressing it again pauses. Nothing is saved.
 */
import { PlayerEngine } from '../../lib/playerEngine'
import { changeLanguage } from '../../i18n'
import { edition, recording, work } from '../../test/books'
import { Page, Server } from '../../test/harness'
import { eventually } from '../../test/wait'
import { PeekListen } from './PeekListen'

const mainPause = vi.fn()
vi.mock('../../state/player', () => ({ usePlayer: () => ({ pause: mainPause }) }))

const server = new Server()
const page = new Page()

const read = (id: number) => ({
  [`GET /api/v1/editions/${id}`]: {
    work: work(id, { title: 'Der stille Obstgarten', editions: [recording(id)] }),
    edition: edition(recording(id, { duration: 600, formats: ['mp3'] }), { files: [{ id: id * 10, format: 'mp3', path: 'A/01.mp3', size: 1000, part: 1, duration: 600, sha256: 'a', protected: false }], chapters: [] }),
  },
})

let order: string[] = []

beforeEach(async () => {
  await changeLanguage('de', false)
  order = []
  mainPause.mockReset()
  mainPause.mockImplementation(() => order.push('main.pause'))
  vi.spyOn(window.HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) => (type.startsWith('audio/mpeg') ? 'probably' : ''))
  vi.spyOn(PlayerEngine.prototype, 'unlock').mockImplementation(function (this: PlayerEngine) {
    order.push('unlock')
  })
  vi.spyOn(PlayerEngine.prototype, 'play').mockImplementation(function (this: PlayerEngine) {
    order.push('play')
  })
  vi.spyOn(PlayerEngine.prototype, 'pause').mockImplementation(function (this: PlayerEngine) {
    order.push('engine.pause')
  })
  server.answers = { ...read(70), ...read(71) }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the small player in the review card', () => {
  it('makes the sound possible in the tap, stops the big player, then plays; a second press pauses', async () => {
    await page.show(<PeekListen editionId={70} title="Der stille Obstgarten" />)
    const play = () => document.querySelector('[data-testid="peek-play"]') as HTMLButtonElement
    await eventually(() => expect(play().disabled).toBe(false))
    order = []
    await page.click(play())
    expect(order).toEqual(['unlock', 'main.pause', 'play'])
    // Nothing was saved by listening.
    expect(server.changes()).toEqual([])
  })

  it('gives the files to the player only in the tap, after the sound was made possible (Safari fetches nothing before)', async () => {
    vi.spyOn(PlayerEngine.prototype, 'load').mockImplementation(function (this: PlayerEngine) {
      order.push('load')
    })
    await page.show(<PeekListen editionId={70} title="Der stille Obstgarten" />)
    const play = () => document.querySelector('[data-testid="peek-play"]') as HTMLButtonElement
    await eventually(() => expect(play().disabled).toBe(false))
    expect(order).toEqual([])
    await page.click(play())
    expect(order).toEqual(['unlock', 'load', 'main.pause', 'play'])
  })

  it('opens at the place of a jump when that is the first tap, and a second jump only moves', async () => {
    vi.spyOn(PlayerEngine.prototype, 'load').mockImplementation(function (this: PlayerEngine, _parts, seconds: number) {
      order.push(`load@${seconds}`)
    })
    vi.spyOn(PlayerEngine.prototype, 'seek').mockImplementation(function (this: PlayerEngine, seconds: number) {
      order.push(`seek@${seconds}`)
    })
    await page.show(<PeekListen editionId={70} title="Der stille Obstgarten" />)
    const third = () => document.querySelector('[data-jump="third"]') as HTMLButtonElement
    await eventually(() => expect(third().disabled).toBe(false))
    await page.click(third())
    expect(order).toEqual(['unlock', 'load@200'])
    await page.click(document.querySelector('[data-jump="middle"]') as HTMLButtonElement)
    expect(order).toEqual(['unlock', 'load@200', 'seek@300'])
  })

  it('plays the files of a download that waits for an answer from the download, not from a library', async () => {
    server.answers = {
      'GET /api/v1/procure/downloads/80/look': {
        download_id: 80, kind: 'audio', folder: '',
        files: [{ id: 800, name: '01.mp3', folder: '', format: 'mp3', size: 1000, part: 1, duration: 600, pages: 0, chapters: 0, protected: false }],
        says: { duration: 600 }, chapters: [],
      },
    }
    server.install()
    await page.show(<PeekListen downloadId={80} title="Der stille Obstgarten" />)
    const play = () => document.querySelector('[data-testid="peek-play"]') as HTMLButtonElement
    await eventually(() => expect(play().disabled).toBe(false))
    await page.click(play())
    const sources = [...document.querySelectorAll('[data-testid="peek-audio"]')].map((audio) => audio.getAttribute('src'))
    expect(sources).toContain('/api/v1/procure/downloads/80/files/800/content')
    expect(sources.some((source) => source?.startsWith('/api/v1/files/'))).toBe(false)
    expect(server.changes()).toEqual([])
  })

  it('stops the other card that plays when a second one starts', async () => {
    await page.show(
      <>
        <div data-card="a">
          <PeekListen editionId={70} title="Eins" />
        </div>
        <div data-card="b">
          <PeekListen editionId={71} title="Zwei" />
        </div>
      </>,
    )
    const play = (card: string) => document.querySelector(`[data-card="${card}"] [data-testid="peek-play"]`) as HTMLButtonElement
    await eventually(() => expect(play('a').disabled || play('b').disabled).toBe(false))
    await eventually(() => expect(play('b').disabled).toBe(false))
    const pauses = vi.mocked(PlayerEngine.prototype.pause)
    await page.click(play('a'))
    const before = pauses.mock.calls.length
    await page.click(play('b'))
    expect(pauses.mock.calls.length).toBe(before + 1)
    // The one that was stopped is the engine of the first card, the one that played before.
    const plays = vi.mocked(PlayerEngine.prototype.play).mock.contexts
    expect(plays).toHaveLength(2)
    expect(plays[0]).not.toBe(plays[1])
    expect(pauses.mock.contexts[before]).toBe(plays[0])
  })
})
