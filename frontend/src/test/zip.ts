/**
 * A ZIP made in a test: files stored as they are, no compression, and the option to lie in the headers about a size. Enough
 * for an EPUB the reader can open, and for one that says it is smaller than it is.
 */
const encoder = new TextEncoder()

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of data) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1
  }
  return (crc ^ 0xffffffff) >>> 0
}

export type Part = {
  name: string
  data: string | Uint8Array
  /** What the headers say the size is, when it is not true. */
  claims?: number
}

export function zip(parts: Part[]): ArrayBuffer {
  const chunks: Uint8Array[] = []
  const central: Uint8Array[] = []
  let offset = 0
  for (const part of parts) {
    const name = encoder.encode(part.name)
    const data = typeof part.data === 'string' ? encoder.encode(part.data) : part.data
    const size = part.claims ?? data.length
    const local = new Uint8Array(30 + name.length)
    const view = new DataView(local.buffer)
    view.setUint32(0, 0x04034b50, true)
    view.setUint16(4, 20, true)
    view.setUint32(14, crc32(data), true)
    view.setUint32(18, data.length, true)
    view.setUint32(22, size, true)
    view.setUint16(26, name.length, true)
    local.set(name, 30)
    chunks.push(local, data)
    const entry = new Uint8Array(46 + name.length)
    const e = new DataView(entry.buffer)
    e.setUint32(0, 0x02014b50, true)
    e.setUint16(4, 20, true)
    e.setUint16(6, 20, true)
    e.setUint32(16, crc32(data), true)
    e.setUint32(20, data.length, true)
    e.setUint32(24, size, true)
    e.setUint16(28, name.length, true)
    e.setUint32(42, offset, true)
    entry.set(name, 46)
    central.push(entry)
    offset += local.length + data.length
  }
  const directory = central.reduce((sum, entry) => sum + entry.length, 0)
  const end = new Uint8Array(22)
  const v = new DataView(end.buffer)
  v.setUint32(0, 0x06054b50, true)
  v.setUint16(8, parts.length, true)
  v.setUint16(10, parts.length, true)
  v.setUint32(12, directory, true)
  v.setUint32(16, offset, true)
  const out = new Uint8Array(offset + directory + 22)
  let at = 0
  for (const piece of [...chunks, ...central, end]) {
    out.set(piece, at)
    at += piece.length
  }
  return out.buffer
}

const CONTAINER =
  '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'

/** A small EPUB 3 with the chapters it is given as [title, body html]; `more` adds or replaces files, `types` gives a chapter
 * another media type in the manifest than the right one. */
export function epub(chapters: [string, string][] = [['One', '<p>First words.</p>']], more: Part[] = [], types: Record<number, string> = {}): ArrayBuffer {
  const manifest =
    chapters.map((_c, n) => `<item id="c${n}" href="c${n}.xhtml" media-type="${types[n] ?? 'application/xhtml+xml'}"/>`).join('') +
    '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
  const spine = chapters.map((_c, n) => `<itemref idref="c${n}"/>`).join('')
  const opf = `<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" unique-identifier="id" version="3.0"><metadata><dc:identifier id="id">urn:test:1</dc:identifier><dc:title>Test Book</dc:title><dc:language>en</dc:language></metadata><manifest>${manifest}</manifest><spine>${spine}</spine></package>`
  const nav = `<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>n</title></head><body><nav epub:type="toc"><ol>${chapters.map(([title], n) => `<li><a href="c${n}.xhtml">${title}</a></li>`).join('')}</ol></nav></body></html>`
  const files: Part[] = [
    { name: 'mimetype', data: 'application/epub+zip' },
    { name: 'META-INF/container.xml', data: CONTAINER },
    { name: 'OEBPS/content.opf', data: opf },
    { name: 'OEBPS/nav.xhtml', data: nav },
    ...chapters.map(([title, body], n) => ({
      name: `OEBPS/c${n}.xhtml`,
      data: `<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>${title}</title></head><body><h1>${title}</h1>${body}</body></html>`,
    })),
  ]
  for (const extra of more) {
    const at = files.findIndex((file) => file.name === extra.name)
    if (at >= 0) files[at] = extra
    else files.push(extra)
  }
  return zip(files)
}

export type PageSpec = {
  /** How the package writes the address of the page (a URL path, as it stands in the manifest). */
  href: string
  /** The name of the entry in the ZIP, relative to the folder of the package; null when the book names the page and does not hold it. */
  entry: string | null
  text: string
  /** The media type in the manifest (the right one when not given). */
  type?: string
  /** The bytes of the entry, when they are not a page. */
  raw?: Uint8Array
}

/** An EPUB 2 whose package, pages and addresses are written as a test asks: the package where it says (`opfPath`), the pages
 * with the address the manifest gives and the name the ZIP holds, an empty `dc:creator` the way some programs write it. */
export function epubWithPages(pages: PageSpec[], opfPath = 'OEBPS/content.opf'): ArrayBuffer {
  const folder = opfPath.includes('/') ? opfPath.slice(0, opfPath.lastIndexOf('/') + 1) : ''
  const manifest = pages.map((page, n) => `<item id="p${n}" href="${page.href}" media-type="${page.type ?? 'application/xhtml+xml'}"/>`).join('')
  const spine = pages.map((_page, n) => `<itemref idref="p${n}"/>`).join('')
  const opf = `<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf" unique-identifier="id" version="2.0"><metadata><dc:identifier id="id">urn:test:2</dc:identifier><dc:title>Test Book</dc:title><dc:creator opf:role="aut">Rune Falkner</dc:creator><dc:creator opf:role="bkp"></dc:creator><dc:language>en</dc:language></metadata><manifest>${manifest}</manifest><spine>${spine}</spine></package>`
  const container = `<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="${opfPath}" media-type="application/oebps-package+xml"/></rootfiles></container>`
  const files: Part[] = [
    { name: 'mimetype', data: 'application/epub+zip' },
    { name: 'META-INF/container.xml', data: container },
    { name: opfPath, data: opf },
  ]
  for (const page of pages) {
    if (page.entry === null) continue
    files.push({ name: folder + page.entry, data: page.raw ?? `<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body><p>${page.text}</p></body></html>` })
  }
  return zip(files)
}
