/**
 * The works of an author:
 * where the catalogue comes from and how sure it is, the filters (language, by kind or by year, covers or a list, "hide what
 * I have"), the sections with a cover or a row for each work and the marks of its editions (on the shelf, wanted, exists,
 * not known), "Beschaffen" for one work and "Alle fehlenden beschaffen" for a section (through the dialog every other place
 * uses), and "Nicht sicher zugeordnet" with "Verschieben nach …".
 *
 * Whoever may not fetch books saves them on the wish list instead ("Merken", "Alle fehlenden merken", after a question).
 */
import { Download, Info } from 'lucide-react'
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, catalogApi, wishApi, type AuthorCatalog, type CatalogEdition, type CatalogWork } from '../api/client'
import { languageName } from '../lib/books'
import { ALL, KIND_ORDER, coverOfWork, editionIn, languageOptions, markOf, missing, originalTitle, sections, visible, type AuthorFilter, type Movable, type Section } from '../lib/catalog'
import { dayText } from '../lib/discover'
import { errorText } from '../lib/errors'
import { useOpenAt, useOpenBook } from '../lib/openBook'
import { useProcure } from '../state/procure'
import { UNDO_MS, useWish } from '../state/wish'
import { AudioIcon, BookIcon, Cover } from './BookCard'
import { CheckIcon, MarkIcon } from './icons'
import { useConfirm } from './useConfirm'

type Ask = (work: CatalogWork, kind: 'ebook' | 'audio') => boolean

function yearText(edition: CatalogEdition | null, work: CatalogWork): string {
  const year = edition?.year ?? (edition?.released ? Number(edition.released.slice(0, 4)) : null) ?? work.year
  return year ? String(year) : ''
}

/** The mark of one kind of a work: ✓ on the shelf, "gesucht", exists (and from when), not known. */
function Mark({ work, edition, kind, asked }: { work: CatalogWork; edition: CatalogEdition | null; kind: 'ebook' | 'audio'; asked: Ask }) {
  const { t, i18n } = useTranslation()
  const mark = markOf(work, edition, kind, asked(work, kind))
  const name = t(`author.kindName.${kind}`)
  const icon = kind === 'ebook' ? <BookIcon /> : <AudioIcon />
  const title =
    mark === 'avail' && edition?.upcoming && edition.released
      ? t('author.mark.availFrom', { kind: name, day: dayText(edition.released, i18n.language, true) })
      : t(`author.mark.${mark}`, { kind: name })
  return (
    <span className={`fp ${mark}`} title={title} data-mark={`${kind}:${mark}`}>
      {icon}
      {mark === 'have' && <CheckIcon />}
      {mark === 'want' && <Download aria-hidden />}
      <span className="sr-only">{title}</span>
    </span>
  )
}

function Badge({ work }: { work: CatalogWork }) {
  const { t } = useTranslation()
  if (work.work) {
    return (
      <span className="tbadge owned">
        <CheckIcon />
        {t('discover.badge.owned')}
      </span>
    )
  }
  if (work.wished) {
    return (
      <span className="tbadge">
        <MarkIcon />
        {t('author.wished')}
      </span>
    )
  }
  return null
}

type Shared = {
  catalog: AuthorCatalog
  language: string
  asked: Ask
  open: (work: CatalogWork) => void
  act: (work: CatalogWork, edition: CatalogEdition | null) => ReactNode
  move: (work: CatalogWork, kind: Movable) => void
}

