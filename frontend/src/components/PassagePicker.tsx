import { useState, type FormEvent } from 'react'
import { apiErrorMessage, listDocumentChunks, type DocumentPassage } from '../api'

const SNIPPET_LENGTH = 140

function snippet(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > SNIPPET_LENGTH ? `${flat.slice(0, SNIPPET_LENGTH - 1).trimEnd()}…` : flat
}

function Choice({ passage, checked, onChange }: { passage: DocumentPassage; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="passage-choice">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0">
        <span className="passage-locator">{passage.locator || 'Đoạn nguồn'}</span>
        <span className="passage-snippet">{snippet(passage.content)}</span>
      </span>
    </label>
  )
}

// The sources of one item a lecturer wrote (a sentence, a bullet, a card): the passages already
// known to the editor, and any other passage of the document, found by a search. A picked passage
// is handed up with its text, so the editor can show its label without asking again.
export function PassagePicker({ documentId, name, citations, known, onChange }: {
  documentId: string
  // "câu 2 cảnh 1", "thẻ 3": what the controls are named after.
  name: string
  citations: string[]
  known: DocumentPassage[]
  onChange: (citations: string[], picked?: DocumentPassage) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<{ q: string; items: DocumentPassage[]; total: number } | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chosen = known.filter((p) => citations.includes(p.chunk_id))
  const knownIds = new Set(known.map((p) => p.chunk_id))
  const found = results?.items.filter((p) => !knownIds.has(p.chunk_id)) ?? []

  async function search(q: string, offset: number) {
    setSearching(true)
    setError(null)
    try {
      const page = await listDocumentChunks(documentId, { q, offset })
      setResults((prev) => ({ q, total: page.total, items: offset > 0 && prev ? [...prev.items, ...page.items] : page.items }))
    } catch (err) {
      setError(apiErrorMessage(err, 'Không tải được đoạn nguồn.'))
    } finally {
      setSearching(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void search(query, 0)
  }

  const toggle = (passage: DocumentPassage, checked: boolean) => onChange(
    checked ? [...citations, passage.chunk_id] : citations.filter((c) => c !== passage.chunk_id),
    checked && !knownIds.has(passage.chunk_id) ? passage : undefined,
  )

  return (
    <details className="passage-picker">
      <summary>{chosen.length > 0 ? `Nguồn: ${chosen.map((p) => p.locator || 'Đoạn nguồn').join('; ')}` : 'Gắn nguồn'}</summary>
      <div className="passage-picker-body">
        {known.length > 0 && (
          <fieldset aria-label={`Chọn nguồn cho ${name}`} className="passage-choices">
            {known.map((p) => (
              <Choice key={p.chunk_id} passage={p} checked={citations.includes(p.chunk_id)} onChange={(checked) => toggle(p, checked)} />
            ))}
          </fieldset>
        )}
        <form role="search" onSubmit={submit} className="passage-search">
          <input
            type="search"
            aria-label={`Tìm đoạn nguồn cho ${name}`}
            placeholder="Tìm trong tài liệu"
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            className="ui-input min-w-0 flex-1"
          />
          <button type="submit" disabled={searching} className="ui-button">Tìm</button>
        </form>
        {searching && <p className="course-hint">Đang tìm...</p>}
        {error && <p className="text-[13px] danger-text">{error}</p>}
        {results && !searching && found.length === 0 && !error && <p className="course-hint">Không có đoạn nào khớp.</p>}
        {found.length > 0 && (
          <ul className="passage-choices" aria-label={`Kết quả tìm cho ${name}`}>
            {found.map((p) => (
              <li key={p.chunk_id}>
                <Choice passage={p} checked={citations.includes(p.chunk_id)} onChange={(checked) => toggle(p, checked)} />
              </li>
            ))}
          </ul>
        )}
        {results && results.items.length < results.total && (
          <button type="button" onClick={() => void search(results.q, results.items.length)} disabled={searching}
            className="link-action small-action self-start">
            Xem thêm
          </button>
        )}
      </div>
    </details>
  )
}
