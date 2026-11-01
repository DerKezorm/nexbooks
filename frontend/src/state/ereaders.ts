/**
 * What the page of a book needs to know about e-readers: whether the operator allows them, whether the server can mail
 * and whether the person has set the address of a Kindle. One question for the whole visit, asked when the first e-book
 * shows its button; the account page forgets the answer when the person changes something there.
 */
import { useEffect, useState } from 'react'

import { ereadersApi, type EreadersState } from '../api/client'

let asked: Promise<EreadersState | null> | null = null

/** A state that holds what the page needs (a stand-in server that knows nothing answers an empty object). */
function usable(found: EreadersState | null): EreadersState | null {
  return found && typeof found.allowed === 'boolean' && found.kindle ? found : null
}

export function loadEreaders(): Promise<EreadersState | null> {
  if (asked === null) {
    asked = ereadersApi.state().then(usable, () => null)
    // An answer that could not be read is asked for again next time.
    void asked.then((found) => {
      if (found === null) asked = null
    })
  }
  return asked
}

/** Forget the answer: the next page of a book asks again. */
export function forgetEreaders(): void {
  asked = null
}

export function useEreaders(): EreadersState | null {
  const [state, setState] = useState<EreadersState | null>(null)
  useEffect(() => {
    let current = true
    void loadEreaders().then((found) => {
      if (current) setState(found)
    })
    return () => {
      current = false
    }
  }, [])
  return state
}
