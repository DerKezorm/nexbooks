/**
 * The style sheet of the reading stack keeps the numbers of the design: the cards of the
 * recap, the towers of lying books, their drop, the board under each year, and the phone rule after the desktop rules. The
 * sheet is read from the disk (the test runner leaves style sheets empty); the real look is measured in the browser by the
 * pictures that stand beside the design's.
 */
type Fs = { readFileSync(path: string, encoding: 'utf-8'): string }
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs

const css = fs.readFileSync('src/stack.css', 'utf-8')

function rule(selector: string, from = 0): string {
  const start = css.indexOf(`${selector} {`, from)
  expect(start, selector).toBeGreaterThan(-1)
  return css.slice(start, css.indexOf('}', start))
}

describe('the recap', () => {
  it('has cards of 160 pixels or more with the corners and the padding of the design', () => {
    expect(rule('.recap')).toContain('minmax(160px, 1fr)')
    expect(rule('.recap')).toContain('gap: 12px')
    expect(rule('.recap')).toContain('margin: 8px 0 34px')
    expect(rule('.rc')).toContain('padding: 16px 18px')
    expect(rule('.rc')).toContain('border-radius: 16px')
    expect(rule('.rc b')).toContain('font: 600 30px/1.1 var(--serif)')
  })

  it('gives the thickest book two places and a cover of 46 pixels', () => {
    expect(rule('.rc.big')).toContain('grid-column: span 2')
    expect(rule('.rc.big .cover')).toContain('width: 46px')
    expect(rule('.rc.big b')).toContain('font-size: 20px')
  })
})

describe('the towers', () => {
  it('stand in a row that rolls sideways, each year a column of lying books on a board', () => {
    expect(rule('.stacks')).toContain('overflow-x: auto')
    expect(rule('.stacks')).toContain('gap: clamp(18px, 3vw, 46px)')
    expect(rule('.stacks')).toContain('min-height: 520px')
    expect(rule('.tower')).toContain('flex-direction: column-reverse')
    expect(rule('.year .plank')).toContain('width: 200px')
    expect(rule('.year .plank')).toContain('height: 14px')
    expect(rule('.year h3')).toContain('font: 600 24px var(--serif)')
  })

  it('lays a book as long and as thick as its variables say, a little out of line, and drops it', () => {
    const lying = rule('.lying')
    expect(lying).toContain('width: var(--w)')
    expect(lying).toContain('height: var(--t)')
    expect(lying).toContain('margin-left: var(--off, 0)')
    expect(lying).toContain('border-radius: 1.5px')
    expect(lying).toContain('font: 600 min(9.5px, calc(var(--t) - 3px)) var(--sans)')
    expect(lying).toContain('animation: drop 0.5s cubic-bezier(0.2, 0.9, 0.3, 1.2) backwards')
    expect(css).toMatch(/@keyframes drop \{\s*from \{\s*transform: translateY\(-40px\);\s*opacity: 0;/)
    expect(rule('.lying:hover,\n  .lying:focus-visible')).toContain('translateX(14px)')
  })

  it('draws a recording with a small ring and the corners of 4 pixels', () => {
    expect(rule('.lying.audio')).toContain('border-radius: 4px')
    expect(rule('.lying.audio::before')).toContain('width: 7px')
    // The page resets boxes to border-box; the ring of the model is 7 pixels inside its border (9 pixels outside).
    expect(rule('.lying.audio::before')).toContain('box-sizing: content-box')
    expect(rule('.lying.audio::before')).toContain('border: 1.5px solid currentColor')
  })
})

describe('the phone and less motion', () => {
  it('gives the thickest book one place on a phone, after the desktop rule', () => {
    const phone = css.indexOf('@media (max-width: 820px)')
    expect(phone).toBeGreaterThan(css.indexOf('.rc.big {'))
    expect(css.slice(phone, phone + 120)).toContain('grid-column: span 1')
  })

  it('lets the books lie at once for a person who asked for less motion', () => {
    const calm = css.indexOf('@media (prefers-reduced-motion: reduce)')
    expect(calm).toBeGreaterThan(-1)
    expect(css.slice(calm, calm + 200)).toContain('animation-duration: 0.01ms')
  })
})
