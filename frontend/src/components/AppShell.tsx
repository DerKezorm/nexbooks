import { Settings } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import type { NoticeLink } from '../lib/say'
import { AccountMenu } from './AccountMenu'
import { LogoMark } from './Logo'
import { SearchBox } from './SearchBox'
import { useAuth } from '../state/auth'
import { useProcure } from '../state/procure'
import { ThemeToggle } from './ThemeToggle'
import { WhatsNewBanner } from './WhatsNew'

/** A short sentence after something was done ("Gespeichert."), handed over by the page that did it as `notice` in the
 * navigation state; shown for a moment, then gone, and not again after a reload. */
function Toast() {
  const location = useLocation()
  const navigate = useNavigate()
  const [shown, setShown] = useState<{ text: string; link?: NoticeLink } | null>(null)
  const state = location.state as { notice?: unknown; link?: unknown } | null
  const notice = state?.notice
  const link = state?.link as NoticeLink | undefined
  useEffect(() => {
    if (typeof notice !== 'string') return
    setShown({ text: notice, ...(link && typeof link.to === 'string' && typeof link.label === 'string' ? { link } : {}) })
    navigate(location.pathname + location.search, { replace: true, state: null })
  }, [notice, link, navigate, location.pathname, location.search])
  useEffect(() => {
    if (!shown) return
    // A sentence with a way on stays longer: there is something to press.
    const timer = window.setTimeout(() => setShown(null), shown.link ? 8000 : 3500)
    return () => window.clearTimeout(timer)
  }, [shown])
  if (!shown) return null
  return (
    <div role="status" className="rise fixed inset-x-0 z-40 flex justify-center px-4" style={{ bottom: 'calc(2rem + var(--player-space, 0px))' }}>
      <p className="rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-paper shadow-soft">
        {shown.text}
        {shown.link && (
          <>
            {' '}
            <Link to={shown.link.to} className="underline underline-offset-2" onClick={() => setShown(null)}>
              {shown.link.label}
            </Link>
          </>
        )}
      </p>
    </div>
  )
}

/** The three areas of the head, in their order. */
const AREAS = [
  { to: '/', label: 'app.library', end: true },
  { to: '/discover', label: 'app.discover', end: false },
  { to: '/stack', label: 'app.stack', end: false },
  // Only for who may ask for books in a library; the number is what runs and what needs somebody.
  { to: '/acquire', label: 'app.acquire', end: false },
]

/**
 * The frame of the design: the head with the mark, the three areas, the button for light and dark and the account
 * menu, and below it the view. The search field of the head stands between the areas and the buttons.
 */
export function AppShell() {
  const { t } = useTranslation()
  const procure = useProcure()
  const { me } = useAuth()
  const place = useLocation()
  const busy = (procure.counts?.running ?? 0) + (procure.counts?.needs ?? 0)
  return (
    <>
      <header className="top">
        <Link className="brand" to="/" aria-label="nexbooks">
          <LogoMark size={34} />
          <span className="wordmark">
            NEX<span>BOOKS</span>
          </span>
        </Link>
        <nav className="tabs" aria-label={t('app.areas')}>
          {AREAS.filter(({ to }) => to !== '/acquire' || procure.allowed).map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? 'on' : undefined)}>
              {t(label)}
              {to === '/acquire' && busy > 0 && (
                <span className="wishcount" id="bescount" data-testid="bescount">
                  {busy}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <SearchBox />
        <div className="top-end">
          {/* The cog of the design, for the operator: the settings of fetching books (indexers, programs, formats). */}
          {me?.role === 'operator' && (
            <Link to="/settings?tab=procure" id="gear" className={`icon-btn ${place.pathname.startsWith('/settings') ? 'on' : ''}`} title={t('settings.title')} aria-label={t('settings.title')}>
              <Settings aria-hidden strokeWidth={1.8} />
            </Link>
          )}
          <ThemeToggle />
          <AccountMenu />
        </div>
      </header>
      <main>
        <WhatsNewBanner />
        <Outlet />
      </main>
      <Toast />
    </>
  )
}
