/** The few drawings of the shelf and the page of a book, with the paths of the design. */
import type { SVGProps } from 'react'

function Svg({ children, width, strokeWidth = 2, ...rest }: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width={width} fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...rest}>
      {children}
    </svg>
  )
}

export function BackIcon() {
  return (
    <Svg width={17}>
      <path d="M15 18l-6-6 6-6" />
    </Svg>
  )
}

export function DownloadIcon({ width }: { width?: number }) {
  return (
    <Svg width={width}>
      <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
    </Svg>
  )
}

/** An arrow up out of a tray: bringing files in from this device. */
export function UploadIcon({ width }: { width?: number }) {
  return (
    <Svg width={width} strokeWidth={1.8}>
      <path d="M12 16V4M7 9l5-5 5 5M5 20h14" />
    </Svg>
  )
}

/** A book going from one shelf to another: moving an edition into another library. */
export function MoveIcon({ width }: { width?: number }) {
  return (
    <Svg width={width} strokeWidth={1.8}>
      <path d="M4 7h11M11 3l4 4-4 4M20 17H9M13 13l-4 4 4 4" />
    </Svg>
  )
}

export function SearchIcon() {
  return (
    <Svg strokeWidth={1.8}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </Svg>
  )
}

export function ShelfIcon() {
  return (
    <Svg strokeWidth={1.8}>
      <rect x="3" y="4" width="4" height="16" rx="1" />
      <rect x="8.5" y="6" width="4" height="14" rx="1" />
      <rect x="14" y="3" width="3.5" height="17" rx="1" />
      <path d="M18.5 7l2.5 13" />
    </Svg>
  )
}

export function GridIcon() {
  return (
    <Svg strokeWidth={1.8}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="9" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </Svg>
  )
}

export function ListIcon() {
  return (
    <Svg strokeWidth={1.8}>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </Svg>
  )
}

export function PencilIcon() {
  return (
    <Svg strokeWidth={1.8}>
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="M13.5 6.5l4 4" />
    </Svg>
  )
}

/** A bookmark: "Merken" / "Gemerkt". */
export function MarkIcon({ width }: { width?: number }) {
  return (
    <Svg width={width}>
      <path d="M6 3h12v18l-6-4-6 4z" />
    </Svg>
  )
}

/** A tick: "Im Regal". */
export function CheckIcon() {
  return (
    <Svg strokeWidth={2.4}>
      <path d="M5 12.5l4.5 4.5L19 7" />
    </Svg>
  )
}
