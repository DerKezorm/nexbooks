import {
  BOMB_FREE, checkDirectory, checkEntries, directoryClaim, entryLimit, evenEndRecord, LimitError, MAX_ENTRIES, MAX_DIRECTORY, MAX_PIXELS, MAX_TEXT_ENTRY, pictureSize, tooManyPixels,
} from './guards'

/** The end of a ZIP that claims `entries` entries in a directory of `size` bytes (and nothing of what it claims). */
function zipEnd(entries: number, size: number, prefix = 100): Uint8Array {
  const out = new Uint8Array(prefix + 22)
  const view = new DataView(out.buffer)
  view.setUint32(prefix, 0x06054b50, true)
  view.setUint16(prefix + 8, Math.min(entries, 0xffff), true)
  view.setUint16(prefix + 10, Math.min(entries, 0xffff), true)
  view.setUint32(prefix + 12, size, true)
  view.setUint32(prefix + 16, 0, true)
  return out
}

describe('what the end of a ZIP claims', () => {
  it('reads the number of entries and the size of the directory', () => {
    expect(directoryClaim(zipEnd(12, 800))).toEqual({ entries: 12, bytes: 800 })
  })

  it('finds the end record behind a comment, and none where there is none', () => {
    const withComment = new Uint8Array([...zipEnd(3, 200), ...new Uint8Array(500)])
    expect(directoryClaim(withComment)).toEqual({ entries: 3, bytes: 200 })
    expect(directoryClaim(new Uint8Array(1000))).toBeNull()
    expect(directoryClaim(new Uint8Array(5))).toBeNull()
  })

  it('reads the ZIP64 record when the plain fields are full', () => {
    const bytes = new Uint8Array(200 + 56 + 20 + 22)
    const view = new DataView(bytes.buffer)
    view.setUint32(200, 0x06064b50, true)
    view.setBigUint64(200 + 32, 3_000_000n, true)
    view.setBigUint64(200 + 40, 90_000_000n, true)
    view.setUint32(256, 0x07064b50, true)
    view.setBigUint64(256 + 8, 200n, true)
    view.setUint32(276, 0x06054b50, true)
    view.setUint16(276 + 10, 0xffff, true)
    view.setUint32(276 + 12, 0xffffffff, true)
    view.setUint32(276 + 16, 0xffffffff, true)
    expect(directoryClaim(bytes)).toEqual({ entries: 3_000_000, bytes: 90_000_000 })
  })
})

describe('limits on a ZIP before anything is unpacked', () => {
  const reason = (action: () => void): string | null => {
    try {
      action()
    } catch (error) {
      return error instanceof LimitError ? error.reason : 'other'
    }
    return null
  }

  it('lets a book through', () => {
    expect(reason(() => checkDirectory(zipEnd(300, 40_000)))).toBeNull()
    expect(reason(() => checkDirectory(new Uint8Array(10)))).toBeNull()
  })

  it('refuses a directory with more entries than a book has, or larger than a book is', () => {
    expect(reason(() => checkDirectory(zipEnd(MAX_ENTRIES + 1, 1000)))).toBe('too_many_entries')
    expect(reason(() => checkDirectory(zipEnd(100, MAX_DIRECTORY + 1)))).toBe('too_large')
  })

  const entry = (filename: string, uncompressedSize: number, compressedSize: number) => ({ filename, uncompressedSize, compressedSize })

  it('refuses entries that together claim more than a book may', () => {
    expect(reason(() => checkEntries([entry('a.xhtml', 5000, 1000), entry('b.png', 90_000, 90_000)], 100_000))).toBeNull()
    expect(reason(() => checkEntries(Array.from({ length: MAX_ENTRIES + 1 }, (_, n) => entry(`${n}.txt`, 1, 1)), 100_000))).toBe('too_many_entries')
    expect(reason(() => checkEntries(Array.from({ length: 20 }, (_, n) => entry(`${n}.png`, 100 * 1024 * 1024, 90 * 1024 * 1024)), 3e9))).toBe('zip_bomb')
  })

  it('refuses a small file that claims to unpack to a great deal, but not text that packs well', () => {
    expect(reason(() => checkEntries([entry('a.xhtml', 3_000_000, 300_000)], 400_000))).toBeNull()
    expect(reason(() => checkEntries([entry('a.xhtml', 20_000_000, 40_000)], 100_000))).toBe('zip_bomb')
    // A tiny entry of zeros has a huge ratio and harms nobody.
    expect(reason(() => checkEntries([entry('a.xhtml', BOMB_FREE - 1, 30)], 5000))).toBeNull()
  })

  it('refuses one entry larger than its kind may be', () => {
    expect(reason(() => checkEntries([entry('chapter.xhtml', MAX_TEXT_ENTRY + 1, MAX_TEXT_ENTRY)], 1e9))).toBe('too_large')
    expect(entryLimit('a.xhtml')).toBe(MAX_TEXT_ENTRY)
    expect(entryLimit('a.png')).toBeGreaterThan(MAX_TEXT_ENTRY)
  })

  it('leaves folders out of the count', () => {
    expect(reason(() => checkEntries([{ ...entry('dir/', 9e9, 0), directory: true }], 1000))).toBeNull()
  })
})

