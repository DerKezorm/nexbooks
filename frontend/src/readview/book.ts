/**
 * A book made from the bytes the page handed over: an EPUB (a ZIP, read by foliate-js), or a MOBI / AZW3 (a Palm database).
 * Everything that comes out of the file goes through the limits of `guards.ts` and the cleaning of `sanitize.ts` before the
 * reader shows it. A book that is copy-protected is named as such and not opened; nothing is done to get around the protection.
 */
import { EPUB } from '../vendor/foliate-js/epub.js'
import { isMOBI, MOBI } from '../vendor/foliate-js/mobi.js'
import type { FoliateBook } from '../vendor/foliate-js/view.js'
import { unzlibSync } from '../vendor/foliate-js/vendor/fflate.js'
import { checkDirectory, checkEntries, entryLimit, evenEndRecord, LimitError, reportLimit, reportNotice, sniffImage, tooManyPixels } from './guards'
import { blankPage, cleanCss, cleanMarkup, kindOf, normalizeType, XML_PAGES } from './sanitize'
import { sha1 } from './sha1'
import { addressIn, entryFinder } from './zipnames'

/** What font scrambling is (not a protection): the algorithms of the container that only hide the font from other books. */
const FONT_SCRAMBLING = new Set(['http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC'])

async function asText(data: unknown): Promise<string> {
  if (typeof data === 'string') return data
  if (data instanceof Blob) return data.text()
  if (data instanceof ArrayBuffer) return new TextDecoder().decode(data)
  if (ArrayBuffer.isView(data)) return new TextDecoder().decode(data as Uint8Array)
  return ''
}

/** The start of a picture: enough to find its size in the header (a JPEG may keep metadata before it). */
const HEAD = 256 * 1024

async function head(data: unknown): Promise<Uint8Array> {
  if (data instanceof Blob) return new Uint8Array(await data.slice(0, HEAD).arrayBuffer())
  if (data instanceof ArrayBuffer) return new Uint8Array(data, 0, Math.min(HEAD, data.byteLength))
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, Math.min(HEAD, data.byteLength))
  return new Uint8Array()
}

/** A page cleaned, and the reader told when it had to be flattened or cut. */
function cleanPage(text: string, type: string, origin: string) {
  const cleaned = cleanMarkup(text, type, origin)
  if (cleaned.flattened) reportNotice('flattened')
  if (cleaned.cut) reportNotice('cut')
  return cleaned
}

/**
 * What the loader of foliate-js is made to do with what it finds in the book: no script is loaded, no sound or video, a
 * page and a style are cleaned, a picture that claims more pixels than a page may get is replaced by nothing. The loader
 * tells before it loads a thing (`load`) and before it gives a thing an address (`data`).
 *
 * What a thing is, is decided by its media type as a browser compares it (no parameters, lower case). A type that is a page
 * (including the XML types a browser would draw as one) is cleaned; a type that is nothing the reader knows gets the type
 * `application/octet-stream` (a browser shows no page from that, and does not guess a page from its bytes); a picture whose type
 * is missing or unknown is recognised by its first bytes.
 */
