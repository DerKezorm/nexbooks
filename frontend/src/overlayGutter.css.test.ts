/**
 * The reader, the big player and the page of a book cover the whole window, so the strip `html { scrollbar-gutter: stable }` keeps free for a scrollbar
 * must not stay beside them. The style sheet is read from the disk (the test runner leaves style sheets empty); what the browser
 * makes of the rule is measured in the browser (e2e/reader.spec.ts and e2e/player.spec.ts).
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/index.css', 'utf-8')

describe('the strip for the scrollbar', () => {
  it('is kept on every page and given up while the reader, the big player or the page of a book is open', () => {
    expect(css).toMatch(/html\s*\{\s*scrollbar-gutter:\s*stable;?\s*\}/)
    const rule = css.match(/html:has\(([^)]*)\)\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    expect(rule![1]).toContain("[data-testid='reader']")
    expect(rule![1]).toContain("[data-testid='full-player']")
    expect(rule![1]).toContain('.detail')
    expect(rule![2]).toMatch(/scrollbar-gutter:\s*auto/)
    // After the rule that keeps it: the later rule of the same weight wins.
    expect(css.indexOf('html:has(')).toBeGreaterThan(css.indexOf('scrollbar-gutter: stable'))
  })
})
