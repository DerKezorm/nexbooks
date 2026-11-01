/**
 * The style sheet of Browse keeps the numbers of the design: the covers' width and the
 * distance between them, the board that runs under the covers, the lift of a cover that is held, the width of the back of a book
 * and the sheet on a phone, and the phone rules after the desktop rules (same weight: the later wins). The style sheet is read
 * from the disk (the test runner leaves style sheets empty); the real look is measured in the browser, by the pictures.
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/browse.css', 'utf-8')
const phone = css.indexOf('@media (max-width: 820px)')

/** The text of the first rule with exactly this selector (a list of selectors is one selector here). */
function rule(selector: string, from = 0): string {
  const start = css.indexOf(`${selector} {`, from)
  expect(start, selector).toBeGreaterThan(-1)
  return css.slice(start, css.indexOf('}', start))
}

describe('the covers on a board', () => {
  it('stand 148 pixels wide with 22 between them, and the board is 12 pixels thick under each card', () => {
    expect(rule('.brow,\n  .bgrid')).toContain('--cw: 148px')
    expect(rule('.brow,\n  .bgrid')).toContain('--gap: 22px')
    expect(rule('.bcv::after')).toContain('height: 12px')
    expect(rule('.bcv::after')).toContain('linear-gradient(180deg, var(--plank-1), var(--plank-2))')
    expect(rule('.bcv')).toContain('height: 150cqw')
  })

  it('lift a cover that is held or looked at with the keys, and take the strip sideways', () => {
    const lift = rule('.bcard:hover .bcv .cover,\n  .bcard:focus-visible .bcv .cover,\n  .bcard.held .bcv .cover')
    expect(lift).toContain('translateY(-10px) rotate(-2deg) scale(1.03)')
    expect(rule('.brow')).toContain('overflow-x: auto')
    expect(rule('.brow')).toContain('scroll-snap-type: x proximity')
  })

  it('name the cards in the serif of the design and put the prize in the accent colour', () => {
    expect(rule('.bcard b')).toContain('font: 600 14.5px/1.25 var(--serif)')
    expect(rule('.bcard .bmeta')).toContain('color: var(--accent)')
    expect(rule('.tribbon')).toContain('width: 23px')
  })
})

describe('the wall', () => {
  it('has tiles of 196 pixels at least, a fan of three covers on a board and a lift on hover', () => {
    expect(rule('.tw-grid')).toContain('repeat(auto-fill, minmax(196px, 1fr))')
    expect(rule('.sh-fan')).toContain('height: 92px')
    expect(rule('.sh-c.c0')).toContain('rotate(-7deg)')
    expect(rule('.shtile:hover,\n  .shtile:focus-visible')).toContain('translateY(-3px)')
  })
})

describe('the back of a book', () => {
  it('is 340 pixels wide, above the page and under the sheet, in the colour of the book', () => {
    const hand = rule('.hand')
    expect(hand).toContain('width: 340px')
    expect(hand).toContain('z-index: 75')
    expect(hand).toContain('--page: color-mix(in oklab, var(--bc) 16%, var(--surface))')
    expect(rule('.hand-back')).toContain('z-index: 74')
  })

  it('has a darker page by night, both for the chosen theme and for the one of the system', () => {
    expect(css).toContain(":root[data-theme='dark'] .hand")
    expect(css).toContain(":root:not([data-theme='light']) .hand")
    expect(rule(":root[data-theme='dark'] .hand")).toContain('#15110e')
  })

  it('is a sheet from below on a phone: the whole width, at the bottom, 82 percent of the height at most', () => {
    const sheet = rule('.hand.sheet')
    expect(sheet).toContain('left: 0 !important')
    expect(sheet).toContain('bottom: 0')
    expect(sheet).toContain('max-height: 82dvh')
    expect(sheet).toContain('border-radius: 18px 18px 0 0')
    expect(rule('.hand.sheet .h-in')).toContain('env(safe-area-inset-bottom)')
  })
})

describe('the phone', () => {
  it('has its rules after the desktop rules, and narrower covers and tiles that roll sideways', () => {
    expect(phone).toBeGreaterThan(css.indexOf('.brow {'))
    expect(phone).toBeGreaterThan(css.indexOf('.tw-grid {'))
    expect(phone).toBeGreaterThan(css.indexOf('.subtabs {'))
    const narrow = css.slice(phone)
    expect(narrow).toContain('--cw: 112px')
    expect(narrow).toContain('--gap: 14px')
    expect(narrow).toContain('grid-auto-columns: 168px')
    expect(narrow).toContain('overflow-x: auto')
  })
})
