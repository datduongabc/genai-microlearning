import axios from 'axios'
import { useEffect, useRef, useState } from 'react'
import { getScenePreview } from '../api'
import type { Limiter } from '../lib/limiter'

// The server renders at most two slides at once and answers 503 when it is busy, so a busy slide
// is asked for again after a pause, a little longer each time, rather than failing the editor.
const PREVIEW_RETRY_DELAYS_MS = [2000, 3000, 5000]

// A slide is fetched once it comes near the screen: ten slides at a second or more each would
// otherwise queue behind each other and behind other lecturers' previews.
function useNearScreen(element: HTMLElement | null): boolean {
  const [near, setNear] = useState(false)
  useEffect(() => {
    if (!element || near) return
    if (typeof IntersectionObserver === 'undefined') {
      // No observer (an old browser, or the test DOM): load straight away rather than never.
      const timer = setTimeout(() => setNear(true), 0)
      return () => clearTimeout(timer)
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setNear(true)
    }, { rootMargin: '400px 0px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [element, near])
  return near
}

export function ScenePreview({ videoId, index, version, number, limiter, note }: {
  videoId: string
  // The scene's index in the saved draft, which is what the server renders.
  index: number
  // Changes when what the slide prints changes (a save or a new picture), and only then.
  version: string
  number: number
  limiter: Limiter
  note?: string
}) {
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const near = useNearScreen(box)
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  // The shown slide stays until the next one has arrived, so a save does not flash an empty box.
  const shown = useRef<string | null>(null)

  useEffect(() => {
    if (!near) return
    let gone = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async (tries: number) => {
      try {
        const blob = await limiter.run(() => getScenePreview(videoId, index, version), () => gone)
        if (gone || !blob) return
        const next = URL.createObjectURL(blob)
        if (shown.current) URL.revokeObjectURL(shown.current)
        shown.current = next
        setUrl(next)
        setFailed(false)
      } catch (err) {
        if (gone) return
        const busy = axios.isAxiosError(err) && err.response?.status === 503
        if (busy && tries < PREVIEW_RETRY_DELAYS_MS.length) {
          timer = setTimeout(() => void load(tries + 1), PREVIEW_RETRY_DELAYS_MS[tries])
          return
        }
        setFailed(true)
      }
    }
    void load(0)
    return () => {
      gone = true
      clearTimeout(timer)
    }
  }, [near, videoId, index, version, limiter, attempt])

  useEffect(() => () => {
    if (shown.current) URL.revokeObjectURL(shown.current)
  }, [])

  return (
    <figure className="video-slide-figure">
      <div ref={setBox} className="video-slide">
        {url ? (
          <img src={url} alt={`Slide cảnh ${number}`} width={1280} height={720} />
        ) : failed ? (
          <div className="video-slide-empty">
            <span>Không tải được slide.</span>
            <button type="button" onClick={() => { setFailed(false); setAttempt((n) => n + 1) }} className="link-action">
              Tải lại slide
            </button>
          </div>
        ) : (
          <span className="video-slide-empty">Đang dựng slide…</span>
        )}
      </div>
      {note && <figcaption className="course-hint">{note}</figcaption>}
    </figure>
  )
}

export function SlidePlaceholder({ text }: { text: string }) {
  return (
    <figure className="video-slide-figure">
      <div className="video-slide"><span className="video-slide-empty">{text}</span></div>
    </figure>
  )
}
