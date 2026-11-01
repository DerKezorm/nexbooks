/**
 * The wishes of "Gesucht" as a table: how the columns are put in order. Pure functions of what the server says
 * (`WishOut`), so that a test can hold them against the rules of the list.
 */
import type { WishOut } from '../api/client'

/** The columns that can be put in order by. */
export type WishSort = 'title' | 'kind' | 'state' | 'last' | 'next' | 'by'

/** The order of the states: what is under way first, what rests last. */
const STATE_ORDER = ['searching', 'not_found', 'capped', 'waiting', 'upgrade', 'idle']

/** The direction a column starts in: what was searched last is on top, the rest from the first. */
export function firstDirection(sort: WishSort): 'asc' | 'desc' {
  return sort === 'last' ? 'desc' : 'asc'
}

function keyOf(wish: WishOut, sort: WishSort): (string | number | null)[] {
  switch (sort) {
    case 'title':
      return [wish.book.title]
    case 'kind':
      return [wish.kind, wish.language, wish.book.title]
    case 'state': {
      const rank = STATE_ORDER.indexOf(wish.state)
      return [rank < 0 ? STATE_ORDER.length : rank, wish.book.title]
    }
    case 'last':
      return [Date.parse(wish.searched_at ?? '') || null, wish.book.title]
    case 'next':
      return [Date.parse(wish.next_try_at ?? '') || null, wish.book.title]
    case 'by':
      return [wish.via || wish.by || null, wish.book.title]
  }
}

/** The wishes in the order of a column. A wish with nothing in the column stands last in either direction. */
export function sortWishes(wishes: WishOut[], sort: WishSort | null, dir: 'asc' | 'desc', language: string): WishOut[] {
  if (sort === null) return wishes
  const collator = new Intl.Collator(language, { numeric: true, sensitivity: 'base' })
  const sign = dir === 'asc' ? 1 : -1
  const keyed = wishes.map((wish, index) => ({ wish, index, key: keyOf(wish, sort) }))
  keyed.sort((a, b) => {
    for (let i = 0; i < Math.max(a.key.length, b.key.length); i++) {
      const x = a.key[i] ?? null
      const y = b.key[i] ?? null
      if (x === y) continue
      if (x === null) return 1
      if (y === null) return -1
      const order = typeof x === 'number' && typeof y === 'number' ? x - y : collator.compare(String(x), String(y))
      if (order !== 0) return order * sign
    }
    return a.index - b.index
  })
  return keyed.map((item) => item.wish)
}
