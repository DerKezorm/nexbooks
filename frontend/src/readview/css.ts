/**
 * CSS from a book, made safe by reading it the way a browser reads it.
 *
 * A search for the text `url(` is not enough: CSS lets a name be written with escapes (`u\72l(`, `\75rl(`, `@\69mport`), and
 * a comment or a line break may sit where a regular expression does not expect it. So the style sheet is first cut into
 * tokens by the rules of CSS Syntax Level 3 (names are decoded, a string is read as a string), and the decisions are made
 * on the decoded tokens:
 *
 * - an address (`url(...)` with or without quotes) stays only when it is the book's own (`ownAddress`);
 * - `@import` stays only for a `blob:` address the reader made from the book;
 * - the functions that fetch by a bare string or by a form `url()` does not cover (`image-set`, `image`, `src`, `cross-fade`)
 *   and the one that runs (`expression`) are replaced by `none`;
 * - the properties `behavior` and `-moz-binding` are renamed so that they mean nothing.
 *
 * Everything else is written out exactly as it was read, so that the browser reads the result as the same tokens (a style
 * sheet is never changed by guessing). What is replaced is replaced by text that is complete in itself.
 */

type Kind = 'ws' | 'comment' | 'ident' | 'function' | 'atkw' | 'hash' | 'string' | 'badstring' | 'url' | 'badurl' | 'number' | 'delim' | 'punct'

type Token = { kind: Kind; raw: string; value: string }

const isDigit = (c: number) => c >= 0x30 && c <= 0x39
const isHex = (c: number) => isDigit(c) || (c >= 0x41 && c <= 0x46) || (c >= 0x61 && c <= 0x66)
const isLetter = (c: number) => (c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a)
const isNameStart = (c: number) => isLetter(c) || c === 0x5f || c >= 0x80
const isNameChar = (c: number) => isNameStart(c) || isDigit(c) || c === 0x2d
const isNewline = (c: number) => c === 0x0a
const isSpace = (c: number) => c === 0x20 || c === 0x09 || c === 0x0a
/** Characters that do not belong in an unquoted address (CSS Syntax: non-printable code points). */
const isNonPrintable = (c: number) => (c >= 0 && c <= 0x08) || c === 0x0b || (c >= 0x0e && c <= 0x1f) || c === 0x7f

/** The text as a CSS parser first sees it: one kind of line break, no NUL. */
function preprocess(css: string): string {
  return css.replace(/\r\n?|\f/g, '\n').replace(/\0/g, '�')
}

class Reader {
  constructor(readonly text: string) {}
  at = 0

  code(offset = 0): number {
    const index = this.at + offset
    return index < this.text.length ? this.text.charCodeAt(index) : -1
  }

  /** Whether a backslash at `offset` starts an escape (it does unless a line break follows). */
  validEscape(offset = 0): boolean {
    return this.code(offset) === 0x5c && this.code(offset + 1) !== 0x0a
  }

  /** Whether a name starts at `offset` (a letter, an underscore, a non-ASCII character or an escape). */
  startsName(offset = 0): boolean {
    const c = this.code(offset)
    if (c === 0x2d) {
      const next = this.code(offset + 1)
      return isNameStart(next) || next === 0x2d || this.validEscape(offset + 1)
    }
    return isNameStart(c) || this.validEscape(offset)
  }

  /** Whether a number starts at `offset`. */
  startsNumber(offset = 0): boolean {
    const c = this.code(offset)
    if (c === 0x2b || c === 0x2d) {
      const next = this.code(offset + 1)
      return isDigit(next) || (next === 0x2e && isDigit(this.code(offset + 2)))
    }
    if (c === 0x2e) return isDigit(this.code(offset + 1))
    return isDigit(c)
  }