describe('the size of a picture, read from its first bytes', () => {
  const be = (n: number) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
  const le16 = (n: number) => [n & 255, (n >> 8) & 255]

  it('reads a PNG', () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, ...be(640), ...be(480), 8, 2, 0, 0, 0])
    expect(pictureSize(png)).toEqual({ width: 640, height: 480 })
  })

  it('reads a GIF', () => {
    const gif = new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, ...le16(320), ...le16(200), ...new Array(20).fill(0)])
    expect(pictureSize(gif)).toEqual({ width: 320, height: 200 })
  })

  it('reads a JPEG behind other segments, and not a segment that is none of the frames', () => {
    const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe1, 0x00, 0x10, ...new Array(14).fill(1), 0xff, 0xc4, 0x00, 0x05, 1, 2, 3, 0xff, 0xc0, 0x00, 0x11, 8, 0x04, 0x00, 0x06, 0x00, 3, ...new Array(10).fill(0)])
    expect(pictureSize(jpeg)).toEqual({ width: 1536, height: 1024 })
  })

  it('reads the three kinds of WebP', () => {
    const riff = [0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]
    const x = new Uint8Array([...riff, 0x56, 0x50, 0x38, 0x58, 10, 0, 0, 0, 0, 0, 0, 0, 0x7f, 0x02, 0x00, 0xdf, 0x01, 0x00, 0, 0, 0, 0])
    expect(pictureSize(x)).toEqual({ width: 640, height: 480 })
    const lossy = new Uint8Array([...riff, 0x56, 0x50, 0x38, 0x20, 0, 0, 0, 0, 0, 0, 0, 0x9d, 0x01, 0x2a, ...le16(300), ...le16(100), 0, 0])
    expect(pictureSize(lossy)).toEqual({ width: 300, height: 100 })
    const bits = 0x2f | ((199 | (99 << 14)) << 8)
    void bits
    const lossless = new Uint8Array([...riff, 0x56, 0x50, 0x38, 0x4c, 0, 0, 0, 0, 0x2f, 199, 0x80 | 0, 0x0c, 0x00, 0, 0, 0, 0, 0, 0, 0, 0])
    expect(pictureSize(lossless)).not.toBeNull()
  })

  it('knows nothing of what is no picture, or too short', () => {
    expect(pictureSize(new Uint8Array(100))).toBeNull()
    expect(pictureSize(new Uint8Array([1, 2, 3]))).toBeNull()
    expect(pictureSize(new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"></svg>'))).toBeNull()
  })

  it('says a picture has too many pixels by its header, without decoding it', () => {
    const png = (w: number, h: number) => new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, ...be(w), ...be(h), 8, 2, 0, 0, 0])
    expect(tooManyPixels(png(4000, 3000))).toBe(false)
    expect(tooManyPixels(png(60_000, 60_000))).toBe(true)
    expect(tooManyPixels(png(MAX_PIXELS, 1))).toBe(false)
    expect(tooManyPixels(png(MAX_PIXELS, 2))).toBe(true)
    expect(tooManyPixels(new Uint8Array(10))).toBe(false)
  })
})

describe('an end record whose two counts disagree', () => {
  /** An end record that counts `here` entries on this disk and `total` in all. */
  function counted(here: number, total: number, disks = [0, 0]): Uint8Array {
    const bytes = zipEnd(total, 300)
    const view = new DataView(bytes.buffer)
    view.setUint16(100 + 4, disks[0], true)
    view.setUint16(100 + 6, disks[1], true)
    view.setUint16(100 + 8, here, true)
    return bytes
  }
  const read = (bytes: Uint8Array) => new DataView(bytes.buffer).getUint16(108, true)

  it('goes by the count of all entries, for a ZIP of one disk', () => {
    const bytes = counted(34, 17)
    expect(evenEndRecord(bytes)).toBe(true)
    expect(read(bytes)).toBe(17)
    expect(directoryClaim(bytes)).toEqual({ entries: 17, bytes: 300 })
  })

  it('changes nothing where the counts agree, the ZIP spans disks, or the counts are those of ZIP64', () => {
    for (const bytes of [counted(17, 17), counted(34, 17, [1, 1]), counted(34, 17, [0, 1]), counted(0xffff, 17), counted(17, 0xffff), new Uint8Array(500)]) {
      const before = Array.from(bytes)
      expect(evenEndRecord(bytes)).toBe(false)
      expect(Array.from(bytes)).toEqual(before)
    }
  })
})
