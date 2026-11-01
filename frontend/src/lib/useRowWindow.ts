/**
 * The rows of a long table that fit on the screen: the table is of fixed row heights, only the rows near the screen are
 * drawn and the rest is a space of the right height, so that a list of 2,000 rows is no more weight than one of 20. The
 * phone (a window of 820 px or less) has its own, higher row.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { windowRange } from './listing'

export function useRowWindow(count: number, desktop: number, phone: number) {
  const table = useRef<HTMLTableElement>(null)
  const [isPhone, setPhone] = useState(() => window.innerWidth <= 820)
  const row = isPhone ? phone : desktop
  const [view, setView] = useState({ top: 0, height: window.innerHeight })

  useLayoutEffect(() => {
    const measure = () => {
      const box = table.current
      const start = box ? box.getBoundingClientRect().top + window.scrollY : 0
      setView({ top: Math.max(0, window.scrollY - start), height: window.innerHeight })
      setPhone(window.innerWidth <= 820)
    }
    measure()
    let frame = 0
    const later = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    }
    window.addEventListener('scroll', later, { passive: true })
    window.addEventListener('resize', later)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', later)
      window.removeEventListener('resize', later)
    }
  }, [count])
  useEffect(() => {
    setView((now) => ({ ...now }))
  }, [row])

  return { table, phone: isPhone, row, ...windowRange(count, row, view.top, view.height) }
}