export function guardLoader(target: EventTarget, origin: string): void {
  target.addEventListener('load', (event) => {
    const detail = (event as CustomEvent<{ type?: string; isScript?: boolean; allow: boolean }>).detail
    const type = String(detail.type ?? '')
    if (detail.isScript || /^(audio|video)\//i.test(type) || /javascript|ecmascript|wasm/i.test(type)) detail.allow = false
  })
  target.addEventListener('data', (event) => {
    const detail = (event as CustomEvent<{ data: unknown; type: string }>).detail
    const type = normalizeType(detail.type)
    const kind = kindOf(type)
    const data = detail.data
    if (kind === 'page') {
      detail.data = Promise.resolve(data).then(async (value) => {
        const cleaned = cleanPage(await asText(value), type, origin)
        detail.type = cleaned.type
        return cleaned.text
      })
    } else if (kind === 'css') {
      detail.type = 'text/css'
      detail.data = Promise.resolve(data).then(async (value) => cleanCss(await asText(value)))
    } else if (kind === 'image') {
      detail.type = type
      detail.data = Promise.resolve(data).then(async (value) => (tooManyPixels(await head(value)) ? new Uint8Array() : value))
    } else if (kind === 'font') {
      detail.type = type
    } else {
      // Nothing the reader knows: not a page (a browser would draw some of these as one, and guess one from the bytes of a
      // part with no type), unless it is a picture in all but its label.
      detail.data = Promise.resolve(data).then(async (value) => {
        const first = await head(value)
        const picture = sniffImage(first)
        detail.type = picture ?? 'application/octet-stream'
        return picture && tooManyPixels(first) ? new Uint8Array() : value
      })
    }
  })
}

/** The package of an EPUB as far as the reader looks into it: the list of its parts and the order of its pages. */
type Package = { manifest: { id: string; href: string; mediaType?: string }[]; spine: { idref: string }[] }

/**
 * What the manifest of an EPUB says each part is, made plain. The manifest says it in more ways than the library compares (`Application/XHTML+XML`, `application/xhtml+xml; charset=utf-8`): the
 * types are made plain, an XML page of the spine is read as XHTML, and a page of the spine that is none of the types a page
 * can have is replaced by an empty one (a browser would draw some of them, and run their scripts).
 */
function settleManifest(book: FoliateBook, origin: string): void {
  const resources = (book as unknown as { resources?: Package }).resources
  if (!resources) return
  for (const item of resources.manifest) item.mediaType = normalizeType(item.mediaType)
  const byId = new Map(resources.manifest.map((item) => [item.id, item]))
  const refused = new Set<string>()
  for (const entry of resources.spine) {
    const item = byId.get(entry.idref)
    if (!item) continue
    if (item.mediaType && XML_PAGES.has(item.mediaType)) item.mediaType = 'application/xhtml+xml'
    else if (kindOf(item.mediaType) !== 'page') refused.add(item.href)
  }
  if (refused.size === 0) return
  for (const section of book.sections) {
    if (typeof section.id !== 'string' || !refused.has(section.id)) continue
    const empty = blankPage(origin)
    section.load = async () => URL.createObjectURL(new Blob([empty.text], { type: empty.type }))
    section.unload = () => undefined
    section.createDocument = async () => new DOMParser().parseFromString('<html><body></body></html>', 'text/html')
  }
}

// ---- EPUB ------------------------------------------------------------------------------------------------------------

async function drain(entry: ZipEntry, max: number): Promise<Uint8Array[]> {
  const parts: Uint8Array[] = []
  let total = 0
  let over = false
  const control = new AbortController()
  const sink = new WritableStream<Uint8Array>({
    write(chunk) {
      total += chunk.length
      if (total > max) {
        over = true
        control.abort()
        throw new LimitError('zip_bomb')
      }
      parts.push(chunk.slice())
    },
  })
  try {
    await entry.getData(sink, { signal: control.signal })
  } catch (error) {
    // The stream the library wrote to was shut by our own abort: what it says then is no use to anyone. The library itself
    // refuses a part that unpacks to more than its header says ("Invalid uncompressed size"): the same lie, found earlier.
    if (over || (error instanceof Error && /uncompressed size/i.test(error.message))) {
      const limit = new LimitError('zip_bomb')
      reportLimit(limit)
      throw limit
    }
    throw error
  }
  return parts
}

type ZipEntry = Awaited<ReturnType<InstanceType<typeof import('../vendor/foliate-js/vendor/zip.js').ZipReader>['getEntries']>>[number]

/** The files of an EPUB, as the reader of foliate-js wants them, within the limits. */
async function zipLoader(buffer: ArrayBuffer) {
  const zip = await import('../vendor/foliate-js/vendor/zip.js')
  zip.configure({ useWebWorkers: false })
  const reader = new zip.ZipReader(new zip.BlobReader(new Blob([buffer])))
  const entries = await reader.getEntries()
  checkEntries(entries, buffer.byteLength)
  const map = new Map(entries.map((entry) => [entry.filename, entry]))
  /** The entry an address of the package means: as written, or as a URL (see `zipnames.ts`). */
  const find = entryFinder(map.keys())
  const entryOf = (name: string) => {
    const actual = find(name)
    return actual === null ? undefined : map.get(actual)
  }
  /** An entry's bytes, never more than it said it has (and a little, for a lie that is still small). */
  const bytes = (name: string) => {
    const entry = entryOf(name)
    return entry ? drain(entry, Math.min(entryLimit(entry.filename), entry.uncompressedSize + 65536)) : null
  }
  return {
    names: new Set(map.keys()),
    find,
    loadText: async (name: string) => {
      const parts = await bytes(name)
      return parts ? new TextDecoder().decode(await new Blob(parts as BlobPart[]).arrayBuffer()) : null
    },
    loadBlob: async (name: string, type?: string) => {
      const parts = await bytes(name)
      return parts ? new Blob(parts as BlobPart[], type ? { type } : undefined) : null
    },
    getSize: (name: string) => entryOf(name)?.uncompressedSize ?? 0,
  }
}

/** Files only a protected book carries: Adobe's and Apple's rights files and the licence of Readium LCP. */
const PROTECTION_FILES = ['META-INF/rights.xml', 'META-INF/license.lcpl', 'META-INF/sinf.xml']
/** How much of a chapter says whether it is markup, and how many chapters are looked at. */
const MARKUP_SAMPLE = 2048
const MARKUP_DOCUMENTS = 4

/** Whether the start of a chapter is text with markup in it (an empty chapter is no evidence either way). */
export function isMarkup(head: Uint8Array): boolean {
  if (head.every((byte) => byte === 0 || byte === 32 || byte === 9 || byte === 10 || byte === 13)) return true
  const utf16 = (head[0] === 0xff && head[1] === 0xfe) || (head[0] === 0xfe && head[1] === 0xff)
  if (!utf16 && head.includes(0)) return false
  const text = new TextDecoder(utf16 ? (head[0] === 0xff ? 'utf-16le' : 'utf-16be') : 'utf-8').decode(head)
  let controls = 0
  let broken = 0
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0
    if (code < 32 && code !== 9 && code !== 10 && code !== 13) controls++
    if (code === 0xfffd) broken++
  }
  return text.includes('<') && controls <= text.length / 50 && broken <= text.length / 20
}

