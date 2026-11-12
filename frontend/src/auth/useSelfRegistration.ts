import { useEffect, useState } from 'react'
import { getAuthOptions } from '../api'

/** Whether anyone may create an account here; null until the server answers. A public deployment
 * closes it (see backend/app/config.py, self_registration). When the question fails the link stays,
 * and the server still refuses with a message if registration is closed. */
export function useSelfRegistration(): boolean | null {
  const [open, setOpen] = useState<boolean | null>(null)
  useEffect(() => {
    let cancelled = false
    Promise.resolve(getAuthOptions()).then(
      (options) => { if (!cancelled) setOpen(options?.self_registration ?? true) },
      () => { if (!cancelled) setOpen(true) },
    )
    return () => { cancelled = true }
  }, [])
  return open
}
