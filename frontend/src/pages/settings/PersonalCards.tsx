/**
 * What is personal, as cards for "My account": the language, and light or dark and the look of the library. Each is
 * kept with the account (never only in this browser).
 */
import { Globe, Monitor, Moon, Palette, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, type LibraryView, type Profile } from '../../api/client'
import { changeLanguage, languageOptions, type LanguageOption } from '../../i18n'
import { applyMode, storedMode, type Mode } from '../../lib/theme'
import { useAuth } from '../../state/auth'
import { Card, Feedback, Segment, SubHead, useAction } from './ui'

export function LanguageCard() {
  const { t, i18n } = useTranslation()
  const { setMe } = useAuth()
  const [options, setOptions] = useState<LanguageOption[]>([])
  useEffect(() => {
    let alive = true
    void languageOptions().then((list) => alive && setOptions(list))
    return () => {
      alive = false
    }
  }, [])
  return (
    <Card icon={Globe} title={t('settings.language.title')} text={t('settings.language.text')}>
      <Segment
        label={t('settings.language.title')}
        value={i18n.language}
        onChange={(code) => {
          void changeLanguage(code)
          // Kept with the account, so the next browser speaks it too.
          void authApi.language(code).then(setMe, () => undefined)
        }}
        options={options.map((option) => ({ value: option.code, label: option.name }))}
      />
    </Card>
  )
}

/** Light, dark or as the system, and how the library shows the books; both kept with the account. */
export function LooksCard() {
  const { t } = useTranslation()
  const { me, setMe } = useAuth()
  const [mode, setMode] = useState<Mode>(me?.profile.theme ?? storedMode())
  const action = useAction()
  const save = (change: Partial<Profile>) => {
    void action.run(async () => {
      const profile = await authApi.preferences(change)
      if (me) setMe({ ...me, profile })
    })
  }
  const modes: { value: Mode; label: string; icon: typeof Sun }[] = [
    { value: 'system', label: t('theme.system'), icon: Monitor },
    { value: 'light', label: t('theme.light'), icon: Sun },
    { value: 'dark', label: t('theme.dark'), icon: Moon },
  ]
  return (
    <Card icon={Palette} title={t('looks.title')} text={t('looks.text')}>
      <div role="radiogroup" aria-label={t('looks.title')} className="grid gap-3 sm:grid-cols-3">
        {modes.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={mode === value}
            onClick={() => {
              setMode(value)
              applyMode(value)
              save({ theme: value })
            }}
            className={`flex items-center gap-3 rounded-2xl border p-4 text-left text-sm font-semibold ${mode === value ? 'border-accent/50 bg-accent-soft text-accent' : 'border-line text-ink-2 hover:text-ink'}`}
          >
            <Icon size={18} aria-hidden /> {label}
          </button>
        ))}
      </div>
      <SubHead title={t('looks.library')} text={t('looks.libraryText')} />
      <Segment
        label={t('looks.library')}
        value={me?.profile.view ?? 'shelf'}
        onChange={(view: LibraryView) => save({ view })}
        options={[
          { value: 'shelf', label: t('looks.shelf') },
          { value: 'grid', label: t('looks.grid') },
        ]}
      />
      <Feedback problem={action.problem} />
    </Card>
  )
}
