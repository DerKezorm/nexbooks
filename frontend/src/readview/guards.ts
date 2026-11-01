/**
 * The limits of reading a book in the browser. A book is a ZIP made by somebody else; the server keeps limits when it reads
 * one (`backend/app/services/readers/limits.py`), and the browser, which unpacks it again, keeps the same kind: how many
 * entries, how large the directory, how much all of it may claim, how far a packed entry may swell, and no more bytes from
 * an entry than it said it has. A broken limit is `LimitError` with the reason a person is told.
 */
import type { FailReason } from '../lib/readFrame'

export class LimitError extends Error {
  readonly reason: FailReason

  constructor(reason: FailReason) {
    super(reason)
    this.reason = reason
  }
}

/** Who is told when a limit is broken while the book is being read, not when it is opened: the library that turns the pages
 * swallows a part that cannot be loaded (it shows an empty page), so the reader must hear of it by itself. */
let reporter: (error: LimitError) => void = () => undefined
export function onLimit(report: (error: LimitError) => void): void {
  reporter = report
}
export function reportLimit(error: unknown): void {
  if (error instanceof LimitError) reporter(error)
}

/** What the reader tells the person about a page it had to change: nested too deeply to be drawn (flattened), or too big (cut), or a part that the book names and does not hold (missing). */
export type Notice = 'flattened' | 'cut' | 'missing'
let noticer: (notice: Notice) => void = () => undefined
export function onNotice(tell: (notice: Notice) => void): void {
  noticer = tell
}
export function reportNotice(notice: Notice): void {
  noticer(notice)
}

export const MAX_ENTRIES = 20_000
/** The central directory of the ZIP: a book's is a few hundred kilobytes. */
export const MAX_DIRECTORY = 2 * 1024 * 1024
/** An entry of this size that unpacks to more than `MAX_RATIO` times its packed size is a bomb, not a book. */
export const BOMB_FREE = 8 * 1024 * 1024
export const MAX_RATIO = 300
/** All entries together may not claim more than this once unpacked, and more than `TOTAL_FREE` only at a ratio a book could have. */
export const MAX_TOTAL = 1024 * 1024 * 1024
export const TOTAL_FREE = 64 * 1024 * 1024
/** One entry: text is small, pictures may be large. */
export const MAX_TEXT_ENTRY = 24 * 1024 * 1024
export const MAX_ENTRY = 128 * 1024 * 1024
/** Pixels of a picture before the page gets it: a PNG of one bit can be tiny on the disk and enormous in memory. */
export const MAX_PIXELS = 36_000_000

const TEXT_NAME = /\.(x?html?|xml|opf|ncx|css|svg|txt|smil|json)$/i

export function entryLimit(name: string): number {
  return TEXT_NAME.test(name) ? MAX_TEXT_ENTRY : MAX_ENTRY
}

/** Where the end record of the ZIP starts: the last signature in the last 64 KiB and a little, the way unzip tools find it. */
function endRecord(bytes: Uint8Array): number | null {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const lowest = Math.max(0, bytes.length - (65535 + 22))
  for (let at = bytes.length - 22; at >= lowest; at--) if (view.getUint32(at, true) === 0x06054b50) return at
  return null
}

/**
 * Makes the two counts of an end record agree. A ZIP has one count of the entries on this disk and one of all entries; a book is
 * one file on one disk, and some programs write the first twice as high as the second (a file the usual tools open without a
 * word). The library that unpacks the book reads the first one and runs past the end of the directory, so the book "cannot be
 * opened". The count of all entries is the one the directory itself backs (it is also the one `directoryClaim` goes by). The
 * bytes are changed in place, only for a single-disk ZIP and only the count on this disk; true when something was changed.
 */
export function evenEndRecord(bytes: Uint8Array): boolean {
  const at = endRecord(bytes)
  if (at === null) return false
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const total = view.getUint16(at + 10, true)
  const here = view.getUint16(at + 8, true)
  if (view.getUint16(at + 4, true) !== 0 || view.getUint16(at + 6, true) !== 0) return false
  if (here === total || here === 0xffff || total === 0xffff) return false
  view.setUint16(at + 8, total, true)
  return true
}

/**
 * What the end of the ZIP says about its central directory: how many entries and how many bytes, read before anything is
 * unpacked. The end record is found the way unzip tools find it (the last signature in the last 64 KiB and a little), the
 * ZIP64 record behind its locator when the plain fields are full. Null when no end record is found.
 */
