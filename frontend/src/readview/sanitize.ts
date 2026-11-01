/**
 * What a page of a book is made to be before the browser sees it. The text of a book is HTML, CSS and pictures from somebody
 * else, and this is the first of the layers around it (the others: the frame has no network, its policy is
 * inherited by the pages, and every page carries a policy of its own that forbids scripts, frames and anything but the
 * pictures, styles and fonts that come out of the book):
 *
 * - no script of any kind (`<script>`, event attributes, `javascript:` addresses), no frames, objects or embeds, no forms,
 *   no `<base>`, no `<meta http-equiv>` (a refresh to another page), no sound or video;
 * - an address that leads out of the book is taken away: a picture, style, font or link in the page may point only to a
 *   `blob:` address the reader made from the book, or hold its data (`data:`), or point inside the page (`#...`); a link may
 *   also be an address of the web, to be opened by the person in a tab of its own (the page does that, with `noopener`);
 * - CSS: no `url()` that is not the book's own, no `@import` but the book's own, nothing that runs (`expression`, `behavior`,
 *   `-moz-binding`). CSS is read the way a browser reads it (`css.ts`): a name written with escapes is the name it spells;
 * - a page that is deeper than `limits.depth` is flattened (the elements below that depth give up their tags and keep their
 *   content) and a page of more than `limits.nodes` nodes is cut, and the reader is told (a browser is brought down by a
 *   document nested twenty thousand deep, and a book of two kilobytes can be that).
 *
 * It keeps everything a reader needs: the structure, the styles of the book, its pictures and its links inside the book.
 */

import { cleanCssWith } from './css'

/** The policy each page of a book carries as a `<meta>`; the frame's own policy is the second wall. */
export function pagePolicy(origin: string): string {
  return [
    "default-src 'none'",
    'img-src blob: data:',
    "media-src 'none'",
    `font-src blob: data: ${origin}`,
    "style-src 'unsafe-inline' blob:",
    "script-src 'none'",
    'frame-src blob:',
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "connect-src 'none'",
  ].join('; ')
}

const REMOVE = new Set([
  'script', 'noscript', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'base', 'form', 'input', 'button',
  'select', 'textarea', 'option', 'optgroup', 'datalist', 'audio', 'video', 'track', 'portal', 'foreignobject', 'animate',
  'animatetransform', 'animatemotion', 'set', 'fetch', 'handler', 'listener', 'template', 'dialog', 'param', 'bgsound',
])

/** Attributes that carry an address a browser would fetch or follow. */
const ADDRESSES = new Set(['src', 'href', 'xlink:href', 'srcset', 'poster', 'data', 'background', 'action', 'formaction', 'cite', 'longdesc', 'icon', 'manifest', 'ping', 'codebase', 'usemap', 'profile', 'lowsrc', 'dynsrc'])

/** An address as a browser reads it: tabs, line breaks and the other control characters are dropped, before the scheme and inside
 * it ("java	script:"). A check of an address has to look at what the browser will look at. */
function squeeze(value: string): string {
  let out = ''
  for (const character of value) if (character.charCodeAt(0) > 0x20) out += character
  return out
}

/** An address the page may fetch for itself: a `blob:` the reader made from the book, a `data:` picture or font, or a place
 * inside the page. Nothing else. */
export function ownAddress(value: string): boolean {
  const text = squeeze(value)
  if (text === '' || text.startsWith('#')) return true
  return /^(blob:|data:(image\/(png|jpe?g|gif|webp|avif|bmp|svg\+xml)|font\/[a-z0-9.+-]+|application\/(x-)?font-?[a-z0-9.+-]*|application\/vnd\.ms-fontobject)[;,])/i.test(text)
}

/** An address a link may have: the book itself, the web, a mail or a phone. Never code. */
export function linkAddress(value: string): boolean {
  const text = squeeze(value)
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return /^(https?:|mailto:|tel:)/i.test(text)
  // No scheme: a path inside the book (the reader resolves it), or a place in the page. Protocol-relative addresses go out.
  return !text.startsWith('//') && !text.startsWith('\\\\')
}