type Loader = Awaited<ReturnType<typeof zipLoader>>

/** The pages the package lists in its spine, in reading order, found by the media type it gives them and not by how their names
 * end (a book keeps its chapters in `.xml` files, or `.htm`, or none at all). Empty when the package cannot be read. */
async function declaredPages(loader: Loader): Promise<string[]> {
  try {
    const container = await loader.loadText('META-INF/container.xml')
    if (!container) return []
    const roots = Array.from(new DOMParser().parseFromString(container, 'application/xml').getElementsByTagNameNS('*', 'rootfile'))
    const path = roots.find((root) => root.getAttribute('media-type') === 'application/oebps-package+xml')?.getAttribute('full-path')
    const packageName = path ? loader.find(path) : null
    const text = packageName ? await loader.loadText(packageName) : null
    if (!packageName || !text) return []
    const doc = new DOMParser().parseFromString(text, 'application/xml')
    const folder = packageName.includes('/') ? packageName.slice(0, packageName.lastIndexOf('/')) : ''
    const items = new Map<string, Element>()
    for (const item of Array.from(doc.getElementsByTagNameNS('*', 'item'))) items.set(item.getAttribute('id') ?? '', item)
    const pages: string[] = []
    for (const ref of Array.from(doc.getElementsByTagNameNS('*', 'itemref'))) {
      const item = items.get(ref.getAttribute('idref') ?? '')
      if (!item || kindOf(item.getAttribute('media-type')) !== 'page') continue
      if ((item.getAttribute('properties') ?? '').split(/\s+/).includes('nav')) continue
      const name = loader.find(addressIn(folder, item.getAttribute('href') ?? ''))
      if (name) pages.push(name)
    }
    return pages
  } catch (error) {
    if (error instanceof LimitError) throw error
    return []
  }
}

/** Whether an EPUB is copy-protected: a rights file or a licence (a Kobo's lies in the root and is made of `kdrm`), an
 * encrypted part that is not a font, or chapters that are no markup (a protection that names itself nowhere). */
