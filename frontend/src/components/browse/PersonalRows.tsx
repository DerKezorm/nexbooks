/**
 * The shelves made for the person: "because you read ...", "like your favourite
 * books", "short", "for long evenings". The server makes them from what the person read, on this server and nowhere else;
 * the page only draws what it gets, and nothing at all when there is no row.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, browseApi, type PersonalRow } from '../../api/client'
import { cardMeta, minutesText } from '../../lib/browse'
import { cardOfWork, coverOf } from '../../lib/discover'
import { Cover } from '../BookCard'
import { ShelfBook } from './ShelfBook'

/** A recording this long (minutes) is named by its running time in the row for long evenings; other books by their pages. */
const LONG_MINUTES = 20 * 60

export function PersonalRows({ market, hide }: { market: string; hide: boolean }) {
  const { t, i18n } = useTranslation()
  const [rows, setRows] = useState<PersonalRow[]>([])

  useEffect(() => {
    const controller = new AbortController()
    browseApi.personal({ market, hide_owned: hide }, controller.signal).then(
      (answer) => setRows(answer.rows),
      (error) => {
        // The rows are an extra: a failure leaves the wall without them.
        if (!(error instanceof ApiError && error.code === 'cancelled')) setRows([])
      },
    )
    return () => controller.abort()
  }, [market, hide])

  if (rows.length === 0) return null
  return (
    <section className="dsec pers" data-testid="personal">
      <div className="dsec-h">
        <div>
          <h2>{t('browse.personal.title')}</h2>
          <p>{t('browse.personal.text')}</p>
        </div>
      </div>
      {rows.map((row) => (
        <div className="prow" key={row.key} data-testid={`personal-${row.key}`}>
          <h3>
            {row.key === 'like' && (
              <span className="favs">
                {row.reads.map((work) => (
                  <span className="favc" key={work.id} title={work.title}>
                    <Cover work={coverOf(cardOfWork(work))} />
                  </span>
                ))}
              </span>
            )}
            {row.key === 'because'
              ? t('browse.personal.because', {
                  title: row.reads[0]?.title ?? '',
                })
              : t(`browse.personal.${row.key}`)}
            <small>
              {row.key === 'because' && [row.author, row.genre ? t(`library.genre.${row.genre}`, { defaultValue: '' }) : ''].filter(Boolean).join(' · ')}
              {row.key !== 'because' && t(`browse.personal.${row.key}Note`, { defaultValue: '' })}
            </small>
          </h3>
          <div className="brow">
            {row.items.map((card) => (
              <ShelfBook
                key={card.token}
                card={card}
                meta={row.key === 'short' ? cardMeta(card, 'pages', t, i18n.language) : row.key === 'long' ? (card.minutes && card.minutes >= LONG_MINUTES ? minutesText(card.minutes, t) : cardMeta(card, 'pages', t, i18n.language)) : ''}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}
