import type { FlashcardSource } from '../api'

// The passages an item cites, each behind a disclosure so the editor stays short.
export function SourcePassages({ sources, label }: { sources: FlashcardSource[]; label: string }) {
  if (sources.length === 0) return null
  return (
    <ul className="flashcard-sources" aria-label={label}>
      {sources.map((source) => (
        <li key={source.chunk_id}>
          <details>
            <summary>{source.locator || 'Đoạn nguồn'}</summary>
            <p className="module-source-passage">{source.content}</p>
          </details>
        </li>
      ))}
    </ul>
  )
}
