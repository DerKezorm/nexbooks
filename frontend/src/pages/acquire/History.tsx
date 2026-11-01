/** "Verlauf" and "Sperrliste": what became of the downloads, and the
 * releases nexbooks never takes for a book again, which anybody who may ask for books can take off the list (asks first). */
import { Check, Lock, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { editionsApi, procureApi, type BlockLine, type HistoryLine } from '../../api/client'
import { useConfirm } from '../../components/useConfirm'
import { ago } from '../../lib/ago'
import { languageName } from '../../lib/books'
import { iso } from '../../lib/say'
import { useLoaded } from './hooks'
import { useOpenAt } from '../../lib/openBook'

type Filter = 'all' | 'done' | 'bad'

function Title({ work, book, title }: { work: number | null; book: string | null; title: string }) {
  const open = useOpenAt()
  if (work == null && !book) return <span dir="auto">{title}</span>
  return (
    <button type="button" className="lnk" onClick={() => (work != null ? open(`/work/${work}`, {}) : open(`/discover/book/${book}`, {}))} dir="auto">
      {title}
    </button>
  )
}

export function History() {
  const { t, i18n } = useTranslation()
  const [filter, setFilter] = useState<Filter>('all')
  const { value, failure, reload } = useLoaded(() => procureApi.history(filter === 'all' ? undefined : filter), [filter])
  const confirm = useConfirm()
  /** "Trennen" for an edition the scan put into this book: it becomes a book of its own again (asks first). */
  const split = (line: HistoryLine) => {
    const title = String(line.note?.title ?? '')
    const edition = Number(line.note?.edition_id)
    confirm.ask({
      title: t('acquire.history.split.title', { title }),
      text: t('acquire.history.split.text', { title }),
      confirm: t('acquire.history.split.confirm'),
      danger: false,
      run: async () => {
        await editionsApi.split(edition)
        await reload()
      },
    })
  }
  const rows: HistoryLine[] = value ?? []
  const fmt = (code: unknown) => (code ? t(`procure.formats.name.${String(code)}`, { defaultValue: String(code).toUpperCase() }) : '')
  const upgradeOf = (line: HistoryLine) => (line.note?.upgrade && typeof line.note.upgrade === 'object' ? (line.note.upgrade as { from?: string; to?: string }) : null)
  const note = (line: HistoryLine) => {
    const code = String(line.note?.code ?? '')
    const detail = String(line.note?.detail ?? '')
    if (line.result === 'joined') return t('acquire.history.note.joined', { title: String(line.note?.title ?? '') })
    if (line.result === 'done') {
      const upgrade = upgradeOf(line)
      const library = line.note?.library ? String(line.note.library) : ''
      let filed = upgrade
        ? t('acquire.history.note.upgrade', { from: fmt(upgrade.from), to: fmt(upgrade.to) })
        : line.filed_as
          ? library
            ? t('acquire.history.note.filedAs', { title: line.filed_as, library })
            : t('acquire.history.note.filedAsOnly', { title: line.filed_as })
          : library
            ? t('acquire.history.note.filed', { library })
            : ''
      if (line.differs && line.wished && line.filed_as && !upgrade) filed = t('acquire.history.note.differs', { filed, wished: line.wished.title })
      return line.note?.variant === 'abridged' ? `${filed}${filed ? ', ' : ''}${t('acquire.history.note.asAbridged')}` : filed
    }
    if (line.result === 'rejected' && code) {
      const file = String(line.note?.file ?? '')
      const wanted = String(line.note?.wanted ?? '')
      return t(`acquire.history.rejected.${code}`, {
        defaultValue: t('acquire.history.rejected.other'),
        file: languageName(file, i18n.language) || file,
        wanted: languageName(wanted, i18n.language) || wanted,
        found: Array.isArray(line.note?.found) ? (line.note.found as unknown[]).map((item) => fmt(item)).join(', ') : fmt(line.note?.found),
        have: fmt(line.note?.have),
        title: String(line.note?.title ?? ''),
      })
    }
    if (!code) return t('acquire.history.note.other')
    return t(`acquire.history.note.${code}`, { defaultValue: t('acquire.history.note.other'), detail: detail ? t(`acquire.history.detail.${detail}`, { defaultValue: detail }) : '' })
  }
  return (
    <>
      <div className="bes-top">
        <p className="bes-count">{t('acquire.history.count', { count: rows.length })}</p>
        <div className="seg small bes-seg" role="group" aria-label={t('acquire.history.filter')}>
          {(['all', 'done', 'bad'] as const).map((key) => (
            <button type="button" key={key} className={filter === key ? 'on' : ''} onClick={() => setFilter(key)} data-hist={key}>
              {t(`acquire.history.${key}`)}
            </button>
          ))}
        </div>
      </div>
      {failure && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {t('acquire.loadFailed')}
        </p>
      )}
      {value && rows.length === 0 && (
        <p className="empty" data-testid="history-empty">
          {t('acquire.history.empty')}
        </p>
      )}
      {confirm.dialog}
      <div className="hist" data-testid="history">
        {rows.map((line) => {
          const ok = line.result === 'done' || line.result === 'joined'
          const differs = ok && Boolean(line.differs)
          const wished = line.wished
          // A book a person sent away from its page ("Falsches Buch") or put in the trash there reads as that, not as an
          // abandoned download.
          const byHand = line.result === 'removed' && ['wrong_book', 'trashed'].includes(String(line.note?.code ?? '')) ? String(line.note?.code) : ''
          return (
            <article className={`hr ${ok ? 'ok' : 'bad'}${differs ? ' differs' : ''}`} key={line.id} data-result={line.result} data-differs={differs ? 'true' : undefined}>
              <span className="hs" title={t(differs ? 'acquire.history.result.differs' : byHand ? `acquire.history.result.${byHand}` : `acquire.history.result.${line.result}`)}>
                {ok && !differs ? <Check aria-hidden className="ic" /> : <X aria-hidden className="ic" />}
              </span>
              <div className="hm">
                <b>
                  <Title work={line.work_id} book={line.book} title={line.book_title || wished?.title || t('acquire.history.unknownBook')} />
                </b>
                {wished && (
                  <small dir="auto" className="hwish" data-testid="wished">
                    {wished.authors[0] ? t('acquire.history.wished', { title: wished.title, author: wished.authors[0] }) : t('acquire.history.wishedTitle', { title: wished.title })}
                  </small>
                )}
                <small dir="auto">
                  {line.book_author ? `${line.book_author} · ` : ''}
                  {t(`procure.releases.kind.${line.kind}`)}
                </small>
              </div>
              <div className="hrel">
                <span className="rel" dir="auto">
                  {line.release}
                </span>
                <small>{iso(line.indexer)}</small>
              </div>
              <div className="hres">
                <span className={`rchip ${differs ? 'bad' : ok ? 'ok' : line.result === 'rejected' ? 'warn' : 'bad'}`}>
                  {differs ? t('acquire.history.result.differs') : ok && upgradeOf(line) ? t('acquire.history.result.upgrade') : byHand ? t(`acquire.history.result.${byHand}`) : t(`acquire.history.result.${line.result}`)}
                </span>
                <small>{note(line)}</small>
              </div>
              <time dateTime={line.created_at}>{ago(line.created_at, i18n.language, t('acquire.justNow'))}</time>
              {line.result === 'joined' && line.can_split && (
                <button type="button" className="bbtn small" onClick={() => split(line)} data-act="split">
                  {t('acquire.history.split.split')}
                </button>
              )}
            </article>
          )
        })}
      </div>
    </>
  )
}

