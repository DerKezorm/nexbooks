/**
 * The last wall, tried before a book is opened. Everything else that keeps a book from running a script (the cleaning of
 * its pages, the policy each page carries) works inside the page of the book; the two walls around it are the browser's: the
 * frame of a page has no `allow-scripts` (not in Safari, where it cannot be left off), and the page inherits the policy of the
 * frame it was made in (`script-src 'self'`). Both are settings, and a setting can fail without a sound: a browser that does not
 * honour one, an engine that changes how it inherits a policy, a server that is told to send another header.
 *
 * So the reader makes a page the way it makes the pages of a book (a `blob:` address, in a frame with the same `sandbox`), puts a
 * script of its own in it, and looks whether the script ran. If it did, the walls do not hold in this browser and the reader
 * opens no book here (and says so in plain words).
 */

/** What a page of a book would be if every cleaning had failed: a script in it that tells whoever made the frame. */
const PROBE_PAGE =
  '<!DOCTYPE html><html><head><meta charset="utf-8"><title>probe</title></head><body><script>try{parent.postMessage({nexbooksProbe:1},"*")}catch(e){}</script></body></html>'

/** How long a probe page is given to load. A page that has not loaded has not run a script. */
const GIVE_UP_MS = 2500
/** After the page has loaded: the scripts of a page run while it is parsed, so what they said is on its way already. */
const SETTLE_MS = 80

/**
 * Whether a script in a page that is made like a page of a book runs. `sandbox` is the attribute the frames of the pages get.
 */
export function scriptRuns(sandbox: string, doc: Document = document, win: Window = window): Promise<boolean> {
  return new Promise((resolve) => {
    const frame = doc.createElement('iframe')
    frame.setAttribute('sandbox', sandbox)
    frame.setAttribute('aria-hidden', 'true')
    frame.tabIndex = -1
    frame.style.cssText = 'position:absolute;width:1px;height:1px;border:0;opacity:0;pointer-events:none'
    const address = URL.createObjectURL(new Blob([PROBE_PAGE], { type: 'text/html' }))
    let finished = false
    const finish = (ran: boolean) => {
      if (finished) return
      finished = true
      win.removeEventListener('message', onMessage)
      frame.remove()
      URL.revokeObjectURL(address)
      resolve(ran)
    }
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { nexbooksProbe?: number } | null
      if (event.source === frame.contentWindow && data !== null && typeof data === 'object' && data.nexbooksProbe === 1) finish(true)
    }
    win.addEventListener('message', onMessage)
    frame.addEventListener('load', () => setTimeout(() => finish(false), SETTLE_MS))
    setTimeout(() => finish(false), GIVE_UP_MS)
    frame.src = address
    doc.body.append(frame)
  })
}
