/** The faces of the player above every page: the small one at the bottom, and the big one while it is open. */
import { useLocation } from 'react-router-dom'

import { usePlayerLive } from '../../state/player'
import { FullPlayer } from './FullPlayer'
import { MiniPlayer } from './MiniPlayer'

export function PlayerChrome() {
  const { expanded } = usePlayerLive()
  const location = useLocation()
  // The reader of e-books fills the screen on its own; the big player does not open over it.
  const away = location.pathname.startsWith('/read/')
  return (
    <>
      <MiniPlayer />
      {expanded && !away && <FullPlayer />}
    </>
  )
}
