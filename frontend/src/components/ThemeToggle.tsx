import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '../api/client'
import { applyMode, storedTheme, type Theme } from '../lib/theme'
import { useAuth } from '../state/auth'

/**
 * The round button of the head: light or dark. It shows the theme that is on (sun by day, moon by night), a press
 * switches to the other and keeps the choice in the account (`profile.theme`) and in this browser.
 */
export function ThemeToggle() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [theme, setTheme] = useState<Theme>(storedTheme())
  const account = me?.profile.theme
  useEffect(() => setTheme(storedTheme()), [account])
  useEffect(() => {
    try {
      const query = window.matchMedia('(prefers-color-scheme: dark)')
      const changed = () => setTheme(storedTheme())
      query.addEventListener('change', changed)
      return () => query.removeEventListener('change', changed)
    } catch {
      return undefined
    }
  }, [])
  const Icon = theme === 'dark' ? Moon : Sun
  return (
    <button
      type="button"
      className="icon-btn"
      title={t('theme.toggle')}
      aria-label={t('theme.toggle')}
      onClick={() => {
        const next: Theme = theme === 'dark' ? 'light' : 'dark'
        applyMode(next)
        setTheme(next)
        if (me) void authApi.preferences({ theme: next }).catch(() => undefined)
      }}
    >
      <Icon aria-hidden strokeWidth={1.8} />
    </button>
  )
}
