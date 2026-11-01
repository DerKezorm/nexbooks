/** SHA-1 of a text (as UTF-8), without the Web Crypto API (which a page served over plain http does not have). EPUB scrambles
 * the first bytes of an embedded font with the SHA-1 of the book's identifier; nothing else here depends on it. */
export function sha1(text: string): Uint8Array {
  const data = new TextEncoder().encode(text)
  const bits = data.length * 8
  const padded = new Uint8Array(((data.length + 9 + 63) >> 6) << 6)
  padded.set(data)
  padded[data.length] = 0x80
  const view = new DataView(padded.buffer)
  view.setUint32(padded.length - 8, Math.floor(bits / 2 ** 32))
  view.setUint32(padded.length - 4, bits >>> 0)
  let h0 = 0x67452301
  let h1 = 0xefcdab89
  let h2 = 0x98badcfe
  let h3 = 0x10325476
  let h4 = 0xc3d2e1f0
  const w = new Uint32Array(80)
  const rotate = (value: number, count: number) => (value << count) | (value >>> (32 - count))
  for (let block = 0; block < padded.length; block += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(block + i * 4)
    for (let i = 16; i < 80; i++) w[i] = rotate(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1)
    let a = h0
    let b = h1
    let c = h2
    let d = h3
    let e = h4
    for (let i = 0; i < 80; i++) {
      const [f, k] =
        i < 20 ? [(b & c) | (~b & d), 0x5a827999]
        : i < 40 ? [b ^ c ^ d, 0x6ed9eba1]
        : i < 60 ? [(b & c) | (b & d) | (c & d), 0x8f1bbcdc]
        : [b ^ c ^ d, 0xca62c1d6]
      const next = (rotate(a, 5) + f + e + k + w[i]) >>> 0
      e = d
      d = c
      c = rotate(b, 30) >>> 0
      b = a
      a = next
    }
    h0 = (h0 + a) >>> 0
    h1 = (h1 + b) >>> 0
    h2 = (h2 + c) >>> 0
    h3 = (h3 + d) >>> 0
    h4 = (h4 + e) >>> 0
  }
  const out = new Uint8Array(20)
  const result = new DataView(out.buffer)
  ;[h0, h1, h2, h3, h4].forEach((value, i) => result.setUint32(i * 4, value))
  return out
}
