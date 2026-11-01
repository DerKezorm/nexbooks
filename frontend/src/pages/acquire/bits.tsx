/** Small parts of the page "Beschaffen" that the lists share: the little cover that opens the book, the chip of a kind and a
 * language, the line that says what went wrong, and the loading of a list that is read again now and then. */
import { useTranslation } from 'react-i18next'

import type { ProcureBook, ReleaseKind } from '../../api/client'
import { AudioIcon, BookIcon, Cover } from '../../components/BookCard'
import { languageName } from '../../lib/books'
import { useOpenAt } from '../../lib/openBook'
import { coverOfBook } from '../../lib/procure'

/** The little cover; a press opens the page of the book over this page. */
export function MiniCover({ book }: { book: ProcureBook }) {
  const open = useOpenAt()
  const { t } = useTranslation()
  const go = () => (book.work_id != null ? open(`/work/${book.work_id}`, {}) : book.book ? open(`/discover/book/${book.book}`, {}) : undefined)
  return (
    <button type="button" className="mcv" onClick={go} aria-label={t('acquire.openBook', { title: book.title })}>
      <Cover work={coverOfBook(book)} />
    </button>
  )
}

export function BookLink({ book }: { book: Pick<ProcureBook, 'work_id' | 'book' | 'title'> }) {
  const open = useOpenAt()
  return (
    <button type="button" className="lnk" onClick={() => (book.work_id != null ? open(`/work/${book.work_id}`, {}) : book.book ? open(`/discover/book/${book.book}`, {}) : undefined)}>
      {book.title}
    </button>
  )
}

/** "E-Book · Deutsch": what the wish is for. */
export function KindChip({ kind, language }: { kind: ReleaseKind; language: string }) {
  const { t, i18n } = useTranslation()
  const Icon = kind === 'ebook' ? BookIcon : AudioIcon
  const named = language ? languageName(language, i18n.language).replace(/^./, (letter) => letter.toLocaleUpperCase(i18n.language)) : t('acquire.anyLanguage')
  return (
    <span className={`chip k-${kind}`}>
      <Icon />
      {t(`procure.releases.kind.${kind}`)} · {named}
    </span>
  )
}
