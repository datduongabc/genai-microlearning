import { getVideoFile } from '../api'
import { useRenderedSource } from '../lib/useRenderedSource'

// An S3 link lasts five minutes, so a player left open that fails to load asks for a new one, a
// couple of times at most so a broken file does not ask forever.
const MAX_LINK_RENEWALS = 2

// A new render is a new player: a failure, the links renewed and the old source of the previous one
// do not carry over.
export function RenderedVideo({ videoId, renderedVersion }: { videoId: string; renderedVersion: number }) {
  return <Player key={`${videoId}-${renderedVersion}`} videoId={videoId} />
}

function Player({ videoId }: { videoId: string }) {
  const { src, failed, onError, loaded } = useRenderedSource(() => getVideoFile(videoId), {
    maxRenewals: MAX_LINK_RENEWALS,
  })

  if (failed) return <p className="text-sm danger-text">Không tải được video đã dựng. Hãy tải lại trang.</p>
  return (
    <video
      aria-label="Video đã dựng"
      src={src ?? undefined}
      controls
      preload="metadata"
      onError={onError}
      onLoadedMetadata={loaded}
      className="video-player"
    />
  )
}
