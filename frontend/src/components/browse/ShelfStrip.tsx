/**
 * One shelf on the wall: its name, the tabs of its sections (new, popular now, prize
 * winners, classics: only those the sources gave books for), the link to the whole shelf, and a strip of covers on a board.
 * Another section is read from the server when it is asked for, and kept while the wall stands.
 */
import { ArrowRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, browseApi, type ShelfCard, type ShelfSection, type ShelfStrip } from '../../api/client'
import { cardMeta, SECTIONS } from '../../lib/browse'
import { errorText } from '../../lib/errors'
import { ShelfBook } from './ShelfBook'

const STRIP = 14

export function ShelfStripView({ strip, market, hide }: { strip: ShelfStrip; market: string; hide: boolean }) {
  const { t, i18n } = useTranslation()
  const [section, setSection] = useState<ShelfSection>(strip.section)
  // The books of the sections already read, by section; the first is the one the wall came with.
  const [kept, setKept] = useState<Partial<Record<ShelfSection, ShelfCard[]>>>({
    [strip.section]: strip.items,
  })
  const [failed, setFailed] = useState(false)

  // Another shelf, another market or the switch that hides what the person has: the strip starts over. The same shelf read
  // again (the wall asks again while the server fills it) keeps the section the person chose and takes the new books of
  // its first section.
  const ident = `${strip.shelf}|${market}|${hide}`
  const shown = useRef(ident)
  useEffect(() => {
    if (shown.current !== ident) {
      shown.current = ident
      setSection(strip.section)
      setKept({ [strip.section]: strip.items })
      setFailed(false)
      return
    }
    setKept((now) => ({ ...now, [strip.section]: strip.items }))
  }, [strip, ident])

  useEffect(() => {
    if (kept[section]) return
    const controller = new AbortController()
    browseApi.shelf(strip.shelf, { market, section, hide_owned: hide, limit: STRIP }, controller.signal).then(
      (answer) => setKept((now) => ({ ...now, [section]: answer.items })),
      (error) => {
        if (!(error instanceof ApiError && error.code === 'cancelled')) setFailed(true)
      },
    )
    return () => controller.abort()
  }, [section, kept, strip.shelf, market, hide])

  const items = kept[section] ?? kept[strip.section] ?? []
  const open = `/discover/shelf/${strip.shelf}`
  const name = t(`browse.shelf.${strip.shelf}.name`)
  const tabs = SECTIONS.filter((key) => strip.sections.some((entry) => entry.key === key))
  const kind = section === 'new' ? 'new' : section === 'award' ? 'award' : ''
  return (
    <div className="shblock" data-shelf={strip.shelf} data-testid="strip">
      <div className="shb-h">
        <h3>
          <Link to={open}>{name}</Link>
        </h3>
        <div className="subtabs" role="group" aria-label={name}>
          {tabs.map((key) => (
            <button key={key} type="button" className={section === key ? 'on' : ''} aria-pressed={section === key} data-section={key} onClick={() => setSection(key)}>
              {t(`browse.section.${key}`)}
            </button>
          ))}
        </div>
        <Link className="sec-link" to={open}>
          {t('browse.wholeShelf')} <ArrowRight aria-hidden />
        </Link>
      </div>
      {failed && (
        <p role="alert" className="mb-2 text-sm text-bad">
          {errorText('internal_error')}
        </p>
      )}
      {items.length > 0 ? (
        <div className="brow">
          {items.map((card) => (
            <ShelfBook key={card.token} card={card} meta={cardMeta(card, kind, t, i18n.language)} />
          ))}
        </div>
      ) : (
        <p className="empty slim">{t('browse.shelfPage.none')}</p>
      )}
    </div>
  )
}
