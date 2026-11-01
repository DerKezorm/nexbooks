import { epub, epubWithPages, zip } from '../test/zip'
import { guardLoader, mobiEncryption, openEpub } from './book'
import { LimitError, onLimit, onNotice, type Notice } from './guards'
import { sha1 } from './sha1'

const ORIGIN = 'http://127.0.0.1:8580'

async function failure(task: Promise<unknown>): Promise<string | null> {
  try {
    await task
  } catch (error) {
    return error instanceof LimitError ? error.reason : `other: ${String(error)}`
  }
  return null
}

describe('an EPUB made from bytes', () => {
  it('opens, with its title, its sections and its contents', async () => {
    const book = await openEpub(epub([['One', '<p>First words.</p>'], ['Two', '<p>More.</p>']]), ORIGIN)
    expect(book.metadata?.title).toBe('Test Book')
    expect(book.sections).toHaveLength(2)
    expect(book.toc?.map((item) => item.label)).toEqual(['One', 'Two'])
  })

  it('refuses what is no ZIP, with the word for a broken book', async () => {
    expect(await failure(openEpub(new TextEncoder().encode('this is no book at all').buffer as ArrayBuffer, ORIGIN))).toBe('broken')
    expect(await failure(openEpub(new ArrayBuffer(2), ORIGIN))).toBe('broken')
    expect(await failure(openEpub(zip([{ name: 'a.txt', data: 'x' }]), ORIGIN))).toBe('broken')
  })

  it('names a copy-protected book and does not open it', async () => {
    const encryption =
      '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/><enc:CipherData><enc:CipherReference URI="OEBPS/c0.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>'
    expect(await failure(openEpub(epub([['One', '<p>x</p>']], [{ name: 'META-INF/encryption.xml', data: encryption }]), ORIGIN))).toBe('protected')
    expect(await failure(openEpub(epub([['One', '<p>x</p>']], [{ name: 'META-INF/rights.xml', data: '<rights/>' }]), ORIGIN))).toBe('protected')
  })

  it('names a protection that no encryption file names: a rights file in the root, a licence, chapters that are no markup', async () => {
    const kobo = { name: 'rights.xml', data: "<?xml version='1.0'?><kdrm><sign>AAAA</sign></kdrm>" }
    expect(await failure(openEpub(epub([['One', '<p>x</p>']], [kobo]), ORIGIN))).toBe('protected')
    expect(await failure(openEpub(epub([['One', '<p>x</p>']], [{ name: 'META-INF/license.lcpl', data: '{}' }]), ORIGIN))).toBe('protected')
    expect(await failure(openEpub(epub([['One', '<p>x</p>']], [{ name: 'META-INF/sinf.xml', data: '<sinf/>' }]), ORIGIN))).toBe('protected')
    // A rights file that is no Kobo's is none: a note of a publisher does not lock a book.
    const note = { name: 'rights.xml', data: '<note>a licence note</note>' }
    expect((await openEpub(epub([['One', '<p>x</p>']], [note]), ORIGIN)).sections).toHaveLength(1)
    // The chapters are noise: nothing in the archive says why, and the page would be noise.
    const noise = new Uint8Array(2000).map((_value, at) => (at * 37 + 11) % 256)
    const scrambled = epub([['One', '<p>x</p>'], ['Two', '<p>y</p>'], ['Three', '<p>z</p>']], [{ name: 'OEBPS/c1.xhtml', data: noise }])
    expect(await failure(openEpub(scrambled, ORIGIN))).toBe('protected')
  })

  it('does not call the scrambling of a font a protection', async () => {
    const encryption =
      '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><enc:CipherData><enc:CipherReference URI="OEBPS/f.otf"/></enc:CipherData></enc:EncryptedData></encryption>'
    const book = await openEpub(epub([['One', '<p>x</p>']], [{ name: 'META-INF/encryption.xml', data: encryption }, { name: 'OEBPS/f.otf', data: 'font' }]), ORIGIN)
    expect(book.sections).toHaveLength(1)
  })

  it('stops a book whose directory claims too many entries', async () => {
    const bytes = new Uint8Array(epub())
    const view = new DataView(bytes.buffer)
    const end = bytes.length - 22
    view.setUint16(end + 10, 40_000, true)
    expect(await failure(openEpub(bytes.buffer as ArrayBuffer, ORIGIN))).toBe('too_many_entries')
  })

  it('stops a book that says a part is small and holds a great deal, as it unpacks, and tells the reader by itself', async () => {
    const told: string[] = []
    onLimit((error) => told.push(error.reason))
    const lying = epub([['One', '<p>x</p>']], [{ name: 'META-INF/container.xml', data: 'x'.repeat(300_000), claims: 40 }])
    expect(await failure(openEpub(lying, ORIGIN))).toBe('zip_bomb')
    expect(told).toEqual(['zip_bomb'])
    onLimit(() => undefined)
  })

  it('stops a book whose parts claim to unpack to far more than it weighs', async () => {
    const huge = zip([{ name: 'a.xhtml', data: 'x', claims: 20_000_000 }, { name: 'b.xhtml', data: 'y' }])
    expect(await failure(openEpub(huge, ORIGIN))).toBe('zip_bomb')
  })
})

