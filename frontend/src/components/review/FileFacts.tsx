/**
 * What the file says, for the review list. Two parts: the panel under the book of the person (the files with their folder, and
 * what is written in them, field by field, with "the file says nothing" where there is nothing), and the comparison under a
 * proposal (what the file contradicts, what is close, what agrees, what the file does not say and what the source does not know).
 * The server works the comparison out (`GET /review/{id}/file`); this only words it.
 */
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import type { CompareRow, FileFacts } from '../../api/client'
import { clock } from '../../lib/timeline'
import { folderText, GROUPS, groupRows } from '../../lib/reviewFacts'

function minutesText(value: string, t: TFunction): string {
  const minutes = Number(value)
  return Number.isFinite(minutes) && minutes > 0 ? t('review.minutes', { count: minutes }) : value
}

/** The value of one row as it is shown: a time in words, everything else as it stands. */
function shown(row: CompareRow, side: 'file' | 'source', t: TFunction): string {
  const value = row[side]
  return row.unit === 'minutes' ? minutesText(value, t) : value
}

/** The comparison of one proposal with the file, in groups; a group without rows is not drawn. */
export function Compare({ rows }: { rows: CompareRow[] }) {
  const { t } = useTranslation()
  const groups = groupRows(rows)
  if (rows.length === 0) return null
  return (
    <div className="compare" data-testid="proposal-compare">
      {GROUPS.map((state) => {
        const list = groups[state]
        if (list.length === 0) return null
        // What contradicts or is close is shown with both values; the rest as the names of the fields only.
        const detailed = state === 'differs' || state === 'similar'
        return (
          <div key={state} className={`compare-group is-${state.replace('_', '-')}`} data-state={state}>
            <p className="compare-head">{t(`review.facts.group.${state}`)}</p>
            {detailed ? (
              <ul>
                {list.map((row) => (
                  <li key={row.field} data-field={row.field}>
                    <b>{t(`review.facts.field.${row.field}`)}</b>
                    <span className="compare-values">
                      {t('review.facts.fileSays', { value: shown(row, 'file', t) })} · {t('review.facts.sourceSays', { value: shown(row, 'source', t) })}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="compare-names">{list.map((row) => t(`review.facts.field.${row.field}`)).join(', ')}</p>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Line({ label, children, silent }: { label: string; children: React.ReactNode; silent?: boolean }) {
  const { t } = useTranslation()
  return (
    <div className="facts-row">
      <dt>{label}</dt>
      <dd className={silent ? 'is-silent' : ''}>{silent ? t('review.facts.nothing') : children}</dd>
    </div>
  )
}

/** The panel: where the files lie, what is written in them. */
export function FilePanel({ facts, top }: { facts: FileFacts; top?: string }) {
  const { t } = useTranslation()
  const says = facts.says
  const audio = facts.kind === 'audio'
  const title = says.title + (says.subtitle ? `: ${says.subtitle}` : '')
  const series = says.series ? (says.series_pos !== null ? `${says.series} ${says.series_pos}` : says.series) : ''
  const named = facts.files.slice(0, 6)
  return (
    <div className="facts" data-testid="file-facts">
      <div className="facts-files">
        <p className="facts-label">{t('review.facts.files', { count: facts.files.length })}</p>
        <p className="facts-folder" data-testid="file-folder">
          {t('review.facts.folder')}: <span>{folderText(facts.folder, top ?? t('review.facts.top'))}</span>
        </p>
        <ul data-testid="file-names">
          {named.map((file) => (
            <li key={file.id}>
              <span className="facts-name">{file.name}</span>
              {file.protected && <span className="facts-flag"> · {t('review.facts.protected')}</span>}
            </li>
          ))}
          {facts.files.length > named.length && <li className="text-muted">{t('review.facts.more', { count: facts.files.length - named.length })}</li>}
        </ul>
      </div>
      <div className="facts-body">
        {facts.cover_url && (
          <figure className="facts-cover" data-testid="file-cover">
            <img src={facts.cover_url} alt="" loading="lazy" decoding="async" draggable={false} />
            <figcaption>{t('review.facts.cover')}</figcaption>
          </figure>
        )}
        <dl>
          <Line label={t('review.facts.field.title')} silent={!says.title}>
            {title}
          </Line>
          <Line label={t('review.facts.field.authors')} silent={says.authors.length === 0}>
            {says.authors.join(', ')}
          </Line>
          {audio && (
            <Line label={t('review.facts.field.narrators')} silent={says.narrators.length === 0}>
              {says.narrators.join(', ')}
            </Line>
          )}
          <Line label={t('review.facts.field.series')} silent={!series}>
            {series}
          </Line>
          <Line label={t('review.facts.field.isbn')} silent={!says.isbn}>
            {says.isbn}
          </Line>
          {says.asin && <Line label={t('review.facts.field.asin')}>{says.asin}</Line>}
          <Line label={t('review.facts.field.publisher')} silent={!says.publisher}>
            {says.publisher}
          </Line>
          <Line label={t('review.facts.field.year')} silent={!says.year}>
            {says.year}
          </Line>
          <Line label={t('review.facts.field.language')} silent={!says.language}>
            {says.language.toUpperCase()}
          </Line>
          {!audio && says.text_language && <Line label={t('review.facts.field.text_language')}>{says.text_language.toUpperCase()}</Line>}
          {audio ? (
            <Line label={t('review.facts.field.runtime')} silent={!says.duration}>
              {clock(says.duration)} · {t('review.minutes', { count: Math.round(says.duration / 60) })}
            </Line>
          ) : (
            <Line label={t('review.facts.field.pages')} silent={!says.pages}>
              {says.pages_counted ? t('review.facts.pagesCounted', { count: says.pages }) : t('review.facts.pagesGuessed', { count: says.pages })}
            </Line>
          )}
          <Line label={t('review.facts.field.chapters')} silent={!says.chapters}>
            {says.chapters}
          </Line>
          {(says.genre || says.subjects.length > 0) && <Line label={t('review.facts.field.genre')}>{[says.genre, ...says.subjects].filter(Boolean).join(', ')}</Line>}
        </dl>
      </div>
    </div>
  )
}
