/**
 * Which entry of the ZIP an address in a book means. The package of an EPUB writes its addresses as a URL (`My%20Part%2C%20One.htm`),
 * the library of foliate-js decodes them only as far as `decodeURI` does (not `%2C`, `%23`, `%3B` and the other reserved
 * characters), and the ZIP holds the plain name (`My Part, One.htm`). Some files are written the other way round and carry a `%`
 * in the name of the entry itself, or a name in another Unicode form or letter case than the package. So an address is looked
 * up as it is first, and only then in the ways above; a name that two entries fit is none (the reader never guesses between
 * two parts).
 */

/** The name as the package may have written it, decoded the way a URL is: null when it is no valid escape or changes nothing. */
function decoded(name: string): string | null {
  try {
    const plain = decodeURIComponent(name)
    return plain === name ? null : plain
  } catch {
    return null
  }
}

/** What two names have in common when they mean the same entry: slashes, Unicode form and letter case. */
function looseKey(name: string): string {
  return name.replace(/\\/g, '/').normalize('NFC').toLowerCase()
}

/** Returns a function that gives the exact name of the entry an address means, or null. */
export function entryFinder(names: Iterable<string>): (address: string) => string | null {
  const all = Array.from(names)
  const exact = new Set(all)
  const loose = new Map<string, string[]>()
  /** By the last part of the name, so that a missing folder is found without looking at every entry for every address. */
  const byTail = new Map<string, string[]>()
  const add = (index: Map<string, string[]>, key: string, name: string) => {
    const same = index.get(key)
    if (same) same.push(name)
    else index.set(key, [name])
  }
  for (const name of all) {
    const key = looseKey(name)
    add(loose, key, name)
    add(byTail, key.slice(key.lastIndexOf('/') + 1), name)
  }
  const memo = new Map<string, string | null>()
  const search = (address: string): string | null => {
    const candidates = [address]
    const plain = decoded(address)
    if (plain !== null) candidates.push(plain)
    for (const candidate of candidates) if (exact.has(candidate)) return candidate
    for (const candidate of candidates) {
      const same = loose.get(looseKey(candidate))
      if (same?.length === 1) return same[0]
    }
    // An address whose folder was lost on the way (a name with a bare `%` in it cannot be resolved against the package's
    // folder): the one entry that ends in it.
    for (const candidate of candidates) {
      if (!candidate.includes('/')) continue
      const key = looseKey(candidate)
      const bucket = byTail.get(key.slice(key.lastIndexOf('/') + 1)) ?? []
      // A name shared by hundreds of entries is no name to find a part by (and a book made to be slow is not helped).
      if (bucket.length > 50) continue
      const ends = bucket.filter((name) => looseKey(name).endsWith('/' + key))
      if (ends.length === 1) return ends[0]
    }
    return null
  }
  return (address: string) => {
    if (memo.has(address)) return memo.get(address) ?? null
    const found = search(address)
    memo.set(address, found)
    return found
  }
}

/** The address of a part as a URL path, written in a package that lies in `folder` (no slash at the end, or empty): `..` and
 * `.` are resolved, a fragment and a query are dropped. What comes back may still be percent-encoded; `entryFinder` decodes. */
export function addressIn(folder: string, href: string): string {
  const clean = href.split('#')[0].split('?')[0]
  const base = 'https://invalid.invalid/' + (folder ? folder.replace(/\/?$/, '/') : '')
  return new URL(clean, base).pathname.slice(1)
}