describe('what the loader of the book is made to do', () => {
  function target() {
    const events = new EventTarget()
    guardLoader(events, ORIGIN)
    const load = (detail: { type: string; isScript?: boolean }) => {
      const copy = { ...detail, allow: true }
      events.dispatchEvent(new CustomEvent('load', { detail: copy }))
      return copy.allow
    }
    const data = async (detail: { data: unknown; type: string }) => {
      const copy = { ...detail } as { data: unknown; type: string }
      events.dispatchEvent(new CustomEvent('data', { detail: copy }))
      return { data: await copy.data, type: copy.type }
    }
    return { load, data }
  }

  it('loads no script and no sound or video', () => {
    const { load } = target()
    expect(load({ type: 'text/javascript', isScript: true })).toBe(false)
    expect(load({ type: 'application/javascript' })).toBe(false)
    expect(load({ type: 'application/wasm' })).toBe(false)
    expect(load({ type: 'audio/mpeg' })).toBe(false)
    expect(load({ type: 'video/mp4' })).toBe(false)
    expect(load({ type: 'application/xhtml+xml' })).toBe(true)
    expect(load({ type: 'image/png' })).toBe(true)
    expect(load({ type: 'text/css' })).toBe(true)
  })

  it('cleans a page and a style before they get an address', async () => {
    const { data } = target()
    const page = await data({ data: '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p onclick="x()">t</p><script>1</script></body></html>', type: 'application/xhtml+xml' })
    expect(String(page.data)).not.toMatch(/<script|onclick/)
    expect(String(page.data)).toContain('Content-Security-Policy')
    const style = await data({ data: 'p{background:url(http://example.com/x)}', type: 'text/css' })
    expect(String(style.data)).not.toContain('example.com')
  })

  it('cleans every type a browser would draw as a page, whatever case or parameters its type has', async () => {
    const { data } = target()
    const dirty = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p onclick="x()">t</p><script>1</script></body></html>'
    for (const type of ['application/xhtml+xml; charset=utf-8', 'APPLICATION/XHTML+XML', 'application/xml', 'text/xml', 'TEXT/XML;charset=utf-8', 'text/html', 'Text/HTML; charset=utf-8', 'image/svg+xml; charset=utf-8']) {
      const page = await data({ data: dirty, type })
      expect(String(page.data), type).not.toMatch(/<script|onclick/)
      expect(page.type, type).toMatch(/^(application\/xhtml\+xml|text\/html|image\/svg\+xml)$/)
    }
  })

  it('gives a part that is nothing the reader knows a type no browser shows as a page, and never lets its bytes pass as one', async () => {
    const { data } = target()
    const page = '<html><body><script>alert(1)</script></body></html>'
    for (const type of ['text/plain', 'application/octet-stream', 'application/x-dtbook+xml', 'application/mathml+xml', 'application/rss+xml', 'application/xslt+xml', 'application/pdf', '']) {
      expect((await data({ data: new Blob([page]), type })).type, JSON.stringify(type)).toBe('application/octet-stream')
    }
  })

  it('knows a picture by its first bytes when its type is missing, and keeps a typeface and a style as they are', async () => {
    const { data } = target()
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, 0, 0, 0, 8, 0, 0, 0, 8, 8, 6, 0, 0, 0])
    expect((await data({ data: Promise.resolve(png), type: '' })).type).toBe('image/png')
    expect((await data({ data: Promise.resolve(png), type: 'text/plain' })).type).toBe('image/png')
    expect((await data({ data: new Blob(['font']), type: 'font/woff2' })).type).toBe('font/woff2')
    expect((await data({ data: new Blob(['font']), type: 'application/vnd.ms-opentype' })).type).toBe('application/vnd.ms-opentype')
    expect((await data({ data: 'p{color:red}', type: 'text/css; charset=utf-8' })).type).toBe('text/css')
  })

  it('replaces a picture that says it has billions of pixels by nothing, and leaves the others', async () => {
    const { data } = target()
    const png = (w: number, h: number) => {
      const bytes = new Uint8Array(40)
      bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52])
      const view = new DataView(bytes.buffer)
      view.setUint32(16, w)
      view.setUint32(20, h)
      return new Blob([bytes], { type: 'image/png' })
    }
    const small = png(100, 100)
    expect((await data({ data: Promise.resolve(small), type: 'image/png' })).data).toBe(small)
    const big = (await data({ data: Promise.resolve(png(60_000, 60_000)), type: 'image/png' })).data as Uint8Array
    expect(big.length).toBe(0)
  })
})

