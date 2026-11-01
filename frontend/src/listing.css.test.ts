/**
 * The style sheet of the list keeps the chips of the editions whole: they break into a second line (a row is 60 pixels, two
 * lines of chips fit) instead of being cut off at the edge of their column, and one that is too long for the column ends in an
 * ellipsis. The sheet is read from the disk (the test runner leaves style sheets empty).
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/listing.css', 'utf-8')

function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`)
  expect(start, selector).toBeGreaterThan(-1)
  return css.slice(start, css.indexOf('}', start))
}

describe('the chips of the editions in the list', () => {
  it('break into lines and are not hidden by the box around them', () => {
    expect(rule('.list-table .chips')).toContain('flex-wrap: wrap')
    expect(rule('.list-table .chips')).not.toContain('overflow')
    expect(rule('.list-table .chips')).not.toContain('nowrap')
  })

  it('end in an ellipsis when a single chip is wider than the column', () => {
    expect(rule('.list-table .chip')).toContain('max-width: 100%')
    expect(rule('.list-table .chip-text')).toContain('text-overflow: ellipsis')
  })
})
