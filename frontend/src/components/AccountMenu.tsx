import { Info, LogOut, Settings, User } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../state/auth'
import { Avatar } from './Avatar'

/**
 * The person's menu: own account, settings, about, sign out. The round picture in the head, the size of
 * the other button there, opening downwards.
 */
export function AccountMenu() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { me, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const { pathname } = useLocation()

  useEffect(() => setOpen(false), [pathname])
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  if (!me) return null
  const item = 'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm font-semibold text-ink-2 hover:bg-sheet-2 hover:text-ink'
  const shown = me.display_name || me.name

  return (
    <div ref={box} className="relative">
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={t('account.menu', { name: shown })}
        className="icon-btn round"
      >
        <Avatar person={me} size={40} />
      </button>
      {open && (
        <div className="card rise absolute top-full right-0 z-50 mt-2 w-56 p-1.5 shadow-soft">
          <Link to="/account" className={item}>
            <User size={16} /> {t('account.mine')}
          </Link>
          {me.role === 'operator' && (
            <Link to="/settings" className={item}>
              <Settings size={16} /> {t('settings.title')}
            </Link>
          )}
          <Link to="/about" className={item}>
            <Info size={16} /> {t('about.menu')}
          </Link>
          <div className="my-1 h-px bg-line" />
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              void signOut().then(() => navigate('/login', { replace: true }))
            }}
            className={item}
          >
            <LogOut size={16} /> {t('account.signOut')}
          </button>
        </div>
      )}
    </div>
  )
}
