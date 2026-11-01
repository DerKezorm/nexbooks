/** The Tab key on a layer over the page: it turns around at the ends and never reaches what lies behind. */
import { focusables, trapTab } from './focusTrap'

function press(layer: HTMLElement, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true })
  const handled = trapTab(event, layer)
  return { handled, prevented: event.defaultPrevented }
}

describe('the Tab key on a layer', () => {
  let layer: HTMLElement
  let behind: HTMLButtonElement
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="behind">behind</button>
      <section id="layer" tabindex="-1">
        <button id="back">back</button>
        <a id="link" href="/x">link</a>
        <button id="off" disabled>off</button>
        <button id="skipped" tabindex="-1">skipped</button>
        <div hidden><button id="hidden">hidden</button></div>
        <button id="last">last</button>
      </section>`
    layer = document.getElementById('layer')!
    behind = document.getElementById('behind') as HTMLButtonElement
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('lists what can take the focus, in order, and leaves out what cannot', () => {
    expect(focusables(layer).map((element) => element.id)).toEqual(['back', 'link', 'last'])
  })

  it('goes from the last to the first with Tab, and from the first to the last with Shift+Tab', () => {
    document.getElementById('last')!.focus()
    expect(press(layer)).toEqual({ handled: true, prevented: true })
    expect(document.activeElement!.id).toBe('back')
    expect(press(layer, true)).toEqual({ handled: true, prevented: true })
    expect(document.activeElement!.id).toBe('last')
  })

  it('leaves the keys in the middle to the browser', () => {
    document.getElementById('link')!.focus()
    expect(press(layer)).toEqual({ handled: false, prevented: false })
    expect(press(layer, true)).toEqual({ handled: false, prevented: false })
    expect(document.activeElement!.id).toBe('link')
  })

  it('takes the focus back in when it stands outside, and from the layer itself with Shift+Tab', () => {
    behind.focus()
    press(layer)
    expect(document.activeElement!.id).toBe('back')
    behind.focus()
    press(layer, true)
    expect(document.activeElement!.id).toBe('last')
    layer.focus()
    press(layer, true)
    expect(document.activeElement!.id).toBe('last')
  })

  it('ignores other keys and Tab with a shortcut key', () => {
    document.getElementById('last')!.focus()
    expect(trapTab(new KeyboardEvent('keydown', { key: 'Enter' }), layer)).toBe(false)
    expect(trapTab(new KeyboardEvent('keydown', { key: 'Tab', ctrlKey: true }), layer)).toBe(false)
    expect(document.activeElement!.id).toBe('last')
  })

  it('keeps the focus on the layer when there is nothing on it to take it', () => {
    layer.innerHTML = ''
    behind.focus()
    expect(press(layer)).toEqual({ handled: true, prevented: true })
    expect(document.activeElement).toBe(layer)
  })
})
