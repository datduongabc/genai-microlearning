import { useEffect, useEffectEvent, useRef } from 'react'

// Reads something again every `intervalMs` while `active`, for work the worker does: the server's
// status is the truth. A failed read is left to the next tick.
export function usePolling<T>(active: boolean, load: () => Promise<T>, apply: (value: T) => void, intervalMs = 3000) {
  // One read at a time: on a slow server a read started while another is out could answer first,
  // and the older answer arriving after it would put the old status back.
  const reading = useRef(false)
  const tick = useEffectEvent(async () => {
    if (reading.current) return
    reading.current = true
    try {
      apply(await load())
    } catch {
      // transient failure: the next tick retries
    } finally {
      reading.current = false
    }
  })
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => void tick(), intervalMs)
    return () => clearInterval(timer)
  }, [active, intervalMs])
}
