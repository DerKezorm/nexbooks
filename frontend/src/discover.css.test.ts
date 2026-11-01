/**
 * The style sheet of Discover keeps the numbers of the design: the sizes of the grid and
 * of the numbers on the covers (the first three in the accent colour), the dashed frame of a volume the person lacks, the tabs
 * that roll sideways on a phone, and the phone rules after the desktop rules (same weight: the later wins). The style sheet is
 * read from the disk (the test runner leaves style sheets empty); the real look is measured in the browser, by the pictures.
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/discover.css', 'utf-8')
const phone = css.indexOf('@media (max-width: 820px)')

/** The text of the first rule with exactly this selector. */
function rule(selector: string, from = 0): string {
  const start = css.indexOf(`${selector} {`, from)
  expect(start, selector).toBeGreaterThan(-1)
  return css.slice(start, css.indexOf('}', start))
}

describe('the grid of a list', () => {
  it('has the sizes of the design', () => {
    expect(rule('.dgrid')).toContain('grid-template-columns: repeat(auto-fill, minmax(150px, 1fr))')
    expect(rule('.dgrid')).toContain('gap: 34px 24px')
    expect(rule('.dcard b')).toContain('font: 600 14.5px/1.25 var(--serif)')
    expect(rule('.dcard .badge')).toContain('height: 24px')
    expect(rule('.dcard .badge')).toContain('border-radius: 7px')
  })

  it('numbers the covers with a ring, the first three in the accent colour and a little larger', () => {
    expect(rule('.dgrid .rk')).toContain('min-width: 30px')
    expect(rule('.dgrid .rk')).toContain('height: 30px')
    expect(rule('.dgrid .rk')).toContain('background: var(--text)')
    const best = rule('.dgrid .dcard:nth-child(-n + 3) .rk')
    expect(best).toContain('background: var(--accent)')
    expect(best).toContain('min-width: 36px')
    expect(best).toContain('height: 36px')
    expect(best).toContain('font-size: 17px')
  })

  it('draws a volume the person lacks dimmed and dashed, framed in the accent colour when it is yet to come', () => {
    expect(rule('.dcard.missing .cover')).toContain('filter: saturate(0.5) brightness(0.82)')
    expect(rule('.dcard.missing .cv::after')).toContain('border: 2px dashed')
    expect(rule('.dcard.missing.soon .cv::after')).toContain('border-color: var(--accent)')
    expect(rule('.spine.ghost')).toContain('border: 1.5px dashed')
    expect(rule('.spine.ghost.soon')).toContain('border-color: color-mix(in oklab, var(--accent) 70%, transparent)')
  })
})

describe('the tabs and the filters', () => {
  it('have the tabs of the design: an underline in the accent colour for the open one', () => {
    // The tabs are buttons and, on the shelf page, links: one look for both.
    expect(rule('.dlists button,\n  .dlists a')).toContain('font: 600 15px var(--sans)')
    expect(rule('.dlists button.on,\n  .dlists a.on')).toContain('border-bottom-color: var(--accent)')
    expect(rule('.dfilters')).toContain('gap: 10px 14px')
  })
})

describe('the phone', () => {
  it('has its rules after the desktop rules', () => {
    expect(phone).toBeGreaterThan(css.indexOf('.dgrid {'))
    expect(phone).toBeGreaterThan(css.indexOf('.row {'))
    expect(phone).toBeGreaterThan(css.indexOf('.dlists {'))
  })

  it('rolls the tabs and the genres sideways in one line, and makes the covers smaller', () => {
    const mobile = css.slice(phone)
    expect(mobile).toMatch(/\.dlists \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto;/)
    expect(mobile).toMatch(/\.dgenres \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto;/)
    expect(mobile).toContain('minmax(108px, 1fr)')
    expect(mobile).toContain('gap: 26px 14px')
    expect(mobile).toMatch(/\.row \{[^}]*grid-auto-columns: 118px;/)
    expect(mobile).toMatch(/#market button \{[^}]*font-size: 12\.5px;/)
  })
})

describe('the rest', () => {
  it('says its sources in a pill and puts the count on the tab in the accent colour', () => {
    expect(rule('.src')).toContain('border-radius: 999px')
    expect(rule('.wishcount')).toContain('background: var(--accent)')
  })
})
