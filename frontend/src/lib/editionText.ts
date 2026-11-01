/** The words a card of an edition says about it (what it is, how long or in what format), and which editions are the twins. */
import { useTranslation } from 'react-i18next'

import type { EditionOut, WorkOut } from '../api/client'
import { durationParts, formatsLabel, languageName } from './books'

export function useEditionLabel() {
  const { t, i18n } = useTranslation()
  const duration = (seconds: number) => {
    const { h, m } = durationParts(seconds)
    return h ? t('work.duration.hours', { h, m }) : t('work.duration.minutes', { m })
  }
  return {
    duration,
    label(edition: EditionOut): { icon: 'book' | 'audio'; main: string; sub: string } {
      if (edition.kind === 'ebook') {
        const pages = edition.pages ? t('work.twin.pages', { count: edition.pages }) : ''
        const sub = [formatsLabel(edition), pages].filter(Boolean).join(' · ')
        const language = languageName(edition.language, i18n.language)
        return { icon: 'book', main: language ? t('work.twin.ebook', { language }) : t('work.twin.noLanguage'), sub }
      }
      const variant = t(`work.variant.${edition.variant || 'unabridged'}`)
      const who = edition.narrators.join(', ')
      const guess = edition.language_guess && edition.language ? t('work.twin.languageGuess', { language: languageName(edition.language, i18n.language) }) : ''
      const sub = [duration(edition.duration), who ? (edition.variant === 'dramatized' ? who : t('work.twin.readBy', { name: who })) : '', guess].filter(Boolean).join(' · ')
      // A reading nothing proves is not named: the card says "Hörbuch" and, below, that the reading is not proven.
      return { icon: 'audio', main: edition.variant_uncertain ? t('work.twin.audioPlain') : t('work.twin.audio', { variant }), sub }
    },
  }
}

/** The e-book (the first one) and the recording (the unabridged one if there is one, else the first) of a work; the rest. */
export function leadEditions(work: Pick<WorkOut, 'editions'>): { ebook?: EditionOut; audio?: EditionOut; others: EditionOut[] } {
  const ebook = work.editions.find((edition) => edition.kind === 'ebook')
  const audio = work.editions.find((edition) => edition.kind === 'audio' && edition.variant === 'unabridged') ?? work.editions.find((edition) => edition.kind === 'audio')
  return { ebook, audio, others: work.editions.filter((edition) => edition !== ebook && edition !== audio) }
}
