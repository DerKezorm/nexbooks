/**
 * The small pieces of the fetching settings, as the design draws them: a plaque with a dot for a state, a chip for a
 * kind or a number, the line that heads a section, and the card of an indexer or a program.
 */
import type { ReactNode } from 'react'

export type Tone = 'ok' | 'warn' | 'bad' | 'muted'

const PLAQUE: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  bad: 'bg-bad/10 text-bad',
  muted: 'border border-line text-muted',
}

/** "bereit", "pausiert bis 01:00", "Schlüssel abgelehnt", "aus": a state with a dot in the colour of its tone. */
export function Plaque({ tone, children, testid }: { tone: Tone; children: ReactNode; testid?: string }) {
  return (
    <span data-testid={testid} data-tone={tone} className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ${PLAQUE[tone]}`}>
      <i aria-hidden className="h-2 w-2 rounded-full bg-current" />
      {children}
    </span>
  )
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title} className="inline-flex items-center gap-1 rounded-full border border-line bg-sheet-2/60 px-2.5 py-0.5 text-xs font-semibold text-ink-2">
      {children}
    </span>
  )
}

/** The line above a group of cards: "Indexer", with what to know about the group below it. */
export function SectionLine({ title, text }: { title: string; text?: string }) {
  return (
    <div className="pt-2">
      <h2 className="font-display text-xl font-semibold">{title}</h2>
      {text && <p className="text-sm text-ink-2">{text}</p>}
    </div>
  )
}

/** A line of mono text: an address, a path. */
export function Code({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <code data-testid={testid} className="block rounded-md bg-ink/5 px-2 py-1 font-mono text-[0.78rem] leading-snug [overflow-wrap:anywhere]">
      {children}
    </code>
  )
}

/** The card of one indexer or program in a grid; `dim` for one that is switched off. */
export function ItemCard({ children, dim = false, testid }: { children: ReactNode; dim?: boolean; testid?: string }) {
  return (
    <article data-testid={testid} className={`card flex flex-col gap-3 p-4 ${dim ? 'opacity-70' : ''}`}>
      {children}
    </article>
  )
}

/** The grid the cards stand in. */
export function CardGrid({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{children}</div>
}
