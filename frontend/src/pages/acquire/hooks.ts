import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { errorText } from '../../lib/errors'

export const POLL_MS = 15_000

/** An action of a button: runs it once, and keeps the sentence about a refusal. */
export function useDo(after?: () => void | Promise<void>) {
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const running = useRef(false)
  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      if (running.current) return false
      running.current = true
      setBusy(true)
      setProblem(null)
      try {
        await action()
        await after?.()
        return true
      } catch (error) {
        setProblem(errorText(error instanceof ApiError ? error.code : 'internal_error', error instanceof ApiError ? error.values : {}))
        return false
      } finally {
        running.current = false
        setBusy(false)
      }
    },
    [after],
  )
  return { problem, busy, run, clear: () => setProblem(null) }
}

/** A list read now and then (every fifteen seconds while the page is in view); `reload` reads it at once. */
export function useLoaded<T>(read: () => Promise<T>, dependencies: unknown[] = []) {
  const [value, setValue] = useState<T | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const reader = useRef(read)
  reader.current = read
  const reload = useCallback(async () => {
    try {
      setValue(await reader.current())
      setFailure(null)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.code : 'internal_error')
    }
  }, [])
  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') void reload()
    }, POLL_MS)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, ...dependencies])
  return { value, failure, reload }
}