export function directoryClaim(bytes: Uint8Array): { entries: number; bytes: number } | null {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const at = endRecord(bytes)
  if (at !== null) {
    let entries = view.getUint16(at + 10, true)
    let size = view.getUint32(at + 12, true)
    const offset = view.getUint32(at + 16, true)
    if (entries === 0xffff || size === 0xffffffff || offset === 0xffffffff) {
      const locator = at - 20
      if (locator >= 0 && view.getUint32(locator, true) === 0x07064b50) {
        const record = Number(view.getBigUint64(locator + 8, true))
        if (record >= 0 && record + 56 <= bytes.length && view.getUint32(record, true) === 0x06064b50) {
          entries = Number(view.getBigUint64(record + 32, true))
          size = Number(view.getBigUint64(record + 40, true))
        }
      }
    }
    return { entries, bytes: size }
  }
  return null
}

/** Throws `LimitError` when the end of the ZIP already claims more than a book has. */
export function checkDirectory(bytes: Uint8Array): void {
  const claim = directoryClaim(bytes)
  if (claim === null) return
  if (claim.entries > MAX_ENTRIES) throw new LimitError('too_many_entries')
  if (claim.bytes > MAX_DIRECTORY) throw new LimitError('too_large')
}

type Entry = { filename: string; uncompressedSize: number; compressedSize: number; directory?: boolean }

/** Throws `LimitError` when the entries together or one of them claim more than a book may. `packed`: the size of the file. */
export function checkEntries(entries: Entry[], packed: number): void {
  if (entries.length > MAX_ENTRIES) throw new LimitError('too_many_entries')
  let declared = 0
  for (const entry of entries) {
    if (entry.directory) continue
    declared += entry.uncompressedSize
    if (entry.uncompressedSize > entryLimit(entry.filename)) throw new LimitError('too_large')
    if (entry.uncompressedSize > BOMB_FREE && entry.uncompressedSize > entry.compressedSize * MAX_RATIO) throw new LimitError('zip_bomb')
  }
  if (declared > MAX_TOTAL || (declared > TOTAL_FREE && declared > packed * MAX_RATIO)) throw new LimitError('zip_bomb')
}

/** The size in pixels a picture says it has in its first bytes (PNG, GIF, JPEG, WebP), or null when it is none of them or
 * too short to say. Only the header is read: a picture is not decoded to find out how big it is. */
export function pictureSize(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.length < 24) return null
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  // PNG: signature, then the IHDR chunk with width and height.
  if (view.getUint32(0) === 0x89504e47 && view.getUint32(4) === 0x0d0a1a0a) {
    return { width: view.getUint32(16), height: view.getUint32(20) }
  }
  // GIF: "GIF8", then width and height as 16 bits, little endian.
  if (view.getUint32(0) === 0x47494638) return { width: view.getUint16(6, true), height: view.getUint16(8, true) }
  // WebP: RIFF, WEBP, then one of three chunk kinds.
  if (view.getUint32(0) === 0x52494646 && view.getUint32(8) === 0x57454250) {
    const kind = view.getUint32(12)
    if (kind === 0x56503858 && bytes.length >= 30) {
      // VP8X: 24 bits each, minus one.
      const width = 1 + (bytes[24] | (bytes[25] << 8) | (bytes[26] << 16))
      const height = 1 + (bytes[27] | (bytes[28] << 8) | (bytes[29] << 16))
      return { width, height }
    }
    if (kind === 0x56503820 && bytes.length >= 30) return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff }
    if (kind === 0x5650384c && bytes.length >= 25) {
      const bits = view.getUint32(21, true)
      return { width: (bits & 0x3fff) + 1, height: ((bits >> 14) & 0x3fff) + 1 }
    }
    return null
  }
  // JPEG: walk the segments to the first "start of frame".
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let at = 2
    while (at + 9 < bytes.length) {
      if (bytes[at] !== 0xff) {
        at++
        continue
      }
      const marker = bytes[at + 1]
      if (marker === 0xff) {
        at++
        continue
      }
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
        at += 2
        continue
      }
      const length = view.getUint16(at + 2)
      if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
        return { height: view.getUint16(at + 5), width: view.getUint16(at + 7) }
      }
      if (length < 2) return null
      at += 2 + length
    }
  }
  return null
}

/** The media type of a picture by its first bytes (PNG, GIF, JPEG, WebP), or null. For a part whose manifest names no type that
 * a browser would take for a picture. */
export function sniffImage(bytes: Uint8Array): string | null {
  if (bytes.length < 12) return null
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'image/png'
  if (bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x38) return 'image/gif'
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'image/jpeg'
  if (bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50) return 'image/webp'
  return null
}

/** Whether a picture's header claims more pixels than the page may be given. */
export function tooManyPixels(bytes: Uint8Array): boolean {
  const size = pictureSize(bytes)
  return size !== null && size.width * size.height > MAX_PIXELS
}