describe('a page of the spine that is no page', () => {
  async function sections(types: Record<number, string>) {
    const book = await openEpub(epub([['One', '<p>First words.</p>'], ['Two', '<p>More.</p>']], [], types), ORIGIN)
    const urls: (string | null)[] = []
    for (const section of book.sections) urls.push(await section.load())
    const texts: string[] = []
    for (const url of urls) texts.push(url ? await (await fetch(url)).text() : '')
    return { book, urls, texts }
  }

  it('reads the types of the manifest the way a browser reads them', async () => {
    for (const type of ['application/xhtml+xml; charset=utf-8', 'APPLICATION/XHTML+XML', 'application/xml', 'text/xml']) {
      const { book } = await sections({ 0: type })
      expect(book.sections, type).toHaveLength(2)
      expect((book as unknown as { resources: { manifest: { id: string; mediaType: string }[] } }).resources.manifest.find((item) => item.id === 'c0')?.mediaType, type).toMatch(/^application\/xhtml\+xml$/)
    }
  })

  it('shows an empty page for a spine item of a type that is none of a page, instead of the file', async () => {
    for (const type of ['text/plain', 'application/octet-stream', 'application/x-dtbook+xml', 'application/pdf', 'image/png']) {
      const made: Blob[] = []
      const spy = vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => (made.push(blob as Blob), `blob:null/${made.length}`))
      try {
        const book = await openEpub(epub([['One', '<p>SECRET words.</p>'], ['Two', '<p>More.</p>']], [], { 0: type }), ORIGIN)
        expect(await book.sections[0].load(), type).toBe('blob:null/1')
        const page = await made[0].text()
        expect(page, type).not.toContain('SECRET')
        expect(page, type).toContain('Content-Security-Policy')
        expect(made[0].type, type).toBe('application/xhtml+xml')
        expect(await book.sections[0].createDocument?.()).toBeTruthy()
        // The other page of the book is still its own.
        expect(await book.sections[1].load()).toBeTruthy()
      } finally {
        spy.mockRestore()
      }
    }
  })
})

