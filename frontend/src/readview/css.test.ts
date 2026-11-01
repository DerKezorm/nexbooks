import { cleanCss } from './sanitize'

const OUTSIDE = 'http://example.com/x.png'

/** Whether the sheet still names the outside where a browser would follow it (not inside a comment). */
function live(css: string): boolean {
  return /@import\s*["']?\s*https?:|url\(\s*["']?https?:|image-set\(|expression\(/i.test(css.replace(/\/\*[\s\S]*?\*\//g, ''))
}

describe('what a style sheet may fetch', () => {
  it('lets the book own addresses stay, and writes the rest of a style sheet exactly as it was', () => {
    const sheet = '@charset "utf-8"; @font-face { font-family: "Body"; src: url("blob:null/1") format("woff2"), url(data:font/woff2;base64,AAAA); }\n' +
      'body { color: #2a241d; margin: 0 1.5em 1e3px; background: url(blob:null/2) no-repeat; content: "\\201C quoted \\"text\\""; }\n' +
      '/* a note */ @media (min-width: 600px) { p.x > a:hover, h1 + h2 { width: calc(100% - 2em) !important; filter: url(#blur); } }\n' +
      '@import "blob:null/3"; @import url(blob:null/4) screen;'
    expect(cleanCss(sheet)).toBe(sheet)
  })

  it('takes out an address of the web, quoted or not, in any case, with white space around it', () => {
    for (const written of [`url(${OUTSIDE})`, `url("${OUTSIDE}")`, `url('${OUTSIDE}')`, `URL(  ${OUTSIDE}  )`, `Url(\n"${OUTSIDE}"\n)`, `url(//example.com/x.png)`, `url(/api/v1/auth/me)`]) {
      const css = cleanCss(`a{background:${written}}`)
      expect(css, written).toBe('a{background:url("")}')
    }
  })

  it('reads a name the way CSS reads it: with its escapes', () => {
    for (const name of ['u\\72l', '\\75rl', 'U\\52L', 'u\\000072l', '\\55 \\52 \\4c', 'ur\\6c']) {
      const unquoted = cleanCss(`a{background:${name}(${OUTSIDE})}`)
      const quoted = cleanCss(`a{background:${name}("${OUTSIDE}")}`)
      expect(live(unquoted), unquoted).toBe(false)
      expect(live(quoted), quoted).toBe(false)
      expect(unquoted).not.toContain('example.com')
      expect(quoted).not.toContain('example.com')
    }
  })

  it('reads an address the way CSS reads it: with its escapes', () => {
    expect(cleanCss('a{background:url("ht\\74 tp://example.com/x.png")}')).toBe('a{background:url("")}')
    expect(cleanCss('a{background:url(ht\\74p://example.com/x.png)}')).toBe('a{background:url("")}')
    expect(cleanCss('a{background:url("http\\3a //example.com/x.png")}')).toBe('a{background:url("")}')
    // `blob:` written with an escape is the book's own all the same.
    expect(cleanCss('a{background:url("\\62 lob:null/1")}')).toBe('a{background:url("\\62 lob:null/1")}')
  })

  it('takes out @import in every way it can be written', () => {
    for (const written of [
      `@import "${OUTSIDE}";`,
      `@import url(${OUTSIDE});`,
      `@IMPORT URL("${OUTSIDE}") screen;`,
      `@\\69mport "${OUTSIDE}";`,
      `@\\000069mport url(${OUTSIDE});`,
      `@i\\6d port "${OUTSIDE}";`,
      `@import/**/"${OUTSIDE}";`,
      `@import\n"${OUTSIDE}"\n;`,
      `@import u\\72l(${OUTSIDE});`,
    ]) {
      const css = cleanCss(`${written} p{color:red}`)
      expect(live(css), written).toBe(false)
      expect(css, written).not.toMatch(/import/i)
      expect(css, written).toContain('p{color:red}')
    }
  })

  it('does not take a comment for part of a name, and a comment in an address for a comment', () => {
    // `u/**/rl(` is the name `u`, a comment and the function `rl`: no address at all. The comment stays, so it stays that way.
    expect(cleanCss(`a{background:u/**/rl(${OUTSIDE})}`)).toBe(`a{background:u/**/rl(${OUTSIDE})}`)
    // Inside the parentheses of an unquoted address a comment is part of the address, which then is not the book's own.
    expect(cleanCss(`a{background:url(/**/${OUTSIDE})}`)).toBe('a{background:url("")}')
    expect(cleanCss(`a{background:url(${OUTSIDE}/**/)}`)).toBe('a{background:url("")}')
  })

  it('takes out the notations that fetch by a bare string or by their own name', () => {
    for (const written of [
      `image-set("${OUTSIDE}" 1x)`,
      `-webkit-image-set("${OUTSIDE}" 1x)`,
      `IMAGE-SET(url(${OUTSIDE}) 2x, "b.png" 1x)`,
      `image\\2d set("${OUTSIDE}" 1x)`,
      `image("${OUTSIDE}")`,
      `src("${OUTSIDE}")`,
      `cross-fade(url(${OUTSIDE}) 50%, url(b.png))`,
      `-webkit-cross-fade(url(${OUTSIDE}), url(b.png), 50%)`,
      `element(#a)`,
      `paint(worklet)`,
    ]) {
      const css = cleanCss(`a{background-image:${written};color:red}`)
      expect(css, written).toContain('background-image:none')
      expect(css, written).toContain(';color:red}')
      expect(css, written).not.toMatch(/example\.com|image-set|cross-fade|element\(|paint\(/i)
    }
  })

  it('takes out a code hook however it is written, and a broken address', () => {
    expect(cleanCss('a{width:expression(alert(1))}')).not.toMatch(/expression/)
    expect(cleanCss('a{width:\\65 xpression(alert(1))}')).not.toMatch(/xpression/)
    expect(cleanCss('a{behavior:url(x.htc);color:red}')).not.toMatch(/behavior/)
    expect(cleanCss('a{\\62 ehavior:url(x.htc)}')).not.toMatch(/ehavior/)
    expect(cleanCss('a{-MOZ-binding:url(x.xml#a)}')).not.toMatch(/binding/i)
    expect(cleanCss('a{background:url(a"b.png)}')).toBe('a{background:url("")}')
    expect(cleanCss('a{background:url(a b.png)}')).toBe('a{background:url("")}')
    expect(cleanCss('a{background:url(a\\\nb.png)}')).toBe('a{background:url("")}')
  })

  it('reads on after a broken string the way a browser does', () => {
    // A string with a line break in it ends at the break; the address after it is still an address.
    const css = cleanCss(`a{content:"broken\nb{background:url(${OUTSIDE})}`)
    expect(css).not.toContain('example.com')
  })

  it('leaves nothing behind that grows together into something new', () => {
    // Taking out the rule between `<` and `/style>` must not leave the end of a style element.
    const css = cleanCss(`<@import url(${OUTSIDE});/style><img src=x onerror=1>`)
    expect(css).not.toMatch(/<\/style/i)
    // Nor an address from the pieces on both sides of what was taken out.
    expect(cleanCss(`u@import "${OUTSIDE}";rl(${OUTSIDE})`)).not.toMatch(/url\(/i)
    expect(cleanCss('p{color:red}</style><script>x</script>')).not.toMatch(/<\/style/i)
  })

  it('does the same for a style sheet of 200 kB without slowing down', () => {
    const big = 'p.a > b { color: red; background: url("blob:null/1"); }\n'.repeat(4000)
    const started = performance.now()
    expect(cleanCss(big)).toBe(big)
    expect(performance.now() - started).toBeLessThan(3000)
  })
})