function Unsure({ work, edition, shared }: { work: CatalogWork; edition: CatalogEdition | null; shared: Shared }) {
  const { t } = useTranslation()
  const title = edition?.title ?? work.editions[0]?.title ?? ''
  return (
    <div className="unsure">
      <span className="rchip warn">{t('author.unsureChip')}</span>
      {work.why && <small>{t(`author.why.${work.why}`, { defaultValue: '' })}</small>}
      {shared.catalog.can_move && (
        <select
          className="sel mini"
          aria-label={t('author.moveLabel', { title })}
          value=""
          onChange={(event) => event.target.value && shared.move(work, event.target.value as Movable)}
          data-testid="move-work"
        >
          <option value="">{t('author.moveTo')}</option>
          {KIND_ORDER.map((kind) => (
            <option key={kind} value={kind}>
              {t(`author.kindOne.${kind}`)}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

function subLine(work: CatalogWork, edition: CatalogEdition | null, language: string, t: (key: string, values?: Record<string, unknown>) => string, ui: string): string {
  if (!edition) return ''
  if (language === ALL) {
    const others = work.editions.map((item) => item.language).filter((code) => code && code !== work.orig_language)
    return others.length ? t('author.alsoIn', { languages: others.map((code) => languageName(code, ui)).join(', ') }) : ''
  }
  const original = originalTitle(work, edition)
  return original ? t('author.originalTitle', { title: original }) : ''
}

function Tile({ work, shared }: { work: CatalogWork; shared: Shared }) {
  const { t, i18n } = useTranslation()
  const edition = editionIn(work, shared.language)
  const title = edition?.title ?? work.editions[0]?.title ?? ''
  const up = !!edition?.upcoming
  const sub = subLine(work, edition, shared.language, t, i18n.language)
  return (
    <div className={`atile ${work.work ? 'is-owned' : ''} ${up ? 'is-soon' : ''} ${work.kind === 'unsure' ? 'is-unsure' : ''}`} data-testid="work" data-key={work.key}>
      <button type="button" className="at-cv" onClick={() => shared.open(work)} aria-label={t('author.open', { title })}>
        <Badge work={work} />
        <Cover work={coverOfWork(work, edition, shared.catalog.name)} />
      </button>
      <div className="at-text">
        <span className="at-y">
          {up ? <span className="soon">{edition?.released ? t('author.soonOn', { day: dayText(edition.released, i18n.language, true) }) : t('author.soonIn', { year: edition?.year })}</span> : yearText(edition, work)}
          {work.series_pos !== null && work.series ? ` · ${t('author.volume', { n: work.series_pos })}` : ''}
        </span>
        <button type="button" className="at-t" onClick={() => shared.open(work)} dir="auto">
          {title}
        </button>
        {sub && <small className="at-sub">{sub}</small>}
        {work.pen && <small className="at-sub">{t('author.asPen', { name: work.pen })}</small>}
        {work.maybe_have && (
          <small className="at-sub" data-testid="maybe-have" dir="auto">
            {t('author.maybeHave', { title: work.maybe_have.title })}
          </small>
        )}
      </div>
      <div className="at-foot">
        <span className="fps">
          <Mark work={work} edition={edition} kind="ebook" asked={shared.asked} />
          <Mark work={work} edition={edition} kind="audio" asked={shared.asked} />
        </span>
        {work.kind === 'unsure' ? <Unsure work={work} edition={edition} shared={shared} /> : shared.act(work, edition)}
      </div>
    </div>
  )
}

function Row({ work, shared }: { work: CatalogWork; shared: Shared }) {
  const { t, i18n } = useTranslation()
  const edition = editionIn(work, shared.language)
  const title = edition?.title ?? work.editions[0]?.title ?? ''
  const up = !!edition?.upcoming
  const sub = subLine(work, edition, shared.language, t, i18n.language)
  return (
    <tr className={`${work.work ? 'is-owned' : ''} ${up ? 'is-soon' : ''}`} data-testid="work" data-key={work.key}>
      <td className="c-y">{up ? <span className="soon">{edition?.released ? dayText(edition.released, i18n.language, true) : edition?.year}</span> : yearText(edition, work)}</td>
      <td className="c-t">
        <button type="button" className="at-t" onClick={() => shared.open(work)} dir="auto">
          {title}
        </button>
        {work.series && work.series_pos !== null && (
          <small dir="auto">
            {work.series}, {t('author.volume', { n: work.series_pos })}
          </small>
        )}
        {sub && <small>{sub}</small>}
        {work.pen && <small>{t('author.asPen', { name: work.pen })}</small>}
      </td>
      <td className="c-k hide-s">{work.kind === 'unsure' ? <span className="rchip warn">{t('author.unsureChip')}</span> : t(`author.kindOne.${work.kind}`)}</td>
      <td className="c-f">
        <Mark work={work} edition={edition} kind="ebook" asked={shared.asked} />
        <Mark work={work} edition={edition} kind="audio" asked={shared.asked} />
      </td>
      <td className="c-s hide-s" title={t('author.table.sourcesTitle')}>
        {Array.from({ length: Math.min(4, Math.max(1, work.sources.length)) }, (_, index) => (
          <i key={index} />
        ))}
      </td>
      <td className="c-a">{work.kind === 'unsure' ? <Unsure work={work} edition={edition} shared={shared} /> : shared.act(work, edition)}</td>
    </tr>
  )
}

function sectionTitle(section: Section, t: (key: string, values?: Record<string, unknown>) => string): string {
  switch (section.type) {
    case 'series':
      return t('author.series', { name: section.name })
    case 'kind':
      return t(`author.kind.${section.kind}`)
    case 'pen':
      return t('author.pen', { name: section.name })
    case 'decade':
      return section.decade ? t('author.decade', { decade: section.decade }) : t('author.noYear')
    default:
      return t('author.unsure')
  }
}

function sectionText(section: Section, catalog: AuthorCatalog, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (section.type === 'series') {
    return section.hidden ? t('author.volumesHidden', { count: section.works.length, hidden: section.hidden }) : t('author.volumes', { count: section.works.length })
  }
  if (section.type === 'pen') return t('author.penText', { person: catalog.name })
  if (section.type === 'unsure') return t('author.unsureText')
  return ''
}

/** "Woher dieser Katalog kommt": the sources with their numbers, how sure the catalogue is, and when it was asked. */
export function CatalogNote({ catalog }: { catalog: AuthorCatalog }) {
  const { t, i18n } = useTranslation()
  const catalogued = catalog.works.filter((work) => work.token !== null).length
  const thin = catalog.level === 'low'
  const level = { low: 1, mid: 2, high: 3 }[catalog.level]
  const names = catalog.sources.map((item) => t(`sources.names.${item.source}`, { defaultValue: item.source }))
  const libraries: Record<string, string> = { de: 'dnb', fr: 'bnf', sv: 'libris', nl: 'kb', no: 'nb' }
  const library = libraries[catalog.orig_language]
  const when = catalog.built_at ? new Date(catalog.built_at).toLocaleString(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }) : t('author.note.never')
  // Without a source the line starts with the numbers, not with a dot.
  const key = `author.note.summary${names.length === 0 ? 'NoSource' : ''}${catalog.unsure ? 'Unsure' : ''}`
  const summary = t(key, { sources: names.join(', '), sure: catalog.sure, total: catalogued, unsure: catalog.unsure })
  return (
    <details className={`catnote ${thin ? 'thin' : ''}`} data-testid="catalog-note">
      <summary>
        <Info aria-hidden />
        <span className="cn-s">
          <b>{t('author.note.title')}</b>
          <span>{summary}</span>
        </span>
        <span className={`meter m${level}`} title={t('author.note.meter', { level: t(`author.note.level.${catalog.level}`) })}>
          <i />
          <i />
          <i />
          <em>{t(`author.note.level.${catalog.level}`)}</em>
        </span>
      </summary>
      <div className="cn-body">
        <p>
          {thin ? t('author.note.thin', { name: catalog.name }) : t('author.note.rule')}
          {library && catalog.sources.some((item) => item.source === library) ? ` ${t('author.note.library', { language: languageName(catalog.orig_language, i18n.language), library: t(`sources.names.${library}`) })}` : ''}
        </p>
        <ul className="cn-src">
          {catalog.sources.map((item) => (
            <li key={item.source}>
              <b>{t(`sources.names.${item.source}`, { defaultValue: item.source })}</b>
              <span>{t('author.note.works', { count: item.works })}</span>
            </li>
          ))}
        </ul>
        {catalog.away.length > 0 && <p className="muted">{t('author.note.away', { sources: catalog.away.map((source) => t(`sources.names.${source}`, { defaultValue: source })).join(', ') })}</p>}
        <p className="muted">{t('author.note.footer', { when })}</p>
      </div>
    </details>
  )
}

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (value: T) => void }) {
  return (
    <div className="seg small" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button key={key} type="button" className={value === key ? 'on' : ''} aria-pressed={value === key} onClick={() => onChange(key)}>
          {text}
        </button>
      ))}
    </div>
  )
}

export function AuthorWorks({ catalog, onChange, filter, onFilter }: { catalog: AuthorCatalog; onChange: (next: AuthorCatalog) => void; filter: AuthorFilter; onFilter: (next: AuthorFilter) => void }) {
  const { t, i18n } = useTranslation()
  const procure = useProcure()
  const wish = useWish()
  const openAt = useOpenAt()
  const openBook = useOpenBook()
  const { ask, dialog } = useConfirm()
  const [said, setSaid] = useState<{ text: string; undo?: () => void } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const ui = i18n.language.slice(0, 2)

  useEffect(() => {
    if (!said) return
    const timer = window.setTimeout(() => setSaid(null), UNDO_MS)
    return () => window.clearTimeout(timer)
  }, [said])

  const asked: Ask = (work, kind) => procure.kindsOf({ workId: work.work?.id ?? null, token: work.token }).some((state) => state.kind === kind)
  const askedAny = (work: CatalogWork) => asked(work, 'ebook') || asked(work, 'audio')
  const bookOf = (work: CatalogWork, edition: CatalogEdition | null) => ({
    token: work.token ?? undefined,
    title: edition?.title ?? work.editions[0]?.title ?? '',
    authors: [catalog.name],
    series: work.series,
    series_pos: work.series_pos,
    color: work.color || '#8e3b46',
    cover_url: work.cover_url,
  })
  const fail = (error: unknown) => setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error'))

  const toggleWish = async (work: CatalogWork) => {
    if (!work.token) return
    try {
      await (work.wished ? wishApi.remove(work.token) : wishApi.add(work.token))
      onChange({ ...catalog, works: catalog.works.map((item) => (item === work ? { ...item, wished: !item.wished } : item)) })
      void wish.reload()
    } catch (error) {
      fail(error)
    }
  }

  const act = (work: CatalogWork, edition: CatalogEdition | null): ReactNode => {
    if (!procure.allowed) {
      if (work.work || !work.token) return null
      return (
        <button type="button" className="mini-act" onClick={() => void toggleWish(work)} data-testid="work-wish">
          <MarkIcon />
          {work.wished ? t('author.wished') : t('author.wish')}
        </button>
      )
    }
    const kinds = (['ebook', 'audio'] as const).filter((kind) => edition?.[kind] && (kind === 'ebook' ? !work.have_ebook : !work.have_audio) && !asked(work, kind))
    if (!kinds.length) return null
    if (!work.work) {
      if (!work.token) return null
      return (
        <button type="button" className="mini-act" onClick={() => procure.open([bookOf(work, edition)])} data-testid="work-get">
          <Download aria-hidden />
          {edition?.upcoming ? t('author.reserve') : t('author.get')}
        </button>
      )
    }
    const kind = kinds[0]!
    const own = work.work
    return (
      <button
        type="button"
        className="mini-act"
        data-testid="work-get"
        onClick={() => procure.open([{ workId: own.id, title: own.title, authors: own.authors.map((author) => author.name), series: own.series?.name ?? '', series_pos: own.series?.pos ?? null, color: own.color, cover_url: own.cover_url }], { kinds: [kind] })}
      >
        <Download aria-hidden />
        {t('author.getKind', { kind: t(`author.kindName.${kind}`) })}
      </button>
    )
  }

  const move = async (work: CatalogWork, kind: Movable) => {
    const title = editionIn(work, filter.language)?.title ?? work.editions[0]?.title ?? ''
    try {
      onChange(await catalogApi.move(catalog.token, work.key, kind))
      setSaid({
        text: t('author.moved', { title, kind: t(`author.kind.${kind}`) }),
        undo: async () => {
          try {
            onChange(await catalogApi.unmove(catalog.token, work.key))
            setSaid({ text: t('author.movedBack', { title }) })
          } catch (error) {
            fail(error)
          }
        },
      })
    } catch (error) {
      fail(error)
    }
  }

  const open = (work: CatalogWork) => {
    if (work.work) openBook(work.work.id, work.work)
    else if (work.token) openAt(`/discover/book/${encodeURIComponent(work.token)}`, {})
  }

  const bulk = (section: Section) => {
    const list = missing(section.works, filter.language, askedAny)
    if (!list.length) return
    if (procure.allowed) {
      procure.open(list.map((work) => bookOf(work, editionIn(work, filter.language))))
      return
    }
    const fresh = list.filter((work) => !work.wished)
    ask({
      title: t('author.bulkWishTitle', { count: fresh.length }),
      text: t('author.bulkWishText', { section: sectionTitle(section, t) }),
      confirm: t('author.bulkWishConfirm'),
      danger: false,
      run: async () => {
        try {
          for (const work of fresh) if (work.token) await wishApi.add(work.token)
          const tokens = new Set(fresh.map((work) => work.token))
          onChange({ ...catalog, works: catalog.works.map((item) => (tokens.has(item.token) ? { ...item, wished: true } : item)) })
          void wish.reload()
          setSaid({ text: t('author.bulkWished', { count: fresh.length }) })
        } catch (error) {
          fail(error)
        }
      },
    })
  }

  const shared: Shared = { catalog, language: filter.language, asked, open, act, move: (work, kind) => void move(work, kind) }
  const shown = useMemo(() => sections(catalog.works, filter), [catalog.works, filter])
  const counts = visible(catalog.works, filter)
  const options = languageOptions(catalog, ui)
  const legend = (mark: 'have' | 'want' | 'avail' | 'none') => (
    <>
      <span className={`fp ${mark}`}>
        <BookIcon />
        {mark === 'have' && <CheckIcon />}
        {mark === 'want' && <Download aria-hidden />}
      </span>
      {t(`author.legend.${mark}`)}
    </>
  )
  return (
    <>
      <div className="au-werk-h">
        <h2>{t('author.werk')}</h2>
        <CatalogNote catalog={catalog} />
      </div>
      <div className="au-tools">
        <Seg
          label={t('author.tools.language')}
          value={filter.language}
          options={options.map((code) => [code, code === ALL ? t('author.tools.allLanguages') : `${languageName(code, i18n.language)}${code === catalog.orig_language ? ` · ${t('author.original')}` : ''}`])}
          onChange={(language) => onFilter({ ...filter, language })}
        />
        <Seg label={t('author.tools.order')} value={filter.order} options={[['kind', t('author.tools.byKind')], ['year', t('author.tools.byYear')]]} onChange={(order) => onFilter({ ...filter, order })} />
        <Seg label={t('author.tools.look')} value={filter.look} options={[['covers', t('author.tools.covers')], ['list', t('author.tools.list')]]} onChange={(look) => onFilter({ ...filter, look })} />
        <label className="toggle">
          <input type="checkbox" checked={filter.hideOwned} onChange={(event) => onFilter({ ...filter, hideOwned: event.target.checked })} data-testid="hide-owned" />
          <span />
          {t('author.tools.hideOwned')}
        </label>
      </div>
      <p className="au-legend">
        {legend('have')} {legend('want')} {legend('avail')} {legend('none')}
        {counts.hiddenLanguage > 0 && filter.language !== ALL && (
          <span className="au-hid" data-testid="hidden-language">
            {t('author.legend.hiddenLanguage', { count: counts.hiddenLanguage, language: languageName(filter.language, i18n.language) })}{' '}
            <button type="button" className="lnk" onClick={() => onFilter({ ...filter, language: ALL })}>
              {t('author.legend.showAll')}
            </button>
          </span>
        )}
        {counts.hiddenOwned > 0 && <span className="au-hid">{t('author.legend.hiddenOwned', { count: counts.hiddenOwned })}</span>}
      </p>
      {problem && (
        <p className="empty" role="alert">
          {problem}
        </p>
      )}
      {shown.length === 0 && <p className="empty">{t('author.empty')}</p>}
      {shown.map((section) => {
        const owned = section.works.filter((work) => work.work).length
        const gap = section.type === 'unsure' ? [] : missing(section.works, filter.language, askedAny)
        const text = sectionText(section, catalog, t)
        return (
          <section className="au-sec" key={section.id} data-testid="author-section" data-section={section.id}>
            <div className="au-sec-h">
              <div>
                <h2>
                  {sectionTitle(section, t)} <small>{owned ? t('author.countOwned', { count: section.works.length, owned }) : t('author.count', { count: section.works.length })}</small>
                </h2>
                {text && <p>{text}</p>}
              </div>
              {gap.length > 0 && (procure.allowed || gap.some((work) => !work.wished)) && (
                <button type="button" className="bbtn small" onClick={() => bulk(section)} data-testid="bulk">
                  {procure.allowed ? <Download aria-hidden /> : <MarkIcon />}
                  {procure.allowed ? t('author.bulkGet') : t('author.bulkWish')} <small className="cnt">{procure.allowed ? gap.length : gap.filter((work) => !work.wished).length}</small>
                </button>
              )}
            </div>
            {filter.look === 'list' ? (
              <div className="atab-w">
                <table className="atab">
                  <thead>
                    <tr>
                      <th>{t('author.table.year')}</th>
                      <th>{t('author.table.title')}</th>
                      <th className="hide-s">{t('author.table.kind')}</th>
                      <th>{t('author.table.forms')}</th>
                      <th className="hide-s">{t('author.table.sources')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {section.works.map((work) => (
                      <Row key={work.key} work={work} shared={shared} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="agrid">
                {section.works.map((work) => (
                  <Tile key={work.key} work={work} shared={shared} />
                ))}
              </div>
            )}
          </section>
        )
      })}
      {said && (
        <div role="status" className="rise fixed inset-x-0 z-40 flex justify-center px-4" style={{ bottom: 'calc(2rem + var(--player-space, 0px))' } as CSSProperties} data-testid="author-notice">
          <p className="rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-paper shadow-soft">
            {said.text}{' '}
            {said.undo && (
              <button type="button" className="underline underline-offset-2" onClick={() => said.undo?.()}>
                {t('author.undo')}
              </button>
            )}
          </p>
        </div>
      )}
      {dialog}
    </>
  )
}
