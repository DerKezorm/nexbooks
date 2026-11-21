// nexbooks mark: a shelf with three book spines, the middle one filled, the third one leaning. The source of every
// version is nexapps/icons-vergleich/neu.py; the same shape is in public/logo.svg (favicon) and scripts/app-icons.mjs
// (home screen). The colours come from index.css (--nb-logo-…) and follow the theme: a white tile in light mode, a
// dark one in dark mode.
export function LogoMark({ size = 36, className = '' }: { size?: number; className?: string }) {
  const verlauf = 'url(#nexbooks-mark)'
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={`mark ${className}`.trim()} aria-hidden="true">
      <defs>
        <linearGradient id="nexbooks-mark" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
          <stop offset="0" style={{ stopColor: 'var(--nb-logo-verlauf-1)' }} />
          <stop offset=".5" style={{ stopColor: 'var(--nb-logo-verlauf-2)' }} />
          <stop offset="1" style={{ stopColor: 'var(--nb-logo-verlauf-3)' }} />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" style={{ fill: 'var(--nb-logo-grund)' }} />
      <rect
        x="2.75"
        y="2.75"
        width="58.5"
        height="58.5"
        rx="15.3"
        fill="none"
        strokeWidth="1.5"
        style={{ stroke: 'var(--nb-logo-rand)', strokeOpacity: 'var(--nb-logo-rand-deckung)' }}
      />
      <rect x="14" y="20" width="9.5" height="29" rx="1.8" fill="none" stroke={verlauf} strokeWidth="3.8" />
      <rect
        x="26.5"
        y="13.5"
        width="11"
        height="35.5"
        rx="1.8"
        strokeWidth="3.8"
        strokeLinejoin="round"
        style={{ fill: 'var(--nb-logo-akzent)', stroke: 'var(--nb-logo-akzent)' }}
      />
      <path d="M29 20.5h6M29 41.5h6" strokeWidth="2.6" strokeLinecap="round" style={{ stroke: 'var(--nb-logo-grund)' }} />
      <rect
        x="41"
        y="19"
        width="9"
        height="30"
        rx="1.8"
        fill="none"
        stroke={verlauf}
        strokeWidth="3.8"
        transform="rotate(13 45.5 49)"
      />
      <path d="M10.5 52.5h43" stroke={verlauf} strokeWidth="4.2" strokeLinecap="round" />
    </svg>
  )
}

export function Wordmark({ size = 34 }: { size?: number }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="wordmark">
        NEX<span>BOOKS</span>
      </span>
    </span>
  )
}
