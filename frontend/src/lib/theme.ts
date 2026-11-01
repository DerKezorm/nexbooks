/**
 * Light or dark, or as the system is set. It belongs to the account (`profile.theme`, kept on the server); this browser
 * keeps a copy so that `public/theme.js` can paint the right one before the page has asked the server. It is only a
 * preview: whatever the account says wins once it is known. The colours behind it live solely in index.css (the same
 * as the design); this only holds which mode is active. A mode that is not "system" sets `data-theme` on the page,
 * "system" removes it and leaves the choice to `prefers-color-scheme`.
 */

export type Theme = 'dark' | 'light'
export type Mode = Theme | 'system'
const KEY = 'nexbooks.theme'
const GROUND: Record<Theme, string> = { light: '#f3ede3', dark: '#12100e' }

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function isMode(value: unknown): value is Mode {
  return value === 'system' || value === 'light' || value === 'dark'
}

/** The mode chosen in this browser; nothing chosen yet means the system's. */
export function storedMode(): Mode {
  try {
    const stored = localStorage.getItem(KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

/** The mode as it shows now: the system's when that was chosen. */
export function storedTheme(): Theme {
  const mode = storedMode()
  return mode === 'system' ? systemTheme() : mode
}

function paint(mode: Mode): void {
  const root = document.documentElement
  if (mode === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', mode)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', GROUND[mode === 'system' ? systemTheme() : mode])
}

export function applyMode(mode: Mode): void {
  paint(mode)
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    // Then the choice only holds until the next reload.
  }
}

/** While the mode is the system's, the colour of the browser's bar changes along when the system does. Once, at the start. */
export function followSystem(): void {
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (storedMode() === 'system') paint('system')
    })
  } catch {
    // An old browser: the mode stays as it was at the start.
  }
}
