/**
 * The pieces of a hit of the catalogues (one book: its e-books, recordings and editions in one), shared by the quick search of
 * the head and the search of Discover: the cover, the line under the title, what the hit is (a box, a study guide, a play, a
 * part, a comic), whether it stands on the shelf, the languages it exists in when none of them is fetched, the three ways to
 * acquire it ("E-Book", "Hörbuch", "Beides") and the list of its editions.
 */
import { Download } from 'lucide-react'
import { useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'

import type { CatalogHit, CatalogHitEdition, ReleaseKind } from '../api/client'
import { languageName } from '../lib/books'
import { KINDS, useAcquireHit } from '../lib/catalogSearch'
import { useProcure } from '../state/procure'

export function HitCover({ hit, big = false }: { hit: CatalogHit; big?: boolean }) {
  return (
    <span className={`cover ${big ? 'hit-cover' : 'mini'}`} style={{ '--c': 'color-mix(in oklab, var(--accent) 22%, var(--surface))' } as CSSProperties}>
      {hit.cover_url && <img src={hit.cover_url} alt="" loading="lazy" />}
    </span>
  )
}

/** The chips of a hit: what it is, and whether the person has it (``not on the shelf`` for a person who may not acquire it:
 * the buttons to acquire say it for the others). */
export function HitChips({ hit }: { hit: CatalogHit }) {
  const { t, i18n } = useTranslation()
  const procure = useProcure()
  return (
    <>
      {hit.label !== 'work' && (
        <span className="chip hit-label" data-testid="hit-label" data-label={hit.label}>
          {t(`search.hit.label.${hit.label}`)}
        </span>
      )}
      {hit.owned_work_id !== null ? (
        <span className="chip">{t('discover.badge.owned')}</span>
      ) : (
        !procure.allowed && <span className="chip">{t('search.notInLibrary')}</span>
      )}
      {!hit.allowed && hit.languages.length > 0 && (
        <span className="chip hit-elsewhere" data-testid="hit-elsewhere">
          {t('search.hit.existsIn', { languages: hit.languages.map((code) => languageName(code, i18n.language)).join(', ') })}
        </span>
      )}
    </>
  )
}

/**
 * "E-Book", "Hörbuch", "Beides": opens the dialog to acquire the book with that choice. Only for a person who may acquire, and
 * not for a book on the shelf or one that is wished already (that one says how far it is), and only for a book: a box, a
 * play, a part, a study guide or a comic is named for what it is, a book named like its series whose volume nobody knows says
 * so (the person picks the volume). `cells`: the buttons stand in a row of the quick search as cells of its grid (the arrows
 * reach them, `active` is the one chosen, `cellId` names each).
 */
export function AcquireKinds({ hit, ticket, cells, onOpen }: { hit: CatalogHit; ticket?: string | null; cells?: { cellId: (index: number) => string; active: number }; onOpen?: () => void }) {
  const { t } = useTranslation()
  const procure = useProcure()
  const acquire = useAcquireHit()
  const [gone, setGone] = useState(false)
  if (!procure.allowed || hit.owned_work_id !== null) return null
  if (!hit.procurable) {
    return hit.why_not === 'volume_unknown' ? (
      <span className="chip" data-testid="hit-why-not">
        {t('search.hit.whyNot.volume_unknown')}
      </span>
    ) : null
  }
  const states = procure.kindsOf({ token: hit.token })
  if (states.length > 0) {
    return (
      <span className="chip stt" data-testid="catalog-asked">
        {states.some((state) => state.download) ? t('acquire.shelf.markRunning') : t('acquire.shelf.markSearching')}
      </span>
    )
  }
  if (gone) {
    return (
      <span className="chip" data-testid="hit-gone">
        {t('search.hit.gone')}
      </span>
    )
  }
  const go = (kinds: ReleaseKind[]) => (event: MouseEvent | KeyboardEvent) => {
    event.stopPropagation()
    event.preventDefault()
    onOpen?.()
    acquire(hit, ticket, kinds).catch(() => setGone(true))
  }
  if (cells) {
    return (
      <>
        {KINDS.map(({ key, kinds }, index) => (
          <span key={key} id={cells.cellId(index + 1)} role="gridcell" aria-selected={cells.active === index + 1} className={`res-act acq ${cells.active === index + 1 ? 'act' : ''}`} data-testid={`hit-acquire-${key}`} onClick={go(kinds)}>
            {key === 'ebook' && <Download aria-hidden />}
            {t(`search.hit.get.${key}`)}
          </span>
        ))}
      </>
    )
  }
  return (
    <span className="hit-acq" role="group" aria-label={t('search.hit.acquire', { title: hit.title })}>
      {KINDS.map(({ key, kinds }) => (
        <button key={key} type="button" className="res-act acq" data-testid={`hit-acquire-${key}`} onClick={go(kinds)}>
          {key === 'ebook' && <Download aria-hidden />}
          {t(`search.hit.get.${key}`)}
        </button>
      ))}
    </span>
  )
}

function hours(minutes: number, t: (key: string, values?: Record<string, unknown>) => string): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? t('search.hit.edition.hours', { h, m }) : t('search.hit.edition.minutes', { m })
}

/** One edition: the language, the form, the title as that edition has it, and what tells it from the others. */
export function EditionLine({ edition }: { edition: CatalogHitEdition }) {
  const { t, i18n } = useTranslation()
  const facts = [
    languageName(edition.language, i18n.language),
    edition.form === 'audio' ? t('search.kind.audio') : t('search.hit.edition.print'),
    edition.year ? String(edition.year) : '',
    edition.publisher,
    edition.narrators.length ? t('search.hit.edition.readBy', { names: edition.narrators.join(', ') }) : '',
    edition.minutes ? hours(edition.minutes, t) : '',
    edition.variant === 'abridged' ? t('search.hit.edition.abridged') : edition.variant === 'dramatized' ? t('search.hit.label.drama') : '',
    edition.pages ? t('search.hit.edition.pages', { count: edition.pages }) : '',
    edition.isbn ? `ISBN ${edition.isbn}` : '',
    t(`sources.names.${edition.source}`),
  ].filter(Boolean)
  return (
    <li className="hit-edition" data-testid="hit-edition">
      <b dir="auto">{edition.title}</b>
      {edition.subtitle && <span dir="auto"> {edition.subtitle}</span>}
      <small>{facts.join(' · ')}</small>
    </li>
  )
}
