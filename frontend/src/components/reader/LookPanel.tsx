/**
 * How a book looks, as the person sets it: the typeface, the size, the line height, the margins, the colours, justified text and
 * pages or a continuous text. Every choice applies at once and is kept with the account (`profile.reader_*`), so that the next
 * book, and the next device, look the same. A book with fixed pages (and a PDF) leaves out what does not apply to it.
 */
import { Minus, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { Profile } from '../../api/client'
import { LINES, SIZES, stepSize, type ReaderFlow, type ReaderFont, type ReaderMargin, type ReaderTheme } from '../../lib/readFrame'

export type LookChange = Partial<Pick<Profile, 'reader_font' | 'reader_size' | 'reader_line' | 'reader_margin' | 'reader_theme' | 'reader_justify' | 'reader_flow'>>

export type LookValues = {
  font: ReaderFont
  size: number
  line: number
  margin: ReaderMargin
  theme: ReaderTheme
  justify: boolean
  flow: ReaderFlow
}

function Choice<T extends string>({ label, value, options, onPick, test }: { label: string; value: T; options: { id: T; text: string }[]; onPick: (id: T) => void; test: string }) {
  return (
    <div className="rd-row" role="group" aria-label={label}>
      <span className="rd-label">{label}</span>
      <div className="seg small" data-testid={test}>
        {options.map((option) => (
          <button key={option.id} type="button" className={option.id === value ? 'on' : ''} aria-pressed={option.id === value} onClick={() => onPick(option.id)}>
            {option.text}
          </button>
        ))}
      </div>
    </div>
  )
}

export function LookPanel({ values, kind, onChange, onClose }: { values: LookValues; kind: 'text' | 'fixed' | 'pdf'; onChange: (change: LookChange) => void; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const lineAt = LINES.indexOf(values.line)
  const decimal = (hundredths: number) => (hundredths / 100).toLocaleString(i18n.language, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const step = (direction: 1 | -1) => onChange({ reader_size: stepSize(values.size, direction) })
  const stepLine = (direction: 1 | -1) => {
    const next = LINES[Math.min(LINES.length - 1, Math.max(0, (lineAt === -1 ? LINES.indexOf(150) : lineAt) + direction))]
    onChange({ reader_line: next })
  }
  const text = kind === 'text'
  return (
    <div className="rd-look" role="dialog" aria-label={t('reader.settings')} data-testid="look-panel">
      <div className="rd-look-head">
        <h2>{t('reader.settings')}</h2>
        <button type="button" className="rd-icon" onClick={onClose} aria-label={t('reader.close')}>
          ×
        </button>
      </div>
      {kind === 'fixed' && <p className="rd-note">{t('reader.look.fixedNote')}</p>}
      {kind === 'pdf' && <p className="rd-note">{t('reader.look.pdfNote')}</p>}
      {text && (
        <Choice
          label={t('reader.look.font')}
          value={values.font}
          test="look-font"
          options={(['fraunces', 'inter', 'publisher'] as const).map((id) => ({ id, text: t(`reader.look.fonts.${id}`) }))}
          onPick={(id) => onChange({ reader_font: id })}
        />
      )}
      {kind !== 'fixed' && (
        <div className="rd-row" role="group" aria-label={t('reader.look.size')}>
          <span className="rd-label">{t('reader.look.size')}</span>
          <div className="rd-step">
            <button type="button" className="rd-icon" onClick={() => step(-1)} disabled={values.size <= SIZES[0]} aria-label={t('reader.look.smaller')}>
              <Minus size={16} aria-hidden />
            </button>
            <output data-testid="look-size">{values.size} %</output>
            <button type="button" className="rd-icon" onClick={() => step(1)} disabled={values.size >= SIZES[SIZES.length - 1]} aria-label={t('reader.look.larger')}>
              <Plus size={16} aria-hidden />
            </button>
          </div>
        </div>
      )}
      {text && (
        <div className="rd-row" role="group" aria-label={t('reader.look.line')}>
          <span className="rd-label">{t('reader.look.line')}</span>
          <div className="rd-step">
            <button type="button" className="rd-icon" onClick={() => stepLine(-1)} disabled={values.line <= LINES[0]} aria-label={t('reader.look.tighter')}>
              <Minus size={16} aria-hidden />
            </button>
            <output data-testid="look-line">{decimal(values.line)}</output>
            <button type="button" className="rd-icon" onClick={() => stepLine(1)} disabled={values.line >= LINES[LINES.length - 1]} aria-label={t('reader.look.looser')}>
              <Plus size={16} aria-hidden />
            </button>
          </div>
        </div>
      )}
      {text && (
        <Choice
          label={t('reader.look.margin')}
          value={values.margin}
          test="look-margin"
          options={(['narrow', 'normal', 'wide'] as const).map((id) => ({ id, text: t(`reader.look.margins.${id}`) }))}
          onPick={(id) => onChange({ reader_margin: id })}
        />
      )}
      {kind !== 'fixed' && (
        <Choice
          label={t('reader.look.theme')}
          value={values.theme}
          test="look-theme"
          options={(['auto', 'light', 'sepia', 'dark'] as const).map((id) => ({ id, text: t(`reader.look.themes.${id}`) }))}
          onPick={(id) => onChange({ reader_theme: id })}
        />
      )}
      {text && (
        <label className="rd-row rd-switch" data-testid="look-justify">
          <span className="rd-label">{t('reader.look.justify')}</span>
          <input type="checkbox" checked={values.justify} onChange={(event) => onChange({ reader_justify: event.target.checked })} />
          <i aria-hidden />
        </label>
      )}
      {kind !== 'fixed' && (
        <Choice
          label={t('reader.look.flow')}
          value={values.flow}
          test="look-flow"
          options={(['paged', 'scroll'] as const).map((id) => ({ id, text: t(`reader.look.flows.${id}`) }))}
          onPick={(id) => onChange({ reader_flow: id })}
        />
      )}
      <p className="rd-note">{t('reader.look.saved')}</p>
    </div>
  )
}
