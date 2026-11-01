import { failureOf, isWebKit, unplayable } from './audioFormats'

const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'
const IPHONE_CHROME = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0 Mobile/15E148 Safari/604.1'
const CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
const FIREFOX = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36'

describe('which browser says what about a file it does not play', () => {
  it('knows WebKit: Safari, and every browser on an iPhone or iPad, but not Chrome, Edge, Firefox or Android', () => {
    expect(isWebKit(SAFARI)).toBe(true)
    expect(isWebKit(IPHONE_CHROME)).toBe(true)
    expect(isWebKit(CHROME)).toBe(false)
    expect(isWebKit(FIREFOX)).toBe(false)
    expect(isWebKit(ANDROID)).toBe(false)
    expect(isWebKit('')).toBe(false)
    // The engine of the tests says AppleWebKit and nothing of Safari.
    expect(isWebKit('Mozilla/5.0 (win32) AppleWebKit/537.36 (KHTML, like Gecko) jsdom/26.1.0')).toBe(false)
  })

  it('does not call a file damaged when WebKit says "not supported" (it says that for a file it could not fetch, too)', () => {
    expect(failureOf(4, true, true)).toBe('browser')
    expect(failureOf(4, true, false)).toBe('broken')
    // A format the browser says it does not know is told as that, wherever it is.
    expect(failureOf(4, false, true)).toBe('unsupported')
    expect(failureOf(4, false, false)).toBe('unsupported')
    expect(failureOf(2, true, true)).toBe('network')
    expect(failureOf(undefined, true, true)).toBe('broken')
  })

  it('lists the formats a browser gives an empty answer for', () => {
    expect(unplayable(['mp3', 'ogg', 'flac'], (type) => (type === 'audio/mpeg' ? 'probably' : ''))).toEqual(['ogg', 'flac'])
  })
})
