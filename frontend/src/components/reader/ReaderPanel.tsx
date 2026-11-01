/**
 * The side panel of the reader: the contents, the bookmarks and the search in the book. It slides in over the page from the
 * left, stays out of the way of the text, and closes with its button, Escape, or when something in it is chosen on a phone.
 */
import { ChevronDown, ChevronUp, Search, Trash2, X } from 'lucide-react'
import { useEffect, useRef, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import type { BookmarkOut } from '../../api/client'
import { daysAgo } from '../../lib/ago'
import { trapTab } from '../../lib/focusTrap'
import type { TocLine } from '../../lib/readFrame'
import type { Found } from './useBookFrame'

export type Tab = 'contents' | 'bookmarks' | 'search'

type Props = {
  tab: Tab
  onTab: (tab: Tab) => void
  toc: TocLine[]
  currentToc: number
  onToc: (id: number) => void
  bookmarks: BookmarkOut[]
  onBookmark: (bookmark: BookmarkOut) => void
  onDeleteBookmark: (bookmark: BookmarkOut) => void
  query: string
  onQuery: (query: string) => void
  found: Found[]
  searching: boolean
  searchProgress: number
  searchDone: { total: number; more: boolean } | null
  count: { current: number; total: number } | null
  pdf: boolean
  onSearch: (query: string) => void
  onStep: (direction: 'next' | 'prev') => void
  onClear: () => void
  onHit: (cfi: string) => void
  onClose: () => void
}

export function ReaderPanel(props: Props) {
  const { t, i18n } = useTranslation()
  const panel = useRef<HTMLElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const current = useRef<HTMLButtonElement>(null)

  // The focus goes into the panel; the Tab key stays in it.
  useEffect(() => {
    const element = panel.current
    if (!element) return
    const key = (event: KeyboardEvent) => void trapTab(event, element)
    element.addEventListener('keydown', key)
    return () => element.removeEventListener('keydown', key)
  }, [])
  useEffect(() => {
    if (props.tab === 'search') input.current?.focus()
    else current.current?.scrollIntoView({ block: 'center' })
  }, [props.tab])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    props.onSearch(props.query)
  }
  const tabs: { id: Tab; label: string }[] = [
    { id: 'contents', label: t('reader.contents') },
    { id: 'bookmarks', label: t('reader.bookmarks') },
    { id: 'search', label: t('reader.search') },
  ]
  const total = props.found.reduce((sum, group) => sum + group.hits.length, 0)

  return (
    <aside ref={panel} className="rd-panel" role="dialog" aria-label={t(`reader.${props.tab}`)} data-testid="reader-panel" data-tab={props.tab}>
      <div className="rd-panel-head">
        <div className="seg small" role="tablist">
          {tabs.map((each) => (
            <button key={each.id} type="button" role="tab" aria-selected={each.id === props.tab} className={each.id === props.tab ? 'on' : ''} onClick={() => props.onTab(each.id)} data-testid={`tab-${each.id}`}>
              {each.label}
            </button>
          ))}
        </div>
        <button type="button" className="rd-icon" onClick={props.onClose} aria-label={t('reader.close')} data-testid="panel-close">
          <X size={18} aria-hidden />
        </button>
      </div>

      {props.tab === 'contents' && (
        <nav className="rd-list" aria-label={t('reader.contentsOf')} data-testid="toc">
          {props.toc.length === 0 && <p className="rd-empty">{t('reader.noContents')}</p>}
          {props.toc.map((line) => (
            <button key={line.id} ref={line.id === props.currentToc ? current : undefined} type="button" className={`rd-toc ${line.id === props.currentToc ? 'on' : ''}`} style={{ paddingInlineStart: `${12 + line.depth * 16}px` }} aria-current={line.id === props.currentToc ? 'location' : undefined} onClick={() => props.onToc(line.id)} dir="auto">
              {line.label || '·'}
            </button>
          ))}
        </nav>
      )}

      {props.tab === 'bookmarks' && (
        <div className="rd-list" data-testid="bookmark-list">
          {props.bookmarks.length === 0 && <p className="rd-empty">{t('reader.noBookmarks')}</p>}
          {props.bookmarks.map((bookmark) => (
            <div key={bookmark.id} className="rd-mark">
              <button type="button" className="rd-mark-go" onClick={() => props.onBookmark(bookmark)} data-testid="bookmark">
                <b dir="auto">{bookmark.chapter_title || t('reader.bookmarkUnnamed')}</b>
                <span>
                  {t('reader.bookmarkAt', { percent: Math.round(bookmark.fraction * 100) })} · {daysAgo(bookmark.created_at, i18n.language)}
                </span>
              </button>
              <button type="button" className="rd-icon" onClick={() => props.onDeleteBookmark(bookmark)} aria-label={t('reader.bookmarkDelete')} data-testid="bookmark-delete">
                <Trash2 size={16} aria-hidden />
              </button>
            </div>
          ))}
        </div>
      )}

      {props.tab === 'search' && (
        <div className="rd-search">
          <form className="rd-field" onSubmit={submit} role="search">
            <Search size={16} aria-hidden />
            <input ref={input} value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder={t('reader.searchPlaceholder')} aria-label={t('reader.searchPlaceholder')} maxLength={200} data-testid="search-input" enterKeyHint="search" />
            {props.query && (
              <button type="button" className="rd-icon" aria-label={t('reader.searchClear')} onClick={() => { props.onQuery(''); props.onClear() }}>
                <X size={14} aria-hidden />
              </button>
            )}
          </form>
          {props.pdf ? (
            <div className="rd-count" role="status" data-testid="search-count">
              {props.count && props.count.total > 0 ? t('reader.searchOf', { current: props.count.current, total: props.count.total }) : props.count ? t('reader.noHits') : ''}
              {props.count && props.count.total > 0 && (
                <span className="rd-pair">
                  <button type="button" className="rd-icon" onClick={() => props.onStep('prev')} aria-label={t('reader.searchPrev')}>
                    <ChevronUp size={16} aria-hidden />
                  </button>
                  <button type="button" className="rd-icon" onClick={() => props.onStep('next')} aria-label={t('reader.searchNext')}>
                    <ChevronDown size={16} aria-hidden />
                  </button>
                </span>
              )}
            </div>
          ) : (
            <div className="rd-list" data-testid="search-results">
              {props.searching && (
                <p className="rd-empty" role="status">
                  {t('reader.searching')}
                  <span className="rd-bar" aria-hidden>
                    <i style={{ width: `${Math.round(props.searchProgress * 100)}%` }} />
                  </span>
                </p>
              )}
              {!props.searching && props.searchDone && (
                <p className="rd-empty" role="status" data-testid="search-summary">
                  {props.searchDone.total === 0 ? t('reader.noHits') : t('reader.hits', { count: props.searchDone.total })}
                  {props.searchDone.more ? ` ${t('reader.moreHits')}` : ''}
                </p>
              )}
              {props.found.map((group, index) => (
                <section key={index} className="rd-group">
                  {group.label && <h3 dir="auto">{group.label}</h3>}
                  {group.hits.map((hit) => (
                    <button key={hit.cfi} type="button" className="rd-hit" onClick={() => props.onHit(hit.cfi)} data-testid="hit" dir="auto">
                      <span>{hit.pre}</span>
                      <mark>{hit.match}</mark>
                      <span>{hit.post}</span>
                    </button>
                  ))}
                </section>
              ))}
              {total === 0 && !props.searching && !props.searchDone && props.query.trim().length === 1 && <p className="rd-empty">{t('reader.searchTooShort')}</p>}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}
