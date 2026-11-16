import type { CallsCost } from '../lib/spent'
import { api } from './client'

// A passage a card cites, as the server resolved it from the card's chunk id.
export interface FlashcardSource {
  chunk_id: string
  locator: string
  content: string
}

export interface FlashcardCard {
  front: string
  back: string
  // Chunk ids of the document the card was drawn from.
  citations: string[]
  sources: FlashcardSource[]
  author: 'model' | 'lecturer'
  // A reason the lecturer should look at the card again; null when nothing is wrong.
  flag: string | null
}

export type FlashcardCardEdit = Pick<FlashcardCard, 'front' | 'back' | 'citations'>

export interface FlashcardSetSummary {
  id: string
  course_id: string
  document_id: string
  focus: string | null
  output_language: 'vi' | 'en'
  model_alias: string
  status: 'generating' | 'draft' | 'failed'
  card_count: number
  published_at: string | null
  created_at: string
  // What the text-model calls of this set cost; empty for a set made before costs were kept.
  cost: CallsCost | null
}

export interface FlashcardSetDetail extends FlashcardSetSummary {
  cards: FlashcardCard[]
}

// The server accepts at most this many cards in a set (app/generation/flashcards.py MAX_CARDS).
export const MAX_FLASHCARDS = 15

// An empty focus is left out so that the server sees "no focus" in one way only.
export async function generateFlashcards(
  documentId: string,
  { focus, outputLanguage, modelAlias }: { focus?: string; outputLanguage: 'vi' | 'en'; modelAlias?: string | null },
) {
  const { data } = await api.post<{ id: string; job_id: string }>(
    '/flashcard-sets',
    {
      output_language: outputLanguage,
      ...(focus?.trim() ? { focus: focus.trim() } : {}),
      ...(modelAlias ? { model_alias: modelAlias } : {}),
    },
    { params: { document_id: documentId } },
  )
  return data
}

export async function listFlashcardSets(courseId: string) {
  const { data } = await api.get<FlashcardSetSummary[]>(`/courses/${courseId}/flashcard-sets`)
  return data
}

export async function getFlashcardSet(id: string) {
  const { data } = await api.get<FlashcardSetDetail>(`/flashcard-sets/${id}`)
  return data
}

export async function saveFlashcards(id: string, cards: FlashcardCardEdit[]) {
  const { data } = await api.put<FlashcardSetDetail>(`/flashcard-sets/${id}/cards`, { cards })
  return data
}

export async function publishFlashcardSet(id: string, published: boolean) {
  const { data } = await api.post<FlashcardSetDetail>(`/flashcard-sets/${id}/${published ? 'publish' : 'unpublish'}`)
  return data
}

export async function deleteFlashcardSet(id: string) {
  await api.delete(`/flashcard-sets/${id}`)
}