  /** An escape after the backslash: up to six hex digits and one space, or the next character as it is. */
  escape(): string {
    this.at++ // the backslash
    const c = this.code()
    if (c === -1) return '�'
    if (isHex(c)) {
      let digits = ''
      while (digits.length < 6 && isHex(this.code())) digits += this.text[this.at++]
      if (isSpace(this.code())) this.at++
      const value = parseInt(digits, 16)
      return value === 0 || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff) ? '�' : String.fromCodePoint(value)
    }
    const char = String.fromCodePoint(this.text.codePointAt(this.at)!)
    this.at += char.length
    return char
  }

  /** A name, decoded. */
  name(): string {
    let out = ''
    for (;;) {
      const c = this.code()
      if (isNameChar(c)) {
        const char = String.fromCodePoint(this.text.codePointAt(this.at)!)
        out += char
        this.at += char.length
      } else if (this.validEscape()) out += this.escape()
      else return out
    }
  }

  /** The rest of a bad address, up to and with the closing parenthesis. */
  badUrlRemnants(): void {
    for (;;) {
      const c = this.code()
      if (c === -1) return
      this.at++
      if (c === 0x29) return
      // An escaped character does not close it (a parenthesis too).
      if (c === 0x5c && this.code() !== -1 && this.code() !== 0x0a) this.at++
    }
  }

  string(quote: number): Token {
    const start = this.at
    this.at++
    let value = ''
    for (;;) {
      const c = this.code()
      if (c === -1) break
      if (c === quote) {
        this.at++
        break
      }
      if (isNewline(c)) return { kind: 'badstring', raw: this.text.slice(start, this.at), value }
      if (c === 0x5c) {
        if (this.code(1) === -1) {
          this.at++
          break
        }
        if (this.code(1) === 0x0a) {
          this.at += 2
          continue
        }
        value += this.escape()
        continue
      }
      const char = String.fromCodePoint(this.text.codePointAt(this.at)!)
      value += char
      this.at += char.length
    }
    return { kind: 'string', raw: this.text.slice(start, this.at), value }
  }

  /** After `url(` and its white space: an address without quotes. */
  unquotedUrl(start: number): Token {
    let value = ''
    for (;;) {
      const c = this.code()
      if (c === -1) break
      if (c === 0x29) {
        this.at++
        break
      }
      if (isSpace(c)) {
        while (isSpace(this.code())) this.at++
        if (this.code() === 0x29) {
          this.at++
          break
        }
        if (this.code() === -1) break
        this.badUrlRemnants()
        return { kind: 'badurl', raw: this.text.slice(start, this.at), value: '' }
      }
      if (c === 0x22 || c === 0x27 || c === 0x28 || isNonPrintable(c)) {
        this.badUrlRemnants()
        return { kind: 'badurl', raw: this.text.slice(start, this.at), value: '' }
      }
      if (c === 0x5c) {
        if (this.validEscape()) {
          value += this.escape()
          continue
        }
        this.badUrlRemnants()
        return { kind: 'badurl', raw: this.text.slice(start, this.at), value: '' }
      }
      value += this.text[this.at++]
    }
    return { kind: 'url', raw: this.text.slice(start, this.at), value }
  }

  next(): Token | null {
    if (this.at >= this.text.length) return null
    const start = this.at
    const c = this.code()
    const make = (kind: Kind, value = ''): Token => ({ kind, raw: this.text.slice(start, this.at), value })

    if (c === 0x2f && this.code(1) === 0x2a) {
      const end = this.text.indexOf('*/', this.at + 2)
      this.at = end === -1 ? this.text.length : end + 2
      return make('comment')
    }
    if (isSpace(c)) {
      while (isSpace(this.code())) this.at++
      return make('ws')
    }
    if (c === 0x22 || c === 0x27) return this.string(c)
    if (c === 0x23) {
      if (isNameChar(this.code(1)) || this.validEscape(1)) {
        this.at++
        return make('hash', this.name())
      }
      this.at++
      return make('delim')
    }
    if ('()[]{},:;'.includes(String.fromCharCode(c))) {
      this.at++
      return make('punct', String.fromCharCode(c))
    }
    if (this.startsNumber()) {
      if (c === 0x2b || c === 0x2d) this.at++
      while (isDigit(this.code())) this.at++
      if (this.code() === 0x2e && isDigit(this.code(1))) {
        this.at++
        while (isDigit(this.code())) this.at++
      }
      if ((this.code() === 0x65 || this.code() === 0x45) && (isDigit(this.code(1)) || ((this.code(1) === 0x2b || this.code(1) === 0x2d) && isDigit(this.code(2))))) {
        this.at += 2
        while (isDigit(this.code())) this.at++
      }
      return make('number')
    }
    if (c === 0x40) {
      if (this.startsName(1)) {
        this.at++
        return make('atkw', this.name())
      }
      this.at++
      return make('delim')
    }
    if (c === 0x3c && this.text.startsWith('<!--', this.at)) {
      this.at += 4
      return make('delim')
    }
    if (c === 0x2d && this.text.startsWith('-->', this.at)) {
      this.at += 3
      return make('delim')
    }
    if (this.startsName()) {
      const name = this.name()
      if (this.code() === 0x28) {
        this.at++
        if (name.toLowerCase() === 'url') {
          // `url(` followed by a string is a function; anything else is one address token.
          let look = 0
          while (isSpace(this.code(look))) look++
          if (this.code(look) === 0x22 || this.code(look) === 0x27) return make('function', name)
          this.at += look
          return this.unquotedUrl(start)
        }
        return make('function', name)
      }
      return make('ident', name)
    }
    const char = String.fromCodePoint(this.text.codePointAt(this.at)!)
    this.at += char.length
    return make('delim')
  }
}

