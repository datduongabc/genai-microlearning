import { api } from './client'

interface LearnDocument {
  id: string
  filename: string
}

export interface LearnFlashcardSetItem {
  id: string
  focus: string | null
  card_count: number
  document: LearnDocument
}

export interface LearnVideoItem {
  id: string
  focus: string | null
  duration_seconds: number | null
  document: LearnDocument
}

export interface LearnItems {
  flashcard_sets: LearnFlashcardSetItem[]
  videos: LearnVideoItem[]
}

// What a learner sees of a card: no review flag, no author, and a source is only its label.
export interface LearnCard {
  front: string
  back: string
  sources: string[]
}

export interface LearnFlashcardSet {
  id: string
  course_id: string
  focus: string | null
  cards: LearnCard[]
}

export async function listLearnItems(courseId: string) {
  const { data } = await api.get<LearnItems>(`/learn/courses/${courseId}/items`)
  return data
}

export async function getLearnFlashcardSet(id: string) {
  const { data } = await api.get<LearnFlashcardSet>(`/learn/flashcard-sets/${id}`)
  return data
}
