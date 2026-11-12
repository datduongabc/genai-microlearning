import { useEffect, useState } from 'react'
import { getMediaModels, type MediaModels } from '../api'

// The voices and image models the server offers, with their prices. `null` while loading,
// 'failed' when they could not be read: a video can still be made with the server's defaults.
export function useMediaModels(wanted = true): MediaModels | null | 'failed' {
  const [models, setModels] = useState<MediaModels | null | 'failed'>(null)
  useEffect(() => {
    if (!wanted) return
    let cancelled = false
    Promise.resolve(getMediaModels()).then(
      (list) => {
        if (!cancelled) setModels(list && Array.isArray(list.voices) ? list : 'failed')
      },
      () => {
        if (!cancelled) setModels('failed')
      },
    )
    return () => {
      cancelled = true
    }
  }, [wanted])
  return models
}
