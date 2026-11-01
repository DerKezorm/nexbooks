/**
 * The reading stack: for each year a tower of lying books, as thick as their pages say
 * (a recording as thick as a recording alone is), on a board, with the year and what it came to; above them the recap of a
 * year (books, pages, hours, the genre most often, the thickest book with its cover). A press on a lying book opens its page
 * the way a cover of the grid does. Only the books the person finished: the stack is their own.
 *
 * The recap starts with the last year that is over (the design shows 2025 in 2026); a press on the year of a tower makes it
 * the year of the recap. The towers drop in one by one, a book every 28 ms, unless the system asks for less motion.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, historyApi, type StackEntry, type StackOut, type StackYear, type WorkBrief } from '../api/client'
import { Cover } from '../components/BookCard'
import { PageHead } from '../components/PageHead'
import { SpineTip } from '../components/Shelf'
import { authorLine, figure, inkFor } from '../lib/books'
import { errorText } from '../lib/errors'
import { reducedMotion } from '../lib/motion'
import { useOpenBook } from '../lib/openBook'
import { useSpineTip } from '../lib/shelf'
import { footName } from '../lib/spine'
import { hours, lyingOffset, lyingThickness, lyingWidth, recapYear } from '../lib/stack'

/** A book drops this long after the one before it. */
const DROP_MS = 28

function Lying({ entry, index, calm, onOpen }: { entry: StackEntry; index: number; calm: boolean; onOpen: (work: WorkBrief) => void }) {
  const { t } = useTranslation()
  const work = entry.work
  const style = {
    '--c': work.color,
    '--ink': inkFor(work.color),
    '--w': `${lyingWidth(entry)}px`,
    '--t': `${lyingThickness(entry)}px`,
    '--off': `${lyingOffset(entry)}px`,
    animationDelay: `${calm ? 0 : index * DROP_MS}ms`,
  } as CSSProperties
  return (
    <button
      type="button"
      className={`lying ${entry.kind === 'listened' ? 'audio' : ''}`}
      style={style}
      data-work={work.id}
      data-entry={entry.id}
      title={work.title}
      aria-label={`${work.title}${work.authors[0] ? `, ${authorLine(work)}` : ''}, ${t(entry.kind === 'listened' ? 'stack.listened' : 'stack.read')}`}
      onClick={() => onOpen(work)}
    >
      <span dir="auto">{work.title}</span>
      <em dir="auto">{footName(work)}</em>
    </button>
  )
}

function Recap({ year, language }: { year: StackYear; language: string }) {
  const { t } = useTranslation()
  const thickest = year.thickest
  return (
    <section id="recap" className="recap" data-testid="recap" aria-label={t('stack.recap.label', { year: year.year })}>
      <div className="rc" data-testid="rc-books">
        <b>{figure(year.books, language)}</b>
        <span>{t('stack.recap.books', { year: year.year })}</span>
      </div>
      <div className="rc" data-testid="rc-pages">
        <b>{figure(year.pages, language)}</b>
        <span>{t('stack.recap.pages')}</span>
      </div>
      <div className="rc" data-testid="rc-hours">
        <b>{t('stack.recap.hours', { count: hours(year.seconds) })}</b>
        <span>{t('stack.recap.heard')}</span>
      </div>
      {year.genre && (
        <div className="rc" data-testid="rc-genre">
          <b>{t(`library.genre.${year.genre}`)}</b>
          <span>{t('stack.recap.often')}</span>
        </div>
      )}
      {thickest && (
        <div className="rc big" data-testid="rc-thickest">
          <span>{t('stack.recap.thickest')}</span>
          <div className="longest">
            <Cover work={thickest.work} />
            <div>
              <b dir="auto">{thickest.work.title}</b>
              <span dir="auto">
                {[authorLine(thickest.work), t('stack.recap.pagesOf', { count: thickest.pages })].filter(Boolean).join(' · ')}
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

export function StackPage() {
  const { t, i18n } = useTranslation()
  const [stack, setStack] = useState<StackOut | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [chosen, setChosen] = useState<number | null>(null)
  const openBook = useOpenBook()
  useEffect(() => {
    const controller = new AbortController()
    historyApi.stack(controller.signal).then(
      (found) => setStack({ years: Array.isArray(found?.years) ? found.years : [], today: found?.today ?? '' }),
      (error) => {
        if (!(error instanceof ApiError && error.code === 'cancelled')) setFailure(error instanceof ApiError ? error.code : 'internal_error')
      },
    )
    return () => controller.abort()
  }, [])
  const years = useMemo(() => stack?.years ?? [], [stack])
  const works = useMemo(() => years.flatMap((year) => year.entries.map((entry) => entry.work)), [years])
  const tip = useSpineTip(works)
  const calm = reducedMotion()
  const now = Number((stack?.today ?? '').slice(0, 4))
  const shown = chosen ?? recapYear(years, stack?.today ?? '')
  const recap = years.find((year) => year.year === shown)
  const language = i18n.language
  return (
    <div className="view">
      <PageHead title={t('stack.title')} text={t('stack.text')} />
      {failure && (
        <p className="empty" role="alert">
          {errorText(failure)}
        </p>
      )}
      {stack && years.length === 0 && (
        <div data-testid="stack-empty-box">
          <p className="empty" data-testid="stack-empty">
            {t('stack.empty')}
          </p>
          <p className="empty-hint" data-testid="stack-hint">
            {t('stack.emptyHint')}
          </p>
        </div>
      )}
      {recap && <Recap year={recap} language={language} />}
      {years.length > 0 && (
        <div id="stacks" className="stacks" data-testid="stacks" {...tip.handlers}>
          {years.map((year) => (
            <div className="year" key={year.year} data-year={year.year}>
              <div className="tower">
                {year.entries.map((entry, index) => (
                  <Lying key={entry.id} entry={entry} index={index} calm={calm} onOpen={(work) => openBook(work.id, work)} />
                ))}
              </div>
              <div className="plank" />
              <h3>
                <button type="button" className={`yr ${year.year === shown ? 'on' : ''}`} aria-pressed={year.year === shown} onClick={() => setChosen(year.year)} data-testid={`year-${year.year}`}>
                  {year.year}
                </button>
                {year.year === now && <small>{t('stack.sofar')}</small>}
              </h3>
              <p>
                {t('stack.year.books', { count: year.books })} · {t('stack.year.pages', { count: year.pages, pages: figure(year.pages, language) })}
                <br />
                {t('stack.year.heard', { count: hours(year.seconds) })}
              </p>
            </div>
          ))}
        </div>
      )}
      <SpineTip tip={tip.tip} />
    </div>
  )
}
