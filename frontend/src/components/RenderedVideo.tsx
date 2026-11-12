import { useEffect, useRef, useState } from 'react'
import { getVideoFile, videoFileBlob } from '../api'

// An S3 link lasts five minutes, so a player left open that fails to load asks for a new one, a
// couple of times at most so a broken file does not ask forever.
const MAX_LINK_RENEWALS = 2

// The rendered MP4: a short-lived S3 link the player follows itself or, without S3, the file
// through the API with the token, as a blob.
export function RenderedVideo({ videoId, renderedVersion }: { videoId: string; renderedVersion: number }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [renewal, setRenewal] = useState(0)
  const renewals = useRef(0)

  useEffect(() => {
    let gone = false
    let created: string | null = null
    getVideoFile(videoId)
      .then(async (target) => {
        if (!target.via_api) {
          if (!gone) setSrc(target.url)
          return
        }
        const blob = await videoFileBlob(target.url)
        if (gone) return
        created = URL.createObjectURL(blob)
        setSrc(created)
      })
      .catch(() => {
        if (!gone) setFailed(true)
      })
    return () => {
      gone = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [videoId, renderedVersion, renewal])

  function renew() {
    if (renewals.current >= MAX_LINK_RENEWALS) {
      setFailed(true)
      return
    }
    renewals.current++
    setRenewal((n) => n + 1)
  }

  if (failed) return <p className="text-sm danger-text">Không tải được video đã dựng. Hãy tải lại trang.</p>
  return (
    <video
      aria-label="Video đã dựng"
      src={src ?? undefined}
      controls
      preload="metadata"
      // Only a link expires; a blob that fails to play would fail again.
      onError={src && !src.startsWith('blob:') ? renew : undefined}
      className="video-player"
    />
  )
}