/** CSS with nothing in it that fetches from outside or runs. */
export function cleanCss(css: string): string {
  const cleaned = cleanCssWith(css, ownAddress, (address) => /^blob:/i.test(squeeze(address)))
  // A style element ends at `</style`; nothing the cleaning did may make one (it leaves a comment where it took something out).
  return cleaned.replace(/<\/(style)/gi, String.raw`<\/$1`)
}

function cleanAttributes(element: Element): void {
  for (const attribute of Array.from(element.attributes)) {
    const name = attribute.name.toLowerCase()
    if (name.startsWith('on')) {
      element.removeAttribute(attribute.name)
    } else if (name === 'srcdoc' || name === 'target' || name === 'is' || name === 'http-equiv') {
      element.removeAttribute(attribute.name)
    } else if (name === 'style') {
      element.setAttribute(attribute.name, cleanCss(attribute.value))
    } else if (ADDRESSES.has(name) || name.endsWith(':href')) {
      const isLink = element.localName.toLowerCase() === 'a' && (name === 'href' || name === 'xlink:href')
      const ok = isLink ? linkAddress(attribute.value) : name === 'srcset' ? srcsetOwn(attribute.value) : ownAddress(attribute.value)
      if (!ok) element.removeAttribute(attribute.name)
    } else if (attribute.value.includes('(')) {
      // An SVG presentation attribute (`fill`, `filter`, `mask`, `cursor`...) is a CSS value and reads `url()` like one.
      const cleaned = cleanCss(attribute.value)
      if (cleaned !== attribute.value) element.setAttribute(attribute.name, cleaned)
    }
  }
  if (element.localName.toLowerCase() === 'a' && element.hasAttribute('href')) element.setAttribute('rel', 'noopener noreferrer')
}

function srcsetOwn(value: string): boolean {
  return value.split(',').every((candidate) => ownAddress(candidate.trim().split(/\s+/)[0] ?? ''))
}

/** How deep a page may nest before the elements below give up their tags, and how many nodes it may have. A browser lays out
 * a page of several hundred nested boxes with its native stack, and the stack is what runs out: a Chromium tab was brought down
 * by 200 nested `div` in a page of a few bytes. Real books nest a few dozen levels at most.
 *
 * `inline` is how many elements of the kind that flows with the text (`span`, `a`, `b`, `em` and the like) one element may
 * hold before the rest give up their tags and keep their text. A run of tens of thousands of such siblings with no place to
 * break the line is laid out in time that grows with its square in WebKit (a minute for 20,000), and no real paragraph has
 * a tenth of that many. Blocks (`p`, `div`, `li`) are never counted: a chapter of thousands of paragraphs stays whole. */
export const limits = { depth: 64, nodes: 100_000, inline: 2000 }

const FLOWS_WITH_TEXT = new Set([
  'a', 'abbr', 'acronym', 'b', 'bdi', 'bdo', 'big', 'cite', 'code', 'data', 'del', 'dfn', 'em', 'font', 'i', 'ins', 'kbd', 'mark', 'q', 's',
  'samp', 'small', 'span', 'strike', 'strong', 'sub', 'sup', 'time', 'tt', 'u', 'var',
])

export type Walked = { flattened: boolean; cut: boolean }

