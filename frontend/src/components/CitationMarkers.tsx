import { useState } from 'react'

interface Passage { chunk_id: string; locator: string; content: string }

// The sources of one item as numbered markers, as in the module preview. A number is the passage's
// place among its scene's or card's sources, so one passage keeps one number across the items.
export function CitationMarkers({ sources, citations, label }: { sources: Passage[]; citations: string[]; label: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const cited = sources.map((passage, i) => ({ passage, number: i + 1 })).filter(({ passage }) => citations.includes(passage.chunk_id))
  if (cited.length === 0) return null
  const shown = cited.find(({ passage }) => passage.chunk_id === open)?.passage
  return (
    <div className="citation-markers" role="group" aria-label={label}>
      <div>
        {cited.map(({ passage, number }) => (
          <button
            key={passage.chunk_id}
            type="button"
            title={passage.locator || 'Đoạn nguồn'}
            aria-label={`Nguồn ${number}: ${passage.locator || 'Đoạn nguồn'}`}
            aria-expanded={open === passage.chunk_id}
            onClick={() => setOpen(open === passage.chunk_id ? null : passage.chunk_id)}
            className="citation-marker"
          >
            [{number}]
          </button>
        ))}
      </div>
      {shown && (
        <div className="citation-passage">
          <p className="module-source-place">{shown.locator || 'Đoạn nguồn'}</p>
          <p className="module-source-passage">{shown.content}</p>
        </div>
      )}
    </div>
  )
}
