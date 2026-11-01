/**
 * A browser that translates the page (Chrome, Edge, an extension) puts elements of its own in the place of the text
 * nodes. React holds on to the old nodes and falls over with `insertBefore` or `removeChild` the next time it adds or
 * takes something there, and the whole page is gone. Here both give way: a node that was taken away already is not taken
 * again, and what should go before a node that was taken away goes before what stands in its place, else at the end.
 */
export function bearTranslatedPages(): void {
  if (typeof Node !== 'function') return
  const proto = Node.prototype
  if ((proto as { nexbooksBears?: boolean }).nexbooksBears) return
  ;(proto as { nexbooksBears?: boolean }).nexbooksBears = true

  const removeChild = proto.removeChild
  proto.removeChild = function <T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) return child
    return removeChild.call(this, child) as T
  }

  const insertBefore = proto.insertBefore
  proto.insertBefore = function <T extends Node>(this: Node, node: T, before: Node | null | undefined): T {
    if (before != null && before.parentNode !== this) {
      return insertBefore.call(this, node, childHolding(this, before)) as T
    }
    return insertBefore.call(this, node, before ?? null) as T
  }
}

/** The child of `parent` that holds `node` today; null when it is not in there any more. */
function childHolding(parent: Node, node: Node): Node | null {
  let current: Node | null = node
  while (current !== null && current.parentNode !== parent) current = current.parentNode
  return current
}