describe('a MOBI file', () => {
  const header = (encryption: number) => {
    const bytes = new Uint8Array(78 + 8 + 2 + 40)
    const view = new DataView(bytes.buffer)
    view.setUint16(76, 1)
    view.setUint32(78, 88)
    view.setUint16(88 + 12, encryption)
    return bytes
  }

  it('says whether it is encrypted (a Kindle DRM)', () => {
    expect(mobiEncryption(header(0))).toBe(0)
    expect(mobiEncryption(header(1))).toBe(1)
    expect(mobiEncryption(header(2))).toBe(2)
  })

  it('knows nothing of what is too short or points out of itself', () => {
    expect(mobiEncryption(new Uint8Array(10))).toBeNull()
    const bad = header(0)
    new DataView(bad.buffer).setUint32(78, 1_000_000)
    expect(mobiEncryption(bad)).toBeNull()
  })
})

describe('SHA-1 for the scrambled fonts of an EPUB', () => {
  const hex = (bytes: Uint8Array) => Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')

  it('matches the published values', () => {
    // The published values of the standard, written in pieces: a long run of hex digits in a file looks like a key.
    const value = (...parts: string[]) => parts.join('')
    expect(hex(sha1(''))).toBe(value('da39a3ee', '5e6b4b0d', '3255bfef', '95601890', 'afd80709'))
    expect(hex(sha1('abc'))).toBe(value('a9993e36', '4706816a', 'ba3e2571', '7850c26c', '9cd0d89d'))
    expect(hex(sha1('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'))).toBe(value('84983e44', '1c3bd26e', 'baae4aa1', 'f95129e5', 'e54670f1'))
    expect(hex(sha1('a'.repeat(1000)))).toBe(value('291e9a6c', '66994949', 'b57ba5e6', '50361e98', 'fc36b1ba'))
    expect(hex(sha1('Grüße ✓'))).toHaveLength(40)
  })
})

/** The text of every page of a book, read the way the reader reads a section. */
async function textOf(book: Awaited<ReturnType<typeof openEpub>>): Promise<string[]> {
  const out: string[] = []
  for (const section of book.sections) out.push((await section.createDocument?.())?.body?.textContent?.trim() ?? '')
  return out
}