async function epubProtected(loader: Loader): Promise<boolean> {
  if (PROTECTION_FILES.some((name) => loader.names.has(name))) return true
  if (loader.names.has('rights.xml') && /kdrm/i.test((await loader.loadText('rights.xml')) ?? '')) return true
  const text = loader.names.has('META-INF/encryption.xml') ? await loader.loadText('META-INF/encryption.xml') : null
  if (text) {
    const doc = new DOMParser().parseFromString(text, 'application/xml')
    if (Array.from(doc.getElementsByTagNameNS('*', 'EncryptionMethod')).some((method) => !FONT_SCRAMBLING.has(method.getAttribute('Algorithm') ?? ''))) return true
  }
  const listed = await declaredPages(loader)
  const chapters = listed.length > 0 ? listed : Array.from(loader.names).filter((name) => /\.(xhtml|html|htm)$/i.test(name) && !/(^|\/)(nav|toc)\.x?html?$/i.test(name))
  const start = Math.floor(chapters.length / 3)
  for (const name of [...chapters.slice(start), ...chapters.slice(0, start)].slice(0, MARKUP_DOCUMENTS)) {
    const blob = await loader.loadBlob(name)
    if (blob && !isMarkup(new Uint8Array(await blob.slice(0, MARKUP_SAMPLE).arrayBuffer()))) return true
  }
  return false
}

/**
 * The sections of the spine whose file is not in the ZIP (the package names a part that is not there, or one the address of
 * which cannot be matched). Such a section shows an empty page; the book would then run to "100 %" at the last page there is
 * text on. So a missing section counts as much as an average one in the progress, the person is told that part of the book is
 * missing, and a book of which no section can be read at all is no book. Returns how many sections are missing.
 */
function settleParts(book: FoliateBook, loader: Loader): number {
  const sizes: number[] = []
  const lost: FoliateBook['sections'] = []
  for (const section of book.sections) {
    if (typeof section.id !== 'string') continue
    if (loader.find(section.id) === null) lost.push(section)
    else sizes.push(section.size ?? 0)
  }
  if (lost.length === 0) return 0
  if (sizes.length === 0) throw new LimitError('broken')
  const average = Math.max(1, Math.round(sizes.reduce((sum, size) => sum + size, 0) / sizes.length))
  for (const section of lost) section.size = average
  reportNotice('missing')
  return lost.length
}

export async function openEpub(buffer: ArrayBuffer, origin: string): Promise<FoliateBook> {
  const view = new Uint8Array(buffer)
  if (view.length < 4 || view[0] !== 0x50 || view[1] !== 0x4b) throw new LimitError('broken')
  checkDirectory(view)
  // A book is one file with one directory: when the two counts of its end record disagree, the one the directory backs is used.
  evenEndRecord(view)
  let loader: Loader
  try {
    loader = await zipLoader(buffer)
  } catch (error) {
    if (error instanceof LimitError) throw error
    throw new LimitError('broken')
  }
  if (await epubProtected(loader)) throw new LimitError('protected')
  try {
    const book = (await new EPUB({ ...loader, sha1: async (text: string) => sha1(text) }).init()) as FoliateBook
    settleManifest(book, origin)
    settleParts(book, loader)
    if (book.transformTarget) guardLoader(book.transformTarget, origin)
    return book
  } catch (error) {
    if (error instanceof LimitError) throw error
    throw new LimitError('broken')
  }
}

// ---- MOBI and AZW3 -------------------------------------------------------------------------------------------------------

/** The encryption type in the first record of a Palm database: 0 is none, anything else is Kindle's DRM. */
export function mobiEncryption(bytes: Uint8Array): number | null {
  if (bytes.length < 80) return null
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const records = view.getUint16(76)
  if (records < 1 || 78 + 8 > bytes.length) return null
  const first = view.getUint32(78)
  if (first + 14 > bytes.length) return null
  return view.getUint16(first + 12)
}

export async function openMobi(buffer: ArrayBuffer, origin: string): Promise<FoliateBook> {
  const bytes = new Uint8Array(buffer)
  const blob = new Blob([buffer])
  if (!(await isMOBI(blob))) throw new LimitError('broken')
  const encryption = mobiEncryption(bytes)
  if (encryption === null) throw new LimitError('broken')
  if (encryption !== 0) throw new LimitError('protected')
  try {
    const book = await new MOBI({ unzlib: unzlibSync }).open(blob)
    book.clean = async (text: string, type: string) => cleanPage(text, type, origin)
    book.guard = (data: Uint8Array) => (tooManyPixels(data) ? new Uint8Array() : data)
    if (book.transformTarget) guardLoader(book.transformTarget, origin)
    return book
  } catch (error) {
    if (error instanceof LimitError) throw error
    throw new LimitError('broken')
  }
}
