/**
 * The third look at the library: a row for each book, in a table that can be put in order by its columns. A
 * small cover, the title with its series and volume, the author, the editions as chips (formats, shortened, radio play), the
 * language, the genre (a guessed one marked quietly), the year, the length, where the person is in the e-book and in the
 * recording (apart), and when it came. A person who looks after a library also sees how sure the matching is of the book
 * (a press leads to the review list) and which library it lies in. On a phone only the title with the author, and where the
 * person is, remain.
 *
 * The rows are fixed in height, and **only the rows near the screen are drawn** (the rest is a space of the right height): a
 * library of 2,000 books is no more weight on the page than one of 20.
 */
import { useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Library, ListSort, WorkRow } from '../api/client'
import { authorLine, formatsLabel, languageChip, languageName } from '../lib/books'
import { useEditionLabel } from '../lib/editionText'
import { standOf } from '../lib/listing'
import { useRowWindow } from '../lib/useRowWindow'
import { AudioIcon, BookIcon, Cover } from './BookCard'
import { CheckIcon } from './icons'

/** The height of a row: desktop and phone (the cover is 34 px wide, 51 high, and a title may take two lines). */
export const ROW_PX = 60
export const ROW_PHONE_PX = 68

type Props = {
  works: WorkRow[]
  libraries: Library[]
  sort: ListSort
  dir: 'asc' | 'desc'
  onSort: (sort: ListSort) => void
  onOpen: (work: WorkRow, element: HTMLElement) => void
}

function Stand({ work }: { work: WorkRow }) {
  const { t } = useTranslation()
  const mine = work.mine
  const hasEbook = work.editions.some((edition) => edition.kind === 'ebook')
  const hasAudio = work.editions.some((edition) => edition.kind === 'audio')
  const row = (kind: 'ebook' | 'audio', icon: ReactNode, fraction: number | null | undefined, done: boolean | undefined) => {
    const percent = Math.round((fraction ?? 0) * 100)
    const label = done ? t(kind === 'ebook' ? 'library.list.ebookDone' : 'library.list.audioDone') : fraction ? t(kind === 'ebook' ? 'library.list.ebookAt' : 'library.list.audioAt', { percent }) : t('library.list.notBegun')
    return (
      <span className={`own-row ${done ? 'done' : ''}`} data-kind={kind} role="img" aria-label={label} title={label}>
        {icon}
        <i className="own-bar">
          <b style={{ width: `${done ? 100 : percent}%` }} />
        </i>
        <small>{done ? <CheckIcon /> : fraction ? `${percent} %` : ''}</small>
      </span>
    )
  }
  return (
    <div className="own" data-testid="list-stand">
      {hasEbook && row('ebook', <BookIcon />, mine?.ebook, mine?.ebook_done)}
      {hasAudio && row('audio', <AudioIcon />, mine?.audio, mine?.audio_done)}
    </div>
  )
}

/** The most chips a row shows: two lines of two. The others are one more chip, which names them in its tooltip. */
const MAX_CHIPS = 4

function chipText(edition: WorkRow['editions'][number], t: (key: string) => string): string {
  if (edition.kind === 'ebook') return [languageChip(edition.language), formatsLabel(edition)].filter(Boolean).join(' ')
  if (edition.variant_uncertain) return t('library.list.audioUnproven')
  return edition.variant && edition.variant !== 'unabridged' ? t(`work.variant.${edition.variant}`) : t('library.list.audioCase')
}

function Chips({ work }: { work: WorkRow }) {
  const { t } = useTranslation()
  const shown = work.editions.length > MAX_CHIPS ? work.editions.slice(0, MAX_CHIPS - 1) : work.editions
  const rest = work.editions.slice(shown.length)
  return (
    <span className="chips">
      {shown.map((edition) => (
        <span className="chip" key={edition.id} title={edition.kind === 'ebook' ? formatsLabel(edition) : edition.narrators.join(', ')}>
          {edition.kind === 'ebook' ? <BookIcon /> : <AudioIcon />}
          <span className="chip-text">{chipText(edition, t)}</span>
        </span>
      ))}
      {rest.length > 0 && (
        <span className="chip more" title={rest.map((edition) => chipText(edition, t)).join(', ')}>
          <span className="chip-text">+{rest.length}</span>
        </span>
      )}
    </span>
  )
}