describe('the addresses of a package and the names in the ZIP', () => {
  const pages = [
    { href: 'Text/Cover.xhtml', entry: 'Text/Cover.xhtml', text: 'Cover words' },
    { href: 'Text/Falkner%20-%20Part%2C%20Two_split_001.htm', entry: 'Text/Falkner - Part, Two_split_001.htm', text: 'Second words' },
    { href: 'Text/Falkner%20-%20Part%2C%20Two_split_002.htm', entry: 'Text/Falkner - Part, Two_split_002.htm', text: 'Third words' },
  ]

  it('finds a part whose address is escaped in the package and plain in the ZIP (a comma and spaces)', async () => {
    const book = await openEpub(epubWithPages(pages), ORIGIN)
    expect(await textOf(book)).toEqual(['Cover words', 'Second words', 'Third words'])
    // The sections have a size, so that the progress is told by the text and not by the first part alone.
    expect(book.sections.map((section) => section.size)).toSatisfy((sizes: (number | undefined)[]) => sizes.every((size) => (size ?? 0) > 0))
  })

  it('finds a part that really has a percent sign or an escape in its name, as written', async () => {
    const book = await openEpub(
      epubWithPages([
        { href: 'Text/a.xhtml', entry: 'Text/a.xhtml', text: 'First words' },
        { href: 'Text/100%25%20done.xhtml', entry: 'Text/100% done.xhtml', text: 'Second words' },
        { href: 'Text/50%2520.xhtml', entry: 'Text/50%20.xhtml', text: 'Third words' },
      ]),
      ORIGIN,
    )
    expect(await textOf(book)).toEqual(['First words', 'Second words', 'Third words'])
  })

  it('does not depend on how the names end: a package in the root, pages in .xml files, the type from the manifest', async () => {
    const book = await openEpub(
      epubWithPages(
        [
          { href: 'falkner-one.xml', entry: 'falkner-one.xml', text: 'First words' },
          { href: 'falkner-two.xml', entry: 'falkner-two.xml', text: 'Second words' },
        ],
        'inhalt.opf',
      ),
      ORIGIN,
    )
    expect(await textOf(book)).toEqual(['First words', 'Second words'])
    expect(book.metadata?.title).toBe('Test Book')
  })

  it('still names a protection in pages that end in .xml, because the manifest says they are pages', async () => {
    const noise = new Uint8Array(2000).map((_value, at) => (at * 37 + 11) % 256)
    // The middle page of the book is scrambled (what a copy protection does when it names itself nowhere).
    const scrambled = epubWithPages(
      [
        { href: 'one.xml', entry: 'one.xml', text: 'First' },
        { href: 'two.xml', entry: 'two.xml', text: 'Second', raw: noise },
        { href: 'three.xml', entry: 'three.xml', text: 'Third' },
      ],
      'inhalt.opf',
    )
    expect(await failure(openEpub(scrambled, ORIGIN))).toBe('protected')
  })

  it('does not take a picture in the spine for a scrambled chapter', async () => {
    const noise = new Uint8Array(2000).map((_value, at) => (at * 37 + 11) % 256)
    const book = await openEpub(
      epubWithPages(
        [
          { href: 'one.xml', entry: 'one.xml', text: 'First words' },
          { href: 'plate.xml', entry: 'plate.xml', text: 'x', type: 'image/png', raw: noise },
          { href: 'two.xml', entry: 'two.xml', text: 'Second words' },
        ],
        'inhalt.opf',
      ),
      ORIGIN,
    )
    expect(book.sections).toHaveLength(3)
  })

  it('opens a book whose end record counts the entries on this disk twice as high as all of them', async () => {
    const bytes = new Uint8Array(epubWithPages(pages))
    const end = bytes.length - 22
    const view = new DataView(bytes.buffer)
    view.setUint16(end + 8, view.getUint16(end + 10, true) * 2, true)
    const book = await openEpub(bytes.buffer as ArrayBuffer, ORIGIN)
    expect(await textOf(book)).toEqual(['Cover words', 'Second words', 'Third words'])
  })
})

describe('a book that names parts it does not hold', () => {
  const told: Notice[] = []
  beforeEach(() => {
    told.length = 0
    onNotice((notice) => told.push(notice))
  })
  afterEach(() => onNotice(() => undefined))

  it('opens, says that part of it is missing, and counts the missing part in the progress', async () => {
    const book = await openEpub(
      epubWithPages([
        { href: 'a.xhtml', entry: 'a.xhtml', text: 'First words' },
        { href: 'b.xhtml', entry: null, text: '' },
        { href: 'c.xhtml', entry: 'c.xhtml', text: 'Third words' },
        { href: 'd.xhtml', entry: null, text: '' },
      ]),
      ORIGIN,
    )
    expect(told).toEqual(['missing'])
    const sizes = book.sections.map((section) => section.size ?? 0)
    // The two parts that are there are about as big as each other; each missing part counts as much as one of them.
    expect(sizes[1]).toBe(sizes[0])
    expect(sizes[3]).toBe(sizes[0])
    expect(sizes.every((size) => size > 0)).toBe(true)
  })

  it('says nothing about a book that holds all it names', async () => {
    await openEpub(epub([['One', '<p>x</p>'], ['Two', '<p>y</p>']]), ORIGIN)
    expect(told).toEqual([])
  })

  it('is no book when not one part it names can be read', async () => {
    const none = epubWithPages([
      { href: 'a.xhtml', entry: null, text: '' },
      { href: 'b.xhtml', entry: null, text: '' },
    ])
    expect(await failure(openEpub(none, ORIGIN))).toBe('broken')
    expect(told).toEqual([])
  })
})