export function Blocklist({ onChanged }: { onChanged: () => Promise<void> }) {
  const { t, i18n } = useTranslation()
  /** Why a release is blocked, in a sentence that needs nothing from the search it came from: the reasons whose words ask
   * for the language or the format of that release have a sentence of their own here, never an empty place. */
  const why = (reason: string, otherwise: string) =>
    i18n.exists(`acquire.block.why.${reason}`) ? t(`acquire.block.why.${reason}`) : t(`procure.releases.reasons.${reason}.text`, { defaultValue: otherwise })
  const { value, failure, reload } = useLoaded(() => procureApi.blocklist())
  const confirm = useConfirm()
  const rows: BlockLine[] = value ?? []
  const unblock = (entry: BlockLine) =>
    confirm.ask({
      title: t('acquire.block.unblock.title'),
      text: t('acquire.block.unblock.text', { why: why(entry.reason, t('acquire.block.byHand')) }),
      confirm: t('acquire.block.unblock.confirm'),
      danger: false,
      run: async () => {
        await procureApi.unblock(entry.id)
        await reload()
        await onChanged()
      },
    })
  if (value && rows.length === 0) {
    return (
      <p className="empty" data-testid="block-empty">
        {t('acquire.block.empty')}
      </p>
    )
  }
  return (
    <>
      {failure && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {t('acquire.loadFailed')}
        </p>
      )}
      {rows.length > 0 && <p className="bes-count mb">{t('acquire.block.count', { count: rows.length })}</p>}
      <div className="hist blocks" data-testid="blocklist">
        {rows.map((entry) => (
          <article className="hr bad" key={entry.id} data-block={entry.id}>
            <span className="hs lockic" title={t('acquire.block.locked')}>
              <Lock aria-hidden className="ic" />
            </span>
            <div className="hm">
              <b>
                <Title work={entry.work_id} book={entry.book} title={entry.book_title || t('acquire.history.unknownBook')} />
              </b>
              <small dir="auto">
                {entry.book_author ? `${entry.book_author} · ` : ''}
                {t(`procure.releases.kind.${entry.kind}`)}
              </small>
            </div>
            <div className="hrel">
              <span className="rel" dir="auto">
                {entry.title}
              </span>
              <small>
                {entry.by ? t('acquire.block.by', { name: entry.by }) : t('acquire.block.auto')} · {ago(entry.created_at, i18n.language, t('acquire.justNow'))}
              </small>
            </div>
            <div className="hres">
              <span className="rchip warn">{t(`procure.releases.reasons.${entry.reason}.chip`, { defaultValue: t('acquire.block.byHand') })}</span>
              <small>{why(entry.reason, '')}</small>
            </div>
            <button type="button" className="bbtn small" onClick={() => unblock(entry)} data-act="unblock">
              {t('acquire.block.unblock.button')}
            </button>
          </article>
        ))}
      </div>
      {confirm.dialog}
    </>
  )
}