export function tokenize(css: string): Token[] {
  const reader = new Reader(preprocess(css))
  const out: Token[] = []
  for (let token = reader.next(); token; token = reader.next()) out.push(token)
  return out
}

/** Functions that fetch something by a bare string or an image notation, or run code. They are replaced by `none`. */
const DROPPED = new Set(['src', 'image', 'image-set', '-webkit-image-set', 'cross-fade', '-webkit-cross-fade', 'element', '-moz-element', 'paint', 'expression', '-webkit-canvas'])
/** Properties of an old engine that bind code to an element. */
const BINDINGS = new Set(['behavior', '-moz-binding'])

const isPlain = (token: Token) => token.kind === 'ws' || token.kind === 'comment'

/** The index of the token that closes the function or bracket opened at `open` (the last token when it never closes). */
function closing(tokens: Token[], open: number): number {
  let depth = 0
  for (let i = open; i < tokens.length; i++) {
    const token = tokens[i]
    if (token.kind === 'function' || (token.kind === 'punct' && (token.value === '(' || token.value === '[' || token.value === '{'))) depth++
    else if (token.kind === 'punct' && (token.value === ')' || token.value === ']' || token.value === '}')) {
      depth--
      if (depth <= 0) return i
    }
  }
  return tokens.length - 1
}

/** The index after an `@import` rule: after its semicolon, or where its block or its parent block ends. */
function afterStatement(tokens: Token[], from: number): number {
  let depth = 0
  for (let i = from; i < tokens.length; i++) {
    const token = tokens[i]
    if (token.kind === 'function' || (token.kind === 'punct' && '([{'.includes(token.value))) depth++
    else if (token.kind === 'punct' && ')]}'.includes(token.value)) {
      if (depth === 0) return i
      depth--
    } else if (token.kind === 'punct' && token.value === ';' && depth === 0) return i + 1
  }
  return tokens.length
}

/** What the `@import` names, as the decoded address of its first string or `url()`, or null. */
function importTarget(tokens: Token[], from: number, to: number): string | null {
  for (let i = from; i < to; i++) {
    const token = tokens[i]
    if (isPlain(token)) continue
    if (token.kind === 'string' || token.kind === 'url') return token.value
    if (token.kind === 'function' && token.value.toLowerCase() === 'url') {
      for (let j = i + 1; j < to; j++) if (tokens[j].kind === 'string') return tokens[j].value
    }
    return null
  }
  return null
}

/**
 * The style sheet with nothing in it that fetches from outside or runs. `own` says whether an address is one the page may
 * fetch for itself (`ownAddress` in sanitize.ts); `ownImport` whether an `@import` of this address may stay.
 */
export function cleanCssWith(css: string, own: (address: string) => boolean, ownImport: (address: string) => boolean): string {
  const tokens = tokenize(css)
  const out: string[] = []
  let i = 0
  while (i < tokens.length) {
    const token = tokens[i]
    if (token.kind === 'atkw' && token.value.toLowerCase() === 'import') {
      const end = afterStatement(tokens, i + 1)
      const target = importTarget(tokens, i + 1, end)
      // Only a part the reader made from the book. The rest of the rule (a media query) goes with it, written as it was read.
      if (target !== null && ownImport(target)) out.push(...tokens.slice(i, end).map((each) => each.raw))
      // What is taken out leaves a comment behind, so that what was on either side cannot grow together into something
      // new (`<` and `/style>` into the end of a style element, `u` and `rl(` into an address).
      else out.push('/**/')
      i = end
      continue
    }
    if (token.kind === 'url') {
      out.push(own(token.value) ? token.raw : 'url("")')
      i++
      continue
    }
    if (token.kind === 'badurl') {
      out.push('url("")')
      i++
      continue
    }
    if (token.kind === 'function') {
      const name = token.value.toLowerCase()
      if (name === 'url') {
        // The form with a string: `url("...")` and nothing else inside.
        const end = closing(tokens, i)
        const inner = tokens.slice(i + 1, end).filter((each) => !isPlain(each))
        const closed = tokens[end]?.kind === 'punct' && tokens[end].value === ')'
        if (closed && inner.length === 1 && inner[0].kind === 'string' && own(inner[0].value)) out.push(...tokens.slice(i, end + 1).map((each) => each.raw))
        else out.push('url("")')
        i = end + 1
        continue
      }
      if (DROPPED.has(name)) {
        i = closing(tokens, i) + 1
        out.push('none /**/')
        continue
      }
    }
    if (token.kind === 'ident' && BINDINGS.has(token.value.toLowerCase())) {
      let look = i + 1
      while (look < tokens.length && isPlain(tokens[look])) look++
      if (tokens[look]?.kind === 'punct' && tokens[look].value === ':') {
        out.push('x-removed')
        i++
        continue
      }
    }
    out.push(token.raw)
    i++
  }
  return out.join('')
}
