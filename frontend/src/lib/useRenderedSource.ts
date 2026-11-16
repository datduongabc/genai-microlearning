import { useEffect, useRef, useState } from 'react'
import { videoFileBlob } from '../api'

export interface RenderedTarget {
  url: string
  via_api: boolean
}

// The source of a player for a rendered video: a short-lived S3 link it follows itself or, without
// S3, the file through the API with the token, as a blob. When a link has expired the player errors
// and `onError` asks for a new one, at most `maxRenewals` times in a row. `loaded` (the player's
// loadedmetadata) proves the file plays and starts the count again, so a second expiry in the same
// session is renewed too, while a file that never loads still stops.
export function useRenderedSource<T extends RenderedTarget>(
  load: () => Promise<T>,
  { initial, maxRenewals, beforeRenew }: { initial?: T; maxRenewals: number; beforeRenew?: () => void },
) {
  const [fresh, setFresh] = useState<T | null>(initial ?? null)
  const [blobSrc, setBlobSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [renewal, setRenewal] = useState(0)
  const renewals = useRef(0)
  // A link is the source as soon as it is known; only a file through the API takes a fetch first.
  const src = fresh ? (fresh.via_api ? blobSrc : fresh.url) : null
  const first = useRef(initial)
  const loader = useRef(load)

  useEffect(() => {
    loader.current = load
  })

  useEffect(() => {
    let gone = false
    let created: string | null = null
    const target = renewal === 0 && first.current ? Promise.resolve(first.current) : loader.current()
    target
      .then(async (loaded) => {
        if (gone) return
        setFresh(loaded)
        if (!loaded.via_api) return
        const blob = await videoFileBlob(loaded.url)
        if (gone) return
        created = URL.createObjectURL(blob)
        setBlobSrc(created)
      })
      .catch(() => {
        if (!gone) setFailed(true)
      })
    return () => {
      gone = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [renewal])

  function onError() {
    // Only a link expires; a blob that fails to play would fail again.
    if (!src || src.startsWith('blob:') || renewals.current >= maxRenewals) {
      setFailed(true)
      return
    }
    renewals.current++
    beforeRenew?.()
    setRenewal((n) => n + 1)
  }

  function loaded() {
    renewals.current = 0
  }

  return { src, fresh, failed, onError, loaded }
}
