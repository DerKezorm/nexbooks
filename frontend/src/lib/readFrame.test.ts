import {
  addressOf, chooseFile, deviceName, frameFor, fromParent, furtherAlong, isWebKit, lookOf, newDeviceKey, openable, plainName, stepSize, SIZES,
} from './readFrame'

const file = (id: number, format: string, over: Partial<{ protected: boolean; size: number }> = {}) => ({ id, format, protected: false, size: 1000, ...over })

describe('which file the reader opens', () => {
  it('takes the EPUB first, then AZW3 and MOBI, then the PDF', () => {
    expect(chooseFile([file(1, 'pdf'), file(2, 'mobi'), file(3, 'epub'), file(4, 'azw3')])).toEqual({ file: file(3, 'epub') })
    expect(chooseFile([file(1, 'pdf'), file(2, 'mobi'), file(4, 'azw3')])).toEqual({ file: file(4, 'azw3') })
    expect(chooseFile([file(1, 'pdf'), file(2, 'mobi')])).toEqual({ file: file(2, 'mobi') })
    expect(chooseFile([file(1, 'pdf')])).toEqual({ file: file(1, 'pdf') })
  })

  it('never opens a copy-protected file, and falls back to one that is open', () => {
    expect(chooseFile([file(1, 'epub', { protected: true }), file(2, 'pdf')])).toEqual({ file: file(2, 'pdf') })
    expect(chooseFile([file(1, 'epub', { protected: true }), file(2, 'azw3', { protected: true })])).toEqual({ none: 'protected' })
  })

  it('says there is nothing when there is no file the reader reads', () => {
    expect(chooseFile([])).toEqual({ none: 'nothing' })
    expect(chooseFile([file(1, 'mp3')])).toEqual({ none: 'nothing' })
  })

  it('takes the format asked for when it is there and open, and the best one else', () => {
    const files = [file(1, 'epub'), file(2, 'pdf')]
    expect(chooseFile(files, 'pdf')).toEqual({ file: file(2, 'pdf') })
    expect(chooseFile(files, 'mobi')).toEqual({ file: file(1, 'epub') })
    expect(chooseFile([file(1, 'epub'), file(2, 'pdf', { protected: true })], 'pdf')).toEqual({ file: file(1, 'epub') })
    expect(chooseFile(files, '../etc')).toEqual({ file: file(1, 'epub') })
  })

  it('has a frame for each format', () => {
    expect(frameFor('epub')).toBe('/readview.html')
    expect(frameFor('azw3')).toBe('/readview.html')
    expect(frameFor('mobi')).toBe('/readview.html')
    expect(frameFor('pdf')).toBe('/pdfview.html')
  })
})

describe('the look', () => {
  it('follows the colours of the app until a colour is chosen', () => {
    expect(lookOf({}, true, '#a', true).theme).toBe('dark')
    expect(lookOf({}, false, '#a', true).theme).toBe('light')
    expect(lookOf({ reader_theme: 'auto' }, true, '#a', true).theme).toBe('dark')
    expect(lookOf({ reader_theme: 'sepia' }, true, '#a', true).theme).toBe('sepia')
    expect(lookOf({ reader_theme: 'light' }, true, '#a', true).theme).toBe('light')
    expect(lookOf({ reader_theme: 'dark' }, false, '#a', true).theme).toBe('dark')
  })

  it('has the defaults of the profile when the person chose nothing', () => {
    expect(lookOf(undefined, false, '#8e3b46', false)).toEqual({ font: 'fraunces', size: 100, line: 150, margin: 'normal', theme: 'light', justify: false, flow: 'paged', accent: '#8e3b46', animate: false })
  })

  it('steps the size through the sizes the profile allows, and stops at the ends', () => {
    expect(stepSize(100, 1)).toBe(110)
    expect(stepSize(100, -1)).toBe(90)
    expect(stepSize(SIZES[0], -1)).toBe(SIZES[0])
    expect(stepSize(SIZES[SIZES.length - 1], 1)).toBe(SIZES[SIZES.length - 1])
    // A size that is none of them steps from the book's own.
    expect(stepSize(101, 1)).toBe(110)
  })
})

