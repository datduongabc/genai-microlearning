// Where a cited chunk is in its document, as a short label for the chat.

interface ChunkPlace {
  heading?: string
  slide_title?: string
  page?: number
  slide_index?: number
  start_ms?: number
  end_ms?: number
  spans?: ChunkPlace[]
}

// "27", "27–28", or "9, 11–12": the numbers a chunk covers, runs joined by a dash.
function numberRanges(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b)
  const runs: string[] = []
  for (let i = 0; i < sorted.length; i++) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
    runs.push(i === j ? `${sorted[i]}` : `${sorted[i]}–${sorted[j]}`)
    i = j
  }
  return runs.join(', ')
}

function clock(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  const hours = Math.floor(seconds / 3600)
  const mm = String(Math.floor((seconds % 3600) / 60)).padStart(hours ? 2 : 1, '0')
  const ss = String(seconds % 60).padStart(2, '0')
  return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

// A chunk's location is stored as JSON: {"page": 27, "heading": "..."}, {"slide_index": 3,
// "slide_title": "..."} or {"start_ms": ..., "end_ms": ...} for audio and video, or a list of those
// in "spans" when it crosses pages. The label names every page it covers, not only the first.
export function sourceLabel(ref: string): string {
  try {
    const stored = JSON.parse(ref) as ChunkPlace
    const places = stored.spans?.length ? stored.spans : [stored]
    const heading = places.map((p) => p.heading || p.slide_title).find(Boolean)
    const pages = places.flatMap((p) => (p.page ? [p.page] : []))
    const slides = places.flatMap((p) => (p.slide_index ? [p.slide_index] : []))
    const starts = places.flatMap((p) => (p.start_ms !== undefined ? [p.start_ms] : []))
    const ends = places.flatMap((p) => (p.end_ms !== undefined ? [p.end_ms] : []))
    let where = ''
    if (pages.length) where = `trang ${numberRanges(pages)}`
    else if (slides.length) where = `slide ${numberRanges(slides)}`
    else if (starts.length) {
      const from = clock(Math.min(...starts))
      where = ends.length ? `${from}–${clock(Math.max(...ends))}` : from
    }
    return [heading, where].filter(Boolean).join(', ') || 'Tài liệu môn học'
  } catch {
    return ref || 'Tài liệu môn học'
  }
}
