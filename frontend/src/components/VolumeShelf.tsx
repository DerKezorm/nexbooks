/**
 * The small shelf of a series with the volumes the person lacks: the volumes
 * they have stand as spines, the ones they do not as dashed ghosts (FEHLT, or BALD for one that is yet to come), in the order
 * of the series. Pressing a spine opens that book; pressing a ghost opens the page of the book that is not on a shelf.
 */
import type { DiscoverCard, Volume, WorkBrief } from '../api/client'
import { GhostSpine } from './GhostSpine'
import { Spine } from './Spine'

export function VolumeShelf({
  volumes,
  current,
  label,
  onOpenWork,
  onOpenBook,
}: {
  volumes: Volume[]
  /** The volume whose page is open: a work of the shelf, or the token of a book that is not on it. */
  current: { work?: number; token?: string }
  label: string
  onOpenWork: (work: WorkBrief) => void
  onOpenBook: (card: DiscoverCard) => void
}) {
  return (
    <div className="mini-shelf" role="group" aria-label={label} data-testid="mini-shelf">
      {volumes.map((volume) =>
        volume.work ? (
          <Spine key={`w${volume.work.id}`} work={volume.work} className={volume.work.id === current.work ? 'current' : ''} onOpen={onOpenWork} />
        ) : volume.book ? (
          <GhostSpine key={`b${volume.book.token}`} book={volume.book} className={volume.book.token === current.token ? 'current' : ''} onOpen={onOpenBook} />
        ) : null,
      )}
    </div>
  )
}