describe('the device', () => {
  it('is named from what the browser says, short', () => {
    expect(deviceName('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36')).toBe('Chrome · Windows')
    expect(deviceName('Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0')).toBe('Firefox · Linux')
    expect(deviceName('Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')).toBe('Safari · iPadOS')
    expect(deviceName('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1')).toBe('Chrome · iOS')
    expect(deviceName('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36')).toBe('Chrome · Android')
    expect(deviceName('Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0')).toBe('Edge · Windows')
    expect(deviceName('')).toBe('Browser')
    // An iPad in the desktop view of Safari calls itself a Mac; the touch points tell it from a Mac.
    const AS_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
    expect(deviceName(AS_MAC, 'MacIntel', 5)).toBe('Safari · iPadOS')
    expect(deviceName(AS_MAC, 'MacIntel', 0)).toBe('Safari · macOS')
    expect(deviceName(AS_MAC, 'MacIntel')).toBe('Safari · macOS')
    expect(deviceName(AS_MAC, 'MacIntel', 1)).toBe('Safari · macOS')
    expect(deviceName('x'.repeat(500)).length).toBeLessThanOrEqual(80)
  })

  it('makes a key of the shape the server takes, and never the same twice in a row', () => {
    const key = newDeviceKey()
    expect(key).toMatch(/^[A-Za-z0-9_-]{4,40}$/)
    expect(newDeviceKey()).not.toBe(key)
  })

  it('knows WebKit (Safari, and every browser on an iPhone or iPad)', () => {
    const safari = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
    const chrome = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
    expect(isWebKit(safari, 'MacIntel', 0)).toBe(true)
    expect(isWebKit(chrome, 'MacIntel', 0)).toBe(false)
    expect(isWebKit('Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0', 'Linux', 0)).toBe(false)
    expect(isWebKit(chrome.replace('Macintosh; Intel Mac OS X 10_15_7', 'iPhone; CPU iPhone OS 17_0 like Mac OS X') + ' CriOS/130', 'iPhone', 5)).toBe(true)
    // An iPad that says it is a Mac: touch gives it away.
    expect(isWebKit(chrome, 'MacIntel', 5)).toBe(true)
  })
})

describe('a place of another device', () => {
  it('counts as further along only by more than a hair', () => {
    expect(furtherAlong(0.5, 0.3)).toBe(true)
    expect(furtherAlong(0.301, 0.3)).toBe(false)
    expect(furtherAlong(0.2, 0.3)).toBe(false)
  })
})

describe('what the frame is told and may ask', () => {
  it('listens only to the page that holds it', () => {
    const page = {} as Window
    expect(fromParent({ source: page } as MessageEvent, page)).toBe(true)
    expect(fromParent({ source: {} as Window } as MessageEvent, page)).toBe(false)
    expect(fromParent({ source: null } as MessageEvent, page)).toBe(false)
    expect(fromParent({ source: page } as MessageEvent, null)).toBe(false)
  })

  it('opens only the web and mail from a book', () => {
    expect(openable('https://example.com/a')).toBe(true)
    expect(openable('http://example.com')).toBe(true)
    expect(openable('mailto:a@example.com')).toBe(true)
    for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', '/api/v1/me', 'data:text/html,x', ' https://x', 'blob:null/1', '//example.com', 'tel:123']) expect(openable(bad)).toBe(false)
  })

  it('hands pdf.js only plain file names', () => {
    for (const ok of ['78-EUC-H.bcmap', 'FoxitSans.pfb', 'openjpeg.wasm']) expect(plainName(ok)).toBe(true)
    for (const bad of ['../secret', 'a/b', '..bcmap', '', '.hidden', 'x'.repeat(200), 'a b']) expect(plainName(bad)).toBe(false)
  })
})

describe('the address of a link in a book', () => {
  it('is written as the browser reads it, and only for the web or a mail address', () => {
    expect(addressOf('HTTPS://Example.COM/a b')).toBe('https://example.com/a%20b')
    expect(addressOf('https://xn--e1afmkfd.example/')).toBe('https://xn--e1afmkfd.example/')
    expect(addressOf('mailto:jule@example.com')).toBe('mailto:jule@example.com')
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', 'blob:https://example.com/x', '/relative', 'https://', 'https://exa mple.com', 'https://' + 'a'.repeat(2100)]) {
      expect(addressOf(bad), bad).toBeNull()
    }
  })
})
