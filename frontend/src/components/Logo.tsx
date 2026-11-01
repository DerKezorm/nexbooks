// nexbooks mark: three book spines on a shelf, the third one tilted, drawn as lines in Bordeaux on a white tile. The mark
// stays the same in every theme; the wordmark's second half follows the accent.
const BORDEAUX = '#8e3b46'

export function LogoMark({ size = 36, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={`mark ${className}`.trim()} aria-hidden="true">
      <rect x="2" y="2" width="60" height="60" rx="16" fill="#ffffff" />
      <rect x="2" y="2" width="60" height="60" rx="16" fill="none" stroke={BORDEAUX} strokeWidth="2.5" />
      <g fill="none" stroke={BORDEAUX} strokeWidth="2.8" strokeLinejoin="round">
        <rect x="13" y="20" width="9" height="30" rx="1.5" />
        <rect x="25" y="13" width="11" height="37" rx="1.5" />
        <rect x="40" y="18" width="9" height="32" rx="1.5" transform="rotate(12 44.5 50)" />
      </g>
      <g stroke={BORDEAUX} strokeWidth="2.4" strokeLinecap="round">
        <path d="M27.5 19h6M27.5 23h6M27.5 44h6" />
        <path d="M15.5 26h4M15.5 44h4" />
      </g>
      <path d="M10 52.5h44" stroke={BORDEAUX} strokeWidth="2.8" strokeLinecap="round" />
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
