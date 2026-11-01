import type { ReactNode } from 'react'

/** The head of a view in the design: the title in Fraunces, a line below it, and what belongs on the right. */
export function PageHead({ title, text, children }: { title: string; text?: ReactNode; children?: ReactNode }) {
  return (
    <div className="bar">
      <div className="lead">
        <h1>{title}</h1>
        {text && <p>{text}</p>}
      </div>
      {children}
    </div>
  )
}
