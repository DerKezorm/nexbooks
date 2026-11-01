/** A page the browser translated keeps working: what Chrome's translator does to the text nodes, then React at work. */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { bearTranslatedPages } from './translatedPage'

/** What Chrome's translator does: every text node makes way for <font><font>…</font></font>. */
function translateLikeChrome(root: Node) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const texts: Text[] = []
  while (walker.nextNode()) texts.push(walker.currentNode as Text)
  for (const text of texts) {
    const outer = document.createElement('font')
    const inner = document.createElement('font')
    inner.textContent = text.data
    outer.appendChild(inner)
    text.replaceWith(outer)
  }
}

function Line({ busy, note }: { busy: boolean; note: boolean }) {
  return (
    <p data-testid="line">
      {busy && <b>busy</b>}
      Saved
      {note && ' just now'}
    </p>
  )
}

let box: HTMLDivElement
let root: Root

beforeAll(() => {
  bearTranslatedPages()
})
beforeEach(() => {
  box = document.createElement('div')
  document.body.appendChild(box)
  root = createRoot(box)
})
afterEach(() => {
  act(() => root.unmount())
  box.remove()
})

const show = (busy: boolean, note: boolean) => act(() => root.render(<Line busy={busy} note={note} />))

it('keeps working when something comes before a text the browser took away', () => {
  show(false, false)
  translateLikeChrome(box)
  show(true, false)
  // Where the text belonged nobody knows any more: what is new stands at the end rather than not at all.
  expect(box.querySelector('b')).not.toBeNull()
})

it('keeps working when a text the browser took away goes', () => {
  show(false, true)
  translateLikeChrome(box)
  show(false, false)
  expect(box.querySelector('p')).not.toBeNull()
})

it('puts what is meant to go before a wrapped node before its wrapper', () => {
  const parent = document.createElement('div')
  const text = document.createTextNode('Saved')
  parent.appendChild(text)
  const wrapper = document.createElement('font')
  text.replaceWith(wrapper)
  wrapper.appendChild(text)
  const added = document.createElement('b')
  parent.insertBefore(added, text)
  expect(parent.firstChild).toBe(added)
  expect(parent.lastChild).toBe(wrapper)
})

it('takes undefined for nothing to put it before, as the browser does', () => {
  const parent = document.createElement('div')
  const first = document.createElement('i')
  const second = document.createElement('b')
  parent.appendChild(first)
  expect(() => parent.insertBefore(second, undefined as unknown as Node | null)).not.toThrow()
  expect([...parent.children]).toEqual([first, second])
})
