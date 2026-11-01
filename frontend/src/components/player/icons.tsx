/** The jump buttons of the player: a round arrow with the number of seconds in it, the way players draw them. */
export function JumpIcon({ dir, seconds, size = 26 }: { dir: 'back' | 'ahead'; seconds: number; size?: number }) {
  const back = dir === 'back'
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="jump-icon">
      <g transform={back ? undefined : 'translate(32 0) scale(-1 1)'}>
        <path d="M8.5 9.5A10.5 10.5 0 1 1 5.6 17" />
        <path d="M8.6 4.6v5h5" />
      </g>
      <text x="16.5" y="20.6" textAnchor="middle" fontSize={seconds >= 100 ? 7.5 : 9} fontWeight="700" fill="currentColor" stroke="none" fontFamily="Inter Variable, system-ui, sans-serif">
        {seconds}
      </text>
    </svg>
  )
}

/** A crescent moon: the sleep timer. */
export function MoonIcon({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />
    </svg>
  )
}
