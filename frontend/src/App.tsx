import type { ReactNode } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation, type Location } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { PlayerChrome } from './components/player/PlayerChrome'
import { AboutPage } from './pages/AboutPage'
import { AcquirePage } from './pages/AcquirePage'
import { AccountPage } from './pages/AccountPage'
import { ForgotPage, InvitePage, LoginPage, MailConfirmPage, ResetPage, SetupPage } from './pages/AuthPages'
import { DiscoverPage } from './pages/DiscoverPage'
import { ShelfPage } from './pages/ShelfPage'
import { DiscoverSearchPage } from './pages/DiscoverSearch'
import { ForeignPage } from './pages/ForeignPage'
import { LibraryPage } from './pages/LibraryPage'
import { PersonPage } from './pages/PersonPage'
import { ReaderPage } from './pages/ReaderPage'
import { ReviewPage } from './pages/ReviewPage'
import { SettingsPage } from './pages/SettingsPage'
import { StackPage } from './pages/StackPage'
import { WorkPage } from './pages/WorkPage'
import { useAuth } from './state/auth'
import { PlayerProvider } from './state/player'
import { ProcureProvider } from './state/procure'
import { WishProvider } from './state/wish'

/** Everything behind the sign-in: without an account the page goes to the sign-in, and comes back after. An account
 * that still has to set up its second factor (the operator requires one) reaches its own account page only. */
function SignedIn({ children }: { children: ReactNode }) {
  const { status, me } = useAuth()
  const location = useLocation()
  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status === 'signedOut') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  // Right after the password, the sign-in page sets the second factor up before anything else opens.
  if (me?.session_stage && me.session_stage !== 'full') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  if (me?.second_factor_setup_required && location.pathname !== '/account') return <Navigate to="/account?tab=security" replace />
  return children
}

export default function App() {
  const location = useLocation()
  // A book opened from a page lies over that page: the page stays mounted (and where it was scrolled) under it, and the
  // page of the book is drawn by the second set of routes. A book opened by its address has no page under it.
  const background = (location.state as { background?: Location } | null)?.background
  return (
    // The player stands above the pages: what plays goes on while the person moves between them.
    <PlayerProvider>
      <WishProvider>
      <ProcureProvider>
      <Routes location={background ?? location}>
      <Route path="setup" element={<SetupPage />} />
      <Route path="login" element={<LoginPage />} />
      <Route path="invite/:token" element={<InvitePage />} />
      <Route path="forgot" element={<ForgotPage />} />
      <Route path="reset/:token" element={<ResetPage />} />
      <Route path="mail/:token" element={<MailConfirmPage />} />
      <Route
        element={
          <SignedIn>
            <Outlet />
          </SignedIn>
        }
      >
        {/* The reader fills the screen: no head of the app around it. */}
        <Route path="read/:editionId" element={<ReaderPage />} />
        <Route element={<AppShell />}>
          <Route index element={<LibraryPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="discover/shelf/:key" element={<ShelfPage />} />
          <Route path="discover/search" element={<DiscoverSearchPage />} />
          <Route path="stack" element={<StackPage />} />
          <Route path="acquire" element={<AcquirePage />} />
          <Route path="work/:id" element={<WorkPage />} />
          <Route path="discover/book/:token" element={<ForeignPage />} />
          <Route path="person/:id" element={<PersonPage />} />
          <Route path="author/:token" element={<PersonPage />} />
          <Route path="review" element={<ReviewPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="about" element={<AboutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
      </Routes>
      {background && (
        <Routes>
          <Route path="work/:id" element={<WorkPage />} />
          <Route path="discover/book/:token" element={<ForeignPage />} />
        </Routes>
      )}
      <PlayerChrome />
      </ProcureProvider>
      </WishProvider>
    </PlayerProvider>
  )
}