/** `<?xml-stylesheet href="...">` and the like: only the book's own stay. */
function keepInstruction(instruction: ProcessingInstruction): boolean {
  return instruction.target === 'xml' || /href\s*=\s*["']blob:/i.test(instruction.data)
}

/**
 * Visits every element below `root` in the order of the page and without recursion (the depth of a hostile page must not become
 * the depth of ours): the ones `visit` refuses are removed with what is in them. An element deeper than `limits.depth` that has
 * content is replaced by that content, all the way down in one step (text, and the elements that mean something empty, such as a
 * picture, stay in their order; the wrappers go). Once the page has more than `limits.nodes` nodes, **the rest of the page is
 * cut off from there to its end**, as a page that was cut short would be (leaving the elements and taking out their text left
 * a page a browser could hardly lay out).
 */
function walk(root: Node, visit: (element: Element) => boolean): Walked {
  const result: Walked = { flattened: false, cut: false }
  let count = 0
  /** Counts a node; false once the page has more than it may. */
  const tick = (): boolean => {
    if (++count <= limits.nodes) return true
    result.cut = true
    return false
  }
  const stack: { node: Node; depth: number; surplus?: true }[] = Array.from(root.childNodes, (node) => ({ node, depth: 1 })).reverse()
  while (stack.length > 0) {
    const { node, depth, surplus } = stack.pop()!
    if (!tick()) {
      // This node and everything after it, up to the end of the page: what is on the stack is exactly that.
      node.parentNode?.removeChild(node)
      for (const later of stack) later.node.parentNode?.removeChild(later.node)
      break
    }
    if (node.nodeType === 1) {
      const element = node as Element
      if (!visit(element)) {
        element.remove()
        continue
      }
      if ((surplus || depth > limits.depth) && element.firstChild) {
        // Too many inline elements in one place is not a page nested too deeply: nothing is said of it.
        if (!surplus) result.flattened = true
        const kept = hoist(element, visit, tick)
        const fragment = element.ownerDocument.createDocumentFragment()
        for (const each of kept) fragment.append(each)
        element.replaceWith(fragment)
        if (result.cut) {
          // The page ran out in the middle of this element: the rest goes, from here on.
          for (const later of stack) later.node.parentNode?.removeChild(later.node)
          break
        }
      } else if (element.firstChild) {
        // The inline elements past the limit keep their text and lose their tags, as those too deep do (see `limits.inline`).
        let inline = 0
        const children = Array.from(element.childNodes, (child) => {
          const flows = child.nodeType === 1 && child.firstChild !== null && FLOWS_WITH_TEXT.has((child as Element).localName.toLowerCase())
          return flows && ++inline > limits.inline ? { node: child, depth: depth + 1, surplus: true as const } : { node: child, depth: depth + 1 }
        })
        for (let index = children.length - 1; index >= 0; index--) stack.push(children[index])
      }
    } else if (node.nodeType === 7 && !keepInstruction(node as ProcessingInstruction)) {
      node.parentNode?.removeChild(node)
    }
  }
  return result
}

/** Elements that mean something with nothing in them (a picture, a line break); any other empty element deeper than the depth
 * that is flattened is dropped, unless it is a place that a link or the contents may lead to (it has an `id` or a `name`). A
 * page of a hundred thousand empty `div` is what a browser's parser makes of a document nested that deep. */
const MEANS_SOMETHING_EMPTY = new Set(['img', 'br', 'hr', 'wbr', 'svg', 'image', 'math', 'picture', 'canvas'])

/** What is left of an element's content when its wrappers are taken away, in document order, each element cleaned by `visit`. */
function hoist(element: Element, visit: (element: Element) => boolean, tick: () => boolean): Node[] {
  const kept: Node[] = []
  const stack: Node[] = Array.from(element.childNodes).reverse()
  while (stack.length > 0) {
    const node = stack.pop()!
    if (!tick()) continue
    if (node.nodeType === 1) {
      const inner = node as Element
      if (!visit(inner)) continue
      if (inner.firstChild) stack.push(...Array.from(inner.childNodes).reverse())
      else if (MEANS_SOMETHING_EMPTY.has(inner.localName.toLowerCase()) || inner.hasAttribute('id') || inner.hasAttribute('name')) kept.push(inner)
    } else if (node.nodeType === 7) {
      if (keepInstruction(node as ProcessingInstruction)) kept.push(node)
    } else kept.push(node)
  }
  return kept
}

function decide(element: Element): boolean {
  const name = element.localName.toLowerCase()
  if (REMOVE.has(name)) return false
  // A refresh to another page, or a policy of the book's own that would stand beside ours.
  if (name === 'meta' && element.hasAttribute('http-equiv')) return false
  if (name === 'link') {
    const rel = (element.getAttribute('rel') ?? '').toLowerCase().split(/\s+/)
    if (!rel.includes('stylesheet') || !/^blob:/.test((element.getAttribute('href') ?? '').trim())) return false
  }
  if (name === 'style') element.textContent = cleanCss(element.textContent ?? '')
  cleanAttributes(element)
  return true
}

// ---- Media types ---------------------------------------------------------------------------------------------------------

/** A media type as the browser compares it: no parameters (`; charset=utf-8`), no white space, lower case. */
export function normalizeType(type: unknown): string {
  return String(type ?? '').split(';')[0].trim().toLowerCase()
}

/** Types a browser shows as a page of markup. `application/xml` and `text/xml` are no types of a book's pages, but a browser
 * draws them as one (with an XHTML root they are an XHTML page, scripts and all), so they are cleaned like one. */
export const XML_PAGES = new Set(['application/xml', 'text/xml'])
export const PAGES = new Set(['application/xhtml+xml', 'text/html', 'image/svg+xml', ...XML_PAGES])

/** What a part of a book is, by its media type: a page, a style sheet, a picture, a typeface, or nothing the reader knows. */
export type Kind = 'page' | 'css' | 'image' | 'font' | 'other'

export function kindOf(type: unknown): Kind {
  const text = normalizeType(type)
  if (PAGES.has(text)) return 'page'
  if (text === 'text/css') return 'css'
  if (text.startsWith('image/')) return 'image'
  if (/^(font\/|application\/(x-)?font|application\/vnd\.ms-(opentype|fontobject)|application\/x-truetype)/.test(text)) return 'font'
  return 'other'
}

const XHTML_NS = 'http://www.w3.org/1999/xhtml'

type Cleaned = { text: string; type: string; flattened: boolean; cut: boolean }

/** A page with nothing on it (for a part that is no page, or an XML document that is no XHTML). */
export function blankPage(origin: string): Cleaned {
  return cleanMarkup('<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body></body></html>', 'application/xhtml+xml', origin)
}

/**
 * The page made safe, as text for the page's own `blob:` address. `type` is the media type the manifest gives it (parameters
 * and case are ignored); the return value says which one to give the result (a page that is not valid XHTML is read as HTML,
 * and handed out as HTML). `flattened` and `cut` say that the page was too deep or too big and the reader has to tell.
 */
export function cleanMarkup(text: string, type: string, origin: string): Cleaned {
  const given = normalizeType(type)
  // An XML page is an XHTML page, or none. SVG is SVG. Everything else that gets here is read as HTML.
  let kind = given === 'image/svg+xml' || given === 'text/html' ? given : 'application/xhtml+xml'
  const parser = new DOMParser()
  let doc = parser.parseFromString(text, kind as DOMParserSupportedType)
  if (kind !== 'text/html' && (doc.querySelector('parsererror') || !doc.documentElement)) {
    if (kind === 'image/svg+xml') return { text: '<svg xmlns="http://www.w3.org/2000/svg"/>', type: kind, flattened: false, cut: false }
    kind = 'text/html'
    doc = parser.parseFromString(text, 'text/html')
  }
  let walked: Walked = { flattened: false, cut: false }
  if (doc.documentElement) {
    // The root itself may carry event attributes and addresses.
    cleanAttributes(doc.documentElement)
    walked = walk(doc, decide)
  }
  if (kind === 'application/xhtml+xml' && !(doc.documentElement?.namespaceURI === XHTML_NS && doc.documentElement.localName === 'html')) {
    // An XML document whose root is no XHTML `html` is no page of a book; a browser would still draw its XHTML elements.
    return blankPage(origin)
  }
  if (kind !== 'image/svg+xml' && doc.documentElement) {
    const meta = doc.createElementNS(XHTML_NS, 'meta')
    meta.setAttribute('http-equiv', 'Content-Security-Policy')
    meta.setAttribute('content', pagePolicy(origin))
    let head = Array.from(doc.documentElement.children).find((child) => child.localName.toLowerCase() === 'head')
    if (!head) {
      head = doc.createElementNS(doc.documentElement.namespaceURI, 'head')
      doc.documentElement.insertBefore(head, doc.documentElement.firstChild)
    }
    head.insertBefore(meta, head.firstChild)
  }
  // Written out as it came: XML stays XML (a page that is HTML stays HTML), so that the places in the page that a search finds
  // in the book's own text are the places in the page that is shown.
  if (kind === 'text/html' || !('XMLSerializer' in globalThis)) return { text: '<!DOCTYPE html>' + doc.documentElement.outerHTML, type: 'text/html', ...walked }
  return { text: new XMLSerializer().serializeToString(doc), type: kind, ...walked }
}
