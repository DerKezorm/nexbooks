import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

/** The dialogs that are open, the last one on top: only that one answers to Escape (a "really?" over a dialog must not
 * close the dialog under it as well). */
const open: symbol[] = []

/** Whether a control can be seen (a hidden one cannot take the focus): `checkVisibility` is missing in some old browsers and in the test's DOM, where everything counts as seen. */
const seen = (item: HTMLElement) => item.closest('[hidden], [inert]') === null && (typeof item.checkVisibility !== 'function' || item.checkVisibility())

/** What the keyboard can reach inside a dialog. */
const FOCUSABLE = 'a[href], button:not(:disabled), input:not([type="hidden"]):not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])'

/** A dialog over the page: from below on a phone, in the middle on a larger screen. Escape and the scrim
 * close it, focus starts inside and comes back afterwards. `broad`: the wide form of the fetching settings (up to
 * 1040 px, the form in two columns). `above`: over everything, also over the big view of a photo
 * (drawn into the body, outside the page behind, which stands still while that view is open). */
export function Dialog({ title, sub, className = '', onClose, children, wide = false, broad = false, above = false }: { title: string; sub?: string; className?: string; onClose: () => void; children: ReactNode; wide?: boolean; broad?: boolean; above?: boolean }) {
  const { t } = useTranslation()
  const panel = useRef<HTMLDivElement>(null)
  // The latest way to close, kept apart from the effect: the dialog starts once, however often its page draws it anew.
  const closer = useRef(onClose)
  closer.current = onClose
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    const me = Symbol('dialog')
    open.push(me)
    // The small player at the bottom steps aside while a dialog is open (`player.css`).
    document.body.classList.add('has-dialog')
    const first = panel.current?.querySelector<HTMLElement>('[data-autofocus]') ?? panel.current?.querySelector<HTMLElement>('input, textarea, select, button:not([data-close])')
    first?.focus()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open[open.length - 1] === me) {
        e.stopPropagation()
        closer.current()
      }
    }
    window.addEventListener('keydown', key, true)
    const box = panel.current
    // Focus stays inside while the dialog is the top one: Tab at the last control goes to the first, Shift+Tab the other
    // way round, and Tab from outside (a click on the scrim's margin) comes back in. A key held down does not press a
    // button again and again: the button the focus lands on after a step ("Next" then "Connect") would be pressed by the
    // repeat of the Enter that made the step.
    const keep = (e: KeyboardEvent) => {
      if (open[open.length - 1] !== me || !box) return
      const target = e.target as HTMLElement | null
      if (e.repeat && (e.key === 'Enter' || e.key === ' ') && target instanceof HTMLButtonElement) {
        e.preventDefault()
        return
      }
      if (e.key !== 'Tab') return
      const items = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(seen)
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const now = document.activeElement as HTMLElement | null
      const at = now ? items.indexOf(now) : -1
      if (at === -1) {
        e.preventDefault()
        items[e.shiftKey ? items.length - 1 : 0].focus()
      } else if (e.shiftKey && at === 0) {
        e.preventDefault()
        items[items.length - 1].focus()
      } else if (!e.shiftKey && at === items.length - 1) {
        e.preventDefault()
        items[0].focus()
      }
    }
    window.addEventListener('keydown', keep, true)
    // A step of a dialog that takes away the control the focus was on ("Next" in a form, a field that goes, a button that
    // turns into another) must not leave the focus on the page: it goes to the next field, else to the main button, else
    // to the first button. Not while that control only waits (a button that is busy is disabled and loses the focus, which
    // comes back to nothing else), and not after the person clicked on something that cannot take the focus.
    let lastFocused: Element | null = box?.contains(document.activeElement) ? document.activeElement : null
    const noted = (e: FocusEvent) => {
      lastFocused = e.target as Element
    }
    const clicked = () => {
      lastFocused = null
    }
    box?.addEventListener('focusin', noted)
    box?.addEventListener('pointerdown', clicked, true)
    const watcher = new MutationObserver(() => {
      const active = document.activeElement
      if (!lastFocused || (active && active !== document.body) || open[open.length - 1] !== me) return
      if (lastFocused.isConnected && (lastFocused as HTMLButtonElement).disabled) return
      lastFocused = null
      const next =
        box?.querySelector<HTMLElement>('[data-autofocus]') ??
        box?.querySelector<HTMLElement>('input:not([type="hidden"]), textarea, select') ??
        box?.querySelector<HTMLElement>('button[data-primary]:not(:disabled)') ??
        box?.querySelector<HTMLElement>('button:not([data-close]):not(:disabled)')
      next?.focus()
    })
    if (box) watcher.observe(box, { childList: true, subtree: true })
    return () => {
      window.removeEventListener('keydown', key, true)
      window.removeEventListener('keydown', keep, true)
      box?.removeEventListener('focusin', noted)
      box?.removeEventListener('pointerdown', clicked, true)
      watcher.disconnect()
      open.splice(open.indexOf(me), 1)
      if (open.length === 0) document.body.classList.remove('has-dialog')
      before?.focus?.()
    }
  }, [])
  const dialog = (
    <div className={`fixed inset-0 ${above ? 'z-[70]' : 'z-50'} flex items-end justify-center bg-scrim p-0 sm:items-center sm:p-6`} onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} role="dialog" aria-modal="true" aria-label={title} className={`card rise max-h-[92dvh] w-full overflow-auto rounded-b-none p-6 sm:rounded-b-[1.25rem] ${broad ? 'max-w-[1040px]' : wide ? 'max-w-3xl' : 'max-w-lg'} ${className}`}>
        <div className={`mb-4 flex ${sub ? 'items-start' : 'items-center'} justify-between gap-3`}>
          <div className="min-w-0">
            <h2 className="font-display text-xl font-semibold">{title}</h2>
            {sub && (
              <p className="mt-[3px] text-[13.5px] text-muted" dir="auto">
                {sub}
              </p>
            )}
          </div>
          <button type="button" data-close onClick={onClose} className="rounded-full p-2 text-muted hover:bg-sheet-2" aria-label={t('common.close')}>
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
  return above ? createPortal(dialog, document.body) : dialog
}
