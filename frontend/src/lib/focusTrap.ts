/**
 * Keeps the Tab key inside a layer that lies over the page (the page of a book): from the last thing on it the focus goes
 * to the first, and with Shift from the first to the last, so that it never walks out onto the page behind, which nobody
 * can see.
 */
const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]'

/** What can take the focus inside the layer, in the order of the page. */
export function focusables(layer: HTMLElement): HTMLElement[] {
  return [...layer.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((element) => {
    if (element.tabIndex < 0 || (element as HTMLButtonElement).disabled || element.closest('[hidden], [inert]')) return false
    const style = getComputedStyle(element)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

/** Handle a key press on the layer: Tab at an end of it turns around. Returns whether it did. */
export function trapTab(event: KeyboardEvent, layer: HTMLElement): boolean {
  if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return false
  const items = focusables(layer)
  if (items.length === 0) {
    event.preventDefault()
    layer.focus({ preventScroll: true })
    return true
  }
  const first = items[0]!
  const last = items[items.length - 1]!
  const active = document.activeElement
  const outside = !(active instanceof Node) || !layer.contains(active)
  if (event.shiftKey && (active === first || active === layer || outside)) {
    event.preventDefault()
    last.focus({ preventScroll: true })
    return true
  }
  if (!event.shiftKey && (active === last || outside)) {
    event.preventDefault()
    first.focus({ preventScroll: true })
    return true
  }
  return false
}