export function ListView({ works, libraries, sort, dir, onSort, onOpen }: Props) {
  const { t, i18n } = useTranslation()
  const { duration } = useEditionLabel()
  const language = i18n.language
  // What the person looks after: the matching and the library are shown only to them.
  const manages = useMemo(() => works.some((work) => work.mine?.match), [works])
  const { table, phone, row, from, to, before, after } = useRowWindow(works.length, ROW_PX, ROW_PHONE_PX)
  const shown = works.slice(from, to)
  const reviewFor = (work: WorkRow) => {
    const names = new Set(work.mine?.libraries ?? [])
    const library = libraries.find((item) => item.my_right === 'manage' && names.has(item.name)) ?? libraries.find((item) => item.my_right === 'manage')
    return library ? `/review?library=${library.id}` : '/review'
  }
  const header = (column: ListSort, optional = false) => {
    const active = sort === column
    const name = t(`library.list.col.${column === 'mine' ? 'mine' : column}`)
    return (
      <th key={column} scope="col" className={`${optional ? 'opt' : ''} ${column === 'added' ? (manages ? 'opt3' : 'opt2') : ''} col-${column}`} aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button type="button" onClick={() => onSort(column)} aria-label={`${t('library.list.sortBy', { column: name })}${active ? `, ${t(dir === 'asc' ? 'library.list.asc' : 'library.list.desc')}` : ''}`} data-sort={column}>
          {name}
          {active && <span aria-hidden>{dir === 'asc' ? '↑' : '↓'}</span>}
        </button>
      </th>
    )
  }

  return (
    <div className="list-wrap" data-testid="list">
      <table className="list-table" ref={table} aria-label={t('library.list.label')} aria-rowcount={works.length + 1}>
        <thead>
          <tr>
            <th className="col-cover" aria-hidden />
            {header('title')}
            {header('author', true)}
            <th scope="col" className="opt opt2 col-editions">
              {t('library.list.col.editions')}
            </th>
            <th scope="col" className={`opt opt2 col-language ${manages ? 'opt3' : ''}`}>
              {t('library.list.col.language')}
            </th>
            {header('genre', true)}
            {header('year', true)}
            {header('size', true)}
            {header('mine')}
            {header('added', true)}
            {manages && (
              <>
                <th scope="col" className="opt optm col-match">
                  {t('library.list.col.match')}
                </th>
                <th scope="col" className="opt optm col-library">
                  {t('library.list.col.library')}
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {before > 0 && (
            // One cell, no span: with fixed columns a cell spanning more columns than the head shows makes empty
            // columns that take the room of the title. The row carries the height.
            <tr className="pad" aria-hidden style={{ height: before }}>
              <td />
            </tr>
          )}
          {shown.map((work, offset) => {
            const audioEdition = work.editions.find((edition) => edition.kind === 'audio')
            const size = work.audio_only && audioEdition ? duration(audioEdition.duration) : work.pages > 0 ? t('library.list.pages', { count: work.pages }) : ''
            const ebook = work.editions.find((edition) => edition.kind === 'ebook')
            const mine = work.mine
            return (
              <tr
                key={work.id}
                data-work={work.id}
                aria-rowindex={from + offset + 2}
                style={{ height: row }}
                className={standOf(work) >= 100 ? 'finished' : ''}
                onClick={(event) => {
                  if ((event.target as HTMLElement).closest('a')) return
                  onOpen(work, event.currentTarget)
                }}
              >
                <td className="col-cover">
                  <Cover work={work} mini />
                </td>
                <th scope="row" className="col-title">
                  <button type="button" className="lt-title" aria-label={t('library.list.openBook', { title: work.title })}>
                    <span dir="auto">{work.title}</span>
                  </button>
                  <small dir="auto">
                    {[work.series ? (work.series.pos !== null ? `${work.series.name} · ${t('library.list.volume', { pos: work.series.pos })}` : work.series.name) : '', phone ? authorLine(work) : ''].filter(Boolean).join(' · ')}
                  </small>
                </th>
                <td className="opt col-author">
                  <span className="clamp" dir="auto">
                    {authorLine(work)}
                  </span>
                </td>
                <td className="opt opt2 col-editions">
                  <Chips work={work} />
                </td>
                <td className={`opt opt2 col-language ${manages ? 'opt3' : ''}`}>
                  {ebook ? languageName(ebook.language, language) : audioEdition ? languageName(audioEdition.language, language) : ''}
                  {(ebook ?? audioEdition)?.language && (ebook ?? audioEdition)?.language_guess && <em className="guess"> {t('library.list.guess')}</em>}
                </td>
                <td className="opt col-genre">
                  <span className="clamp">
                    {work.genre ? t(`library.genre.${work.genre}`) : ''}
                    {work.genre && work.genre_guess && <em className="guess"> {t('library.list.guess')}</em>}
                  </span>
                </td>
                <td className="opt col-year">{work.year ?? ''}</td>
                <td className="opt col-size">{size}</td>
                <td className="col-mine">
                  <Stand work={work} />
                </td>
                <td className={`opt ${manages ? 'opt3' : 'opt2'} col-added`}>{new Date(work.added_at).toLocaleDateString(language, { year: 'numeric', month: 'short', day: 'numeric' })}</td>
                {manages && (
                  <>
                    <td className="opt optm col-match">
                      {mine?.match && (
                        <Link to={reviewFor(work)} className={`match ${mine.match}`} title={t('library.list.toReview')} onClick={(event) => event.stopPropagation()} data-testid="list-match">
                          {t(`library.list.match_${mine.match}`)}
                        </Link>
                      )}
                    </td>
                    <td className="opt optm col-library">
                      <span className="clamp" title={mine?.libraries.join(', ')}>
                        {mine?.libraries.join(', ')}
                      </span>
                    </td>
                  </>
                )}
              </tr>
            )
          })}
          {after > 0 && (
            <tr className="pad" aria-hidden style={{ height: after }}>
              <td />
            </tr>
          )}
        </tbody>
      </table>
      {works.length === 0 && <p className="empty">{t('library.list.none')}</p>}
    </div>
  )
}
