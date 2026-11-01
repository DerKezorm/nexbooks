import { blankPage, cleanCss, cleanMarkup, kindOf, limits, linkAddress, normalizeType, ownAddress, pagePolicy } from './sanitize'

const ORIGIN = 'http://127.0.0.1:8580'
const XHTML = 'application/xhtml+xml'

function page(body: string, head = ''): string {
  return `<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink"><head><title>T</title>${head}</head><body>${body}</body></html>`
}

function clean(body: string, head = '', type = XHTML) {
  const result = cleanMarkup(page(body, head), type, ORIGIN)
  return { ...result, doc: new DOMParser().parseFromString(result.text, result.type as DOMParserSupportedType) }
}

describe('addresses a page may fetch', () => {
  it('lets through the parts the reader made from the book, data pictures and places in the page', () => {
    for (const ok of ['blob:null/abc', 'blob:http://127.0.0.1/1', 'data:image/png;base64,AAAA', 'data:image/svg+xml,%3Csvg%3E', 'data:font/woff2;base64,AAAA', '#note1', '', '  #x']) expect(ownAddress(ok), ok).toBe(true)
  })

  it('refuses everything that leads out, however it is written', () => {
    for (const bad of ['http://example.com/x.png', 'https://example.com', '//example.com/x', 'javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', 'java\nscript:alert(1)', 'data:text/html,<script>1</script>', 'file:///etc/passwd', 'ftp://example.com', '/api/v1/me', '../x.png', 'x.png', 'vbscript:x', '\u0001javascript:x']) {
      expect(ownAddress(bad), JSON.stringify(bad)).toBe(false)
    }
  })

  it('lets a link lead into the book, to the web, to a mail or a phone, never to code', () => {
    for (const ok of ['chapter2.xhtml#top', '#note', '../text/c3.xhtml', 'https://example.com/a', 'http://example.com', 'mailto:a@example.com', 'tel:+4912345']) expect(linkAddress(ok), ok).toBe(true)
    for (const bad of ['javascript:alert(1)', ' java\tscript:x', 'data:text/html,x', 'vbscript:x', 'file:///etc/passwd', 'blob:null/x', '//example.com/x', '\\\\server\\share']) expect(linkAddress(bad), bad).toBe(false)
  })
})

