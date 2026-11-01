/**
 * An author in a list: the photo (only one of a checked licence comes from
 * the server), else a circle with the initials in the colour of the genre most of the books are.
 */
import type { CSSProperties } from 'react'

import type { AuthorHit } from '../api/client'
import { inkFor } from '../lib/books'
import { circleColor, initials } from '../lib/listing'
import { genreKey } from '../lib/shelf'

export function AuthorAvatar({ hit, size = 's' }: { hit: Pick<AuthorHit, 'name' | 'photo_url' | 'genre'>; size?: 'xs' | 's' | 'm' }) {
  if (hit.photo_url) {
    return (
      <span className={`av ${size}`}>
        <img src={hit.photo_url} alt="" loading="lazy" decoding="async" draggable={false} />
      </span>
    )
  }
  const color = circleColor(genreKey(hit.genre))
  return (
    <span className={`av ${size} ini`} style={{ '--ac': color, color: inkFor(color) } as CSSProperties} aria-hidden>
      {initials(hit.name)}
    </span>
  )
}
