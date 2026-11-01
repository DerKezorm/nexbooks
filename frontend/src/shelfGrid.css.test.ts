/**
 * The grid of covers on a phone is the design's: below 820 px the covers are at least 108 px wide and the rows are
 * 22 px / 14 px apart (the desktop keeps 150 px and 30 px / 22 px). The style sheet is read from the disk (the test runner
 * leaves style sheets empty), and the phone rule has to come after the desktop rule: it has the same weight, so the
 * later one wins. The real look is measured in the browser, by screenshots.
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/index.css', 'utf-8')

describe('the grid of covers', () => {
  it('keeps the desktop sizes and has the design phone rule after them', () => {
    const base = css.indexOf('.shelf-grid {')
    expect(base).toBeGreaterThan(-1)
    expect(css.slice(base, base + 200)).toContain('minmax(150px, 1fr)')
    expect(css.slice(base, base + 200)).toContain('gap: 30px 22px')
    const phone = css.indexOf('@media (max-width: 820px)', base)
    expect(phone).toBeGreaterThan(base)
    const rule = css.slice(phone, css.indexOf('.book {', phone))
    expect(rule).toContain('.shelf-grid')
    expect(rule).toContain('minmax(108px, 1fr)')
    expect(rule).toContain('gap: 22px 14px')
  })
})