describe('a page of a book, cleaned', () => {
  it('keeps the text, the structure and the styles of the book', () => {
    const { doc, type } = clean('<h1 id="top">Title</h1><p class="x">One <em>two</em> <a href="c2.xhtml#top">three</a></p><ul><li>a</li></ul><table><tr><td>cell</td></tr></table>', '<style>p.x{color:red}</style>')
    expect(type).toBe(XHTML)
    expect(doc.querySelector('h1#top')?.textContent).toBe('Title')
    expect(doc.querySelector('p.x em')?.textContent).toBe('two')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('c2.xhtml#top')
    expect(doc.querySelectorAll('li, td')).toHaveLength(2)
    expect(doc.querySelector('style')?.textContent).toContain('color:red')
  })

  it('takes out scripts, frames, objects, forms, sounds, base and a refresh', () => {
    const { doc } = clean(
      '<p>text</p><script>alert(1)</script><iframe src="blob:null/1"></iframe><object data="x"></object><embed src="x"/><form action="http://example.com"><input name="a"/><button>go</button></form><video src="blob:x"></video><audio src="blob:x"></audio><noscript>n</noscript><template><p>t</p></template>',
      '<base href="http://example.com/"/><meta http-equiv="refresh" content="0;url=http://example.com"/><script src="x.js"></script><link rel="prefetch" href="http://example.com"/><link rel="stylesheet" href="http://example.com/x.css"/>',
    )
    expect(doc.querySelectorAll('script, iframe, object, embed, form, input, button, video, audio, noscript, template, base, link').length).toBe(0)
    expect(doc.querySelectorAll('meta[http-equiv="refresh"]')).toHaveLength(0)
    expect(doc.body.textContent).toBe('text')
  })

  it('keeps a style sheet that the reader made from the book, and none of the web', () => {
    const { doc } = clean('<p>x</p>', '<link rel="stylesheet" href="blob:null/1"/><link rel="stylesheet" href="https://example.com/a.css"/>')
    const links = Array.from(doc.querySelectorAll('link')).map((link) => link.getAttribute('href'))
    expect(links).toEqual(['blob:null/1'])
  })

  it('takes out every attribute that runs code', () => {
    const { doc } = clean('<p onclick="x()" onmouseover="y()" ONLOAD="z()" id="k">t</p><img src="blob:null/1" onerror="x()" alt="a"/><a href="javascript:x()">j</a><a href="data:text/html,x">d</a><a href="https://example.com/ok" target="_top">w</a><div srcdoc="&lt;script&gt;" is="x" ping="http://example.com"></div>')
    const attributes = Array.from(doc.querySelectorAll('*')).flatMap((element) => element.getAttributeNames())
    expect(attributes.filter((name) => name.toLowerCase().startsWith('on'))).toEqual([])
    expect(doc.querySelector('p')?.getAttribute('id')).toBe('k')
    const anchors = Array.from(doc.querySelectorAll('a'))
    expect(anchors[0].hasAttribute('href')).toBe(false)
    expect(anchors[1].hasAttribute('href')).toBe(false)
    // A link to the web stays, in no window of its own and with no way back to the reader.
    expect(anchors[2].getAttribute('href')).toBe('https://example.com/ok')
    expect(anchors[2].hasAttribute('target')).toBe(false)
    expect(anchors[2].getAttribute('rel')).toBe('noopener noreferrer')
    const div = doc.querySelector('div')!
    expect(['srcdoc', 'is', 'ping'].filter((name) => div.hasAttribute(name))).toEqual([])
  })

  it('takes out the addresses of pictures that lead out, and keeps the book own', () => {
    const { doc } = clean('<img src="blob:null/1" alt="own"/><img src="http://example.com/x.png" alt="far"/><img src="//example.com/x.png" alt="far2"/><img src="x.png" srcset="http://example.com/a.png 1x, blob:null/2 2x" alt="srcset"/><img src="data:image/png;base64,AAAA" alt="data"/>')
    const own = Array.from(doc.querySelectorAll('img')).map((image) => [image.getAttribute('alt'), image.getAttribute('src'), image.hasAttribute('srcset')])
    expect(own).toEqual([['own', 'blob:null/1', false], ['far', null, false], ['far2', null, false], ['srcset', null, false], ['data', 'data:image/png;base64,AAAA', false]])
  })

  it('cleans an inline style and a style element', () => {
    const { doc } = clean('<p style="background:url(http://example.com/x.png);width:expression(alert(1));color:red">t</p>', "<style>@import url('http://example.com/x.css');p{background:url(//example.com/b.png)}q{behavior:url(x.htc);-moz-binding:url(x)}@font-face{src:url(blob:null/f)}</style>")
    const inline = doc.querySelector('p')!.getAttribute('style')!
    expect(inline).not.toMatch(/example\.com|expression\(/)
    expect(inline).toContain('color:red')
    const sheet = doc.querySelector('style')!.textContent!
    expect(sheet).not.toMatch(/example\.com|@import|behavior|-moz-binding/)
    expect(sheet).toContain('url(blob:null/f)')
  })

  it('cleans an SVG in a page: no script, no event, no outside picture, no foreign object', () => {
    const { doc } = clean(
      '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><a xlink:href="javascript:x()"><circle r="5" onclick="x()"/></a><image xlink:href="http://example.com/i.png"/><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject><animate attributeName="href" to="javascript:x()"/><set attributeName="href" to="x"/><use xlink:href="#ok"/></svg>',
    )
    const svg = doc.querySelector('svg')!
    expect(svg.querySelectorAll('script, foreignObject, animate, set').length).toBe(0)
    expect(svg.querySelector('circle')?.hasAttribute('onclick')).toBe(false)
    expect(svg.querySelector('image')?.getAttributeNS('http://www.w3.org/1999/xlink', 'href')).toBeNull()
    expect(svg.querySelector('a')?.hasAttribute('xlink:href')).toBe(false)
    expect(svg.querySelector('use')?.getAttribute('xlink:href')).toBe('#ok')
  })

  it('puts a policy of its own in front of everything in the page', () => {
    const { doc } = clean('<p>x</p>', '<title>T</title>')
    const first = doc.head.firstElementChild!
    expect(first.localName).toBe('meta')
    expect(first.getAttribute('http-equiv')).toBe('Content-Security-Policy')
    const policy = first.getAttribute('content')!
    expect(policy).toBe(pagePolicy(ORIGIN))
    expect(policy).toContain("script-src 'none'")
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain("connect-src 'none'")
    expect(policy).toContain("object-src 'none'")
    expect(policy).toContain("base-uri 'none'")
    expect(policy).toContain("form-action 'none'")
    expect(policy).toContain(`font-src blob: data: ${ORIGIN}`)
    expect(policy).not.toMatch(/'unsafe-eval'|\*|https?:\/\/(?!127)/)
  })

  it('makes a head when the page has none', () => {
    const result = cleanMarkup('<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>', XHTML, ORIGIN)
    const doc = new DOMParser().parseFromString(result.text, XHTML)
    expect(doc.querySelector('head > meta[http-equiv="Content-Security-Policy"]')).toBeTruthy()
    expect(doc.querySelector('p')?.textContent).toBe('x')
  })

  it('reads a page that is no valid XHTML as HTML, and says so', () => {
    const result = cleanMarkup('<html><body><p>one<br>two<script>alert(1)</script></p></body></html>', XHTML, ORIGIN)
    expect(result.type).toBe('text/html')
    const doc = new DOMParser().parseFromString(result.text, 'text/html')
    expect(doc.querySelector('script')).toBeNull()
    expect(doc.body.textContent).toBe('onetwo')
  })

  it('cleans a page that is HTML as it is', () => {
    const result = cleanMarkup('<!DOCTYPE html><html><head></head><body><img src="http://example.com/x" onerror="x()"/><p>ok</p></body></html>', 'text/html', ORIGIN)
    expect(result.type).toBe('text/html')
    const doc = new DOMParser().parseFromString(result.text, 'text/html')
    expect(doc.querySelector('img')?.getAttribute('src')).toBeNull()
    expect(doc.querySelector('img')?.hasAttribute('onerror')).toBe(false)
    expect(doc.querySelector('meta[http-equiv="Content-Security-Policy"]')).toBeTruthy()
  })

  it('cleans a picture of SVG, handed out as an SVG', () => {
    const result = cleanMarkup('<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><script>alert(1)</script><rect width="5" height="5"/></svg>', 'image/svg+xml', ORIGIN)
    expect(result.type).toBe('image/svg+xml')
    expect(result.text).not.toMatch(/script|onload/)
    expect(result.text).toContain('rect')
    expect(result.text).not.toContain('Content-Security-Policy')
  })

  it('drops a processing instruction that fetches a style from outside', () => {
    const text = '<?xml version="1.0"?><?xml-stylesheet href="http://example.com/x.css" type="text/css"?><html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p>x</p></body></html>'
    expect(cleanMarkup(text, XHTML, ORIGIN).text).not.toContain('example.com')
  })
})

describe('a style sheet, cleaned', () => {
  it('keeps what is the book own and takes out what leads out', () => {
    const css = cleanCss('@import "blob:null/1"; @import url(http://example.com/a.css); p{background:url("blob:null/2")} q{background:url(https://example.com/b.png)} r{background:url(data:image/png;base64,AAAA)} s{width:expression(1)}')
    expect(css).toContain('@import "blob:null/1"')
    expect(css).toContain('url("blob:null/2")')
    expect(css).toContain('url(data:image/png;base64,AAAA)')
    expect(css).not.toMatch(/example\.com|expression\(/)
    expect(css).toContain('url("")')
  })

  it('takes out image-set() and the old code hooks', () => {
    expect(cleanCss('a{background:image-set("http://example.com/x.png" 1x)}')).not.toContain('example.com')
    expect(cleanCss('a{-moz-binding:url(x);behavior:url(x)}')).not.toMatch(/binding|behavior/)
  })
})

describe('the media type of a page', () => {
  const dirty = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body><p onclick="x()">text</p><script>alert(1)</script><img src="x:y" onerror="alert(2)"/></body></html>'

  it('is read the way a browser reads it: without its parameters and in any case', () => {
    expect(normalizeType('Application/XHTML+XML; charset=utf-8')).toBe('application/xhtml+xml')
    expect(normalizeType('  TEXT/HTML ;charset=x')).toBe('text/html')
    expect(normalizeType(undefined)).toBe('')
  })

  it('cleans every type a browser would draw as a page, and gives the result a type that is a page', () => {
    for (const type of ['application/xhtml+xml', 'application/xhtml+xml; charset=utf-8', 'APPLICATION/XHTML+XML', 'application/xml', 'APPLICATION/XML;charset=utf-8', 'text/xml', 'Text/XML; charset=utf-8', 'text/html', 'TEXT/HTML', 'text/html;charset=UTF-8']) {
      const result = cleanMarkup(dirty, type, ORIGIN)
      expect(result.text, type).not.toMatch(/<script|onclick|onerror/)
      expect(result.text, type).toContain('text')
      expect(result.text, type).toContain('Content-Security-Policy')
      expect(['application/xhtml+xml', 'text/html'], type).toContain(result.type)
    }
  })

  it('gives an XML page the type of an XHTML page, and an HTML page that of HTML', () => {
    expect(cleanMarkup(dirty, 'application/xml', ORIGIN).type).toBe('application/xhtml+xml')
    expect(cleanMarkup(dirty, 'text/xml', ORIGIN).type).toBe('application/xhtml+xml')
    expect(cleanMarkup(dirty, 'TEXT/HTML', ORIGIN).type).toBe('text/html')
  })

  it('shows an XML document that is no XHTML page as an empty page', () => {
    const rss = '<rss version="2.0"><channel><title>Not a page</title><script xmlns="http://www.w3.org/1999/xhtml">alert(1)</script></channel></rss>'
    for (const type of ['application/xml', 'text/xml', 'application/xhtml+xml']) {
      const result = cleanMarkup(rss, type, ORIGIN)
      expect(result.text, type).not.toMatch(/<script|Not a page/)
      expect(result.text, type).toContain('Content-Security-Policy')
      expect(result.text, type).toBe(blankPage(ORIGIN).text)
    }
  })

  it('cleans an SVG page with parameters or capitals in its type', () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><script>alert(1)</script><rect width="5" height="5"/></svg>'
    for (const type of ['image/svg+xml; charset=utf-8', 'IMAGE/SVG+XML']) {
      const result = cleanMarkup(svg, type, ORIGIN)
      expect(result.type, type).toBe('image/svg+xml')
      expect(result.text, type).not.toMatch(/script|onload/)
    }
  })

  it('knows what a part is by its type, and calls the rest nothing', () => {
    expect(kindOf('application/xhtml+xml; charset=utf-8')).toBe('page')
    expect(kindOf('Text/XML')).toBe('page')
    expect(kindOf('image/svg+xml')).toBe('page')
    expect(kindOf('text/css; charset=utf-8')).toBe('css')
    expect(kindOf('image/png')).toBe('image')
    expect(kindOf('font/woff2')).toBe('font')
    expect(kindOf('application/vnd.ms-opentype')).toBe('font')
    for (const none of ['', 'text/plain', 'application/octet-stream', 'application/x-dtbook+xml', 'application/mathml+xml', 'application/pdf', 'application/rss+xml', 'application/xslt+xml']) {
      expect(kindOf(none), none).toBe('other')
    }
  })
})

describe('a page that is too deep or too big', () => {
  const depthOf = (node: Node): number => {
    let deepest = 0
    const stack: { node: Node; depth: number }[] = [{ node, depth: 0 }]
    while (stack.length) {
      const { node: current, depth } = stack.pop()!
      deepest = Math.max(deepest, depth)
      for (const child of Array.from(current.childNodes)) stack.push({ node: child, depth: depth + 1 })
    }
    return deepest
  }

  it('flattens a page nested six hundred deep, which no browser parser keeps, keeps its text and says so', () => {
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(600) + 'deep text' + '</div>'.repeat(600) + '</body></html>'
    const result = cleanMarkup(html, 'text/html', ORIGIN)
    expect(result.flattened).toBe(true)
    expect(result.cut).toBe(false)
    const doc = new DOMParser().parseFromString(result.text, 'text/html')
    expect(doc.body.textContent).toContain('deep text')
    // The parser a browser has for HTML nests no deeper than 512 anyway; the page that is handed out is far shallower.
    expect(depthOf(doc.body)).toBeLessThanOrEqual(limits.depth + 1)
  })

  it('flattens an XHTML page of the same kind', () => {
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<p><span>'.repeat(250) + 'deep text' + '</span></p>'.repeat(250) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(true)
    expect(result.text).toContain('deep text')
    expect(depthOf(new DOMParser().parseFromString(result.text, result.type as DOMParserSupportedType))).toBeLessThanOrEqual(limits.depth + 4)
  })

  it('lets the inline elements of one place give up their tags past a limit, and keeps all their text, without a notice', () => {
    const before = limits.inline
    limits.inline = 20
    try {
      const words = Array.from({ length: 50 }, (_, n) => (n % 10 === 3 ? `<a href="javascript:x()" onclick="y()">w${n}</a>` : `<span class="c">w${n}</span>`)).join('')
      const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p id="one">' + words + '<img src="blob:null/1" alt="a"/><br/>end</p><p id="two"><i>other</i></p></body></html>'
      const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
      expect(result.flattened).toBe(false)
      expect(result.cut).toBe(false)
      // All the text, in order; the first twenty keep their tags, the rest do not; a picture and a break stay; the next place counts for itself.
      const text = result.text.replace(/<[^>]*>/g, '')
      expect(text).toContain(Array.from({ length: 50 }, (_, n) => `w${n}`).join(''))
      expect((result.text.match(/<span|<a[ >]/g) ?? []).length).toBe(20)
      expect(result.text).not.toMatch(/javascript:|onclick/)
      expect(result.text).toContain('blob:null/1')
      expect(result.text).toContain('<br')
      expect(result.text).toContain('<i>other</i>')
    } finally {
      limits.inline = before
    }
  })

  it('never counts blocks: a chapter of thousands of paragraphs, and a list of items, stay whole', () => {
    const before = limits.inline
    limits.inline = 20
    try {
      const body = Array.from({ length: 300 }, (_, n) => `<p>para ${n}</p>`).join('') + '<ul>' + Array.from({ length: 100 }, (_, n) => `<li>item ${n}</li>`).join('') + '</ul>'
      const result = cleanMarkup('<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + body + '</body></html>', 'application/xhtml+xml', ORIGIN)
      expect((result.text.match(/<p>/g) ?? []).length).toBe(300)
      expect((result.text.match(/<li>/g) ?? []).length).toBe(100)
    } finally {
      limits.inline = before
    }
  })

  it('still cleans, and keeps its pictures, below the depth that is flattened', () => {
    const inner = '<p onclick="x()" style="background:u\\72l(http://example.com/a.png)">kept <b>words</b></p><script>alert(1)</script><img src="blob:null/1" onerror="y()" alt="a"/><br/><a href="javascript:z()">j</a>'
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth + 20) + inner + '</div>'.repeat(limits.depth + 20) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(true)
    expect(result.text).not.toMatch(/<script|onclick|onerror|javascript:|example\.com/)
    expect(result.text).toContain('kept')
    expect(result.text).toContain('words')
    expect(result.text).toContain('blob:null/1')
    expect(result.text).toContain('<br')
  })

  it('drops the empty elements below the depth that is flattened, except pictures, breaks and places a link leads to', () => {
    const inner = '<div></div>'.repeat(50) + '<span></span><img src="blob:null/1" alt="a"/><hr/><div id="note3"></div><a name="here"></a><p>text</p>'
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth + 5) + inner + '</div>'.repeat(limits.depth + 5) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(true)
    // The wrappers that were within the depth stay; the 50 empty divs below it are gone.
    expect((result.text.match(/<div>/g) ?? []).length).toBeLessThanOrEqual(limits.depth)
    expect(result.text.match(/<div\/>|<div><\/div>/g) ?? []).toHaveLength(0)
    expect(result.text).toContain('<img')
    expect(result.text).toContain('<hr')
    expect(result.text).toContain('id="note3"')
    expect(result.text).toContain('name="here"')
    expect(result.text).toContain('text')
    expect(result.text).not.toContain('<span')
  })

  it('leaves a page of real depth alone and says nothing', () => {
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth - 5) + 'text' + '</div>'.repeat(limits.depth - 5) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(false)
    expect(result.cut).toBe(false)
    expect(result.text.match(/<div>/g)).toHaveLength(limits.depth - 5)
  })

  it('cuts what is past the number of nodes a page may have, from there to the end of the page, and says so', () => {
    const before = limits.nodes
    limits.nodes = 200
    try {
      const body = Array.from({ length: 500 }, (_, n) => `<p id="p${n}">word ${n}</p>`).join('')
      const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + body + '</body></html>'
      const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
      expect(result.cut).toBe(true)
      const kept = result.text.match(/id="p(\d+)"/g)!.map((each) => Number(each.slice(5, -1)))
      // The page is cut short: the first paragraphs whole, in order, none after the cut, and no paragraph without its text but the one the cut fell into.
      expect(kept[0]).toBe(0)
      expect(kept.length).toBeGreaterThan(50)
      expect(kept.length).toBeLessThan(110)
      expect(kept).toEqual(Array.from({ length: kept.length }, (_, n) => n))
      // At most the one element the cut fell into is without its text.
      expect((result.text.match(/<p[^>]*><\/p>|<p[^>]*\/>/g) ?? []).length).toBeLessThanOrEqual(1)
    } finally {
      limits.nodes = before
    }
  })

  it('still cleans, and keeps its pictures, below the depth that is flattened', () => {
    const inner = '<p onclick="x()" style="background:u\\72l(http://example.com/a.png)">kept <b>words</b></p><script>alert(1)</script><img src="blob:null/1" onerror="y()" alt="a"/><br/><a href="javascript:z()">j</a>'
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth + 20) + inner + '</div>'.repeat(limits.depth + 20) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(true)
    expect(result.text).not.toMatch(/<script|onclick|onerror|javascript:|example\.com/)
    expect(result.text).toContain('kept')
    expect(result.text).toContain('words')
    expect(result.text).toContain('blob:null/1')
    expect(result.text).toContain('<br')
  })

  it('drops the empty elements below the depth that is flattened, except pictures, breaks and places a link leads to', () => {
    const inner = '<div></div>'.repeat(50) + '<span></span><img src="blob:null/1" alt="a"/><hr/><div id="note3"></div><a name="here"></a><p>text</p>'
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth + 5) + inner + '</div>'.repeat(limits.depth + 5) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(true)
    // The wrappers that were within the depth stay; the 50 empty divs below it are gone.
    expect((result.text.match(/<div>/g) ?? []).length).toBeLessThanOrEqual(limits.depth)
    expect(result.text.match(/<div\/>|<div><\/div>/g) ?? []).toHaveLength(0)
    expect(result.text).toContain('<img')
    expect(result.text).toContain('<hr')
    expect(result.text).toContain('id="note3"')
    expect(result.text).toContain('name="here"')
    expect(result.text).toContain('text')
    expect(result.text).not.toContain('<span')
  })

  it('leaves a page of real depth alone and says nothing', () => {
    const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<div>'.repeat(limits.depth - 5) + 'text' + '</div>'.repeat(limits.depth - 5) + '</body></html>'
    const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
    expect(result.flattened).toBe(false)
    expect(result.cut).toBe(false)
    expect(result.text.match(/<div>/g)).toHaveLength(limits.depth - 5)
  })

  it('cuts what is past the number of nodes a page may have, and says so', () => {
    const before = limits.nodes
    limits.nodes = 200
    try {
      const html = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + '<i>x</i>'.repeat(500) + '</body></html>'
      const result = cleanMarkup(html, 'application/xhtml+xml', ORIGIN)
      expect(result.cut).toBe(true)
      expect(result.text.match(/<i>/g)!.length).toBeLessThanOrEqual(200)
    } finally {
      limits.nodes = before
    }
  })
})

describe('the CSS in a page', () => {
  it('is read the way a browser reads it, in a style element, in an attribute and in an SVG presentation attribute', () => {
    const { doc } = clean(
      '<p style="background:u\\72l(http://example.com/a.png)">x</p><svg xmlns="http://www.w3.org/2000/svg"><rect fill="\\75rl(http://example.com/a.svg#p)" filter="url(http://example.com/f.svg#f)" width="1" height="1"/></svg>',
      '<style>@\\69mport url(http://example.com/a.css); p{background:u\\72l(http://example.com/b.png)}</style>',
    )
    expect(new XMLSerializer().serializeToString(doc)).not.toContain('example.com')
  })
})
