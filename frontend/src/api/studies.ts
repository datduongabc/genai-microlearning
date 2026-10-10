import { api } from "./client";
import type { Rating } from "../lib/ratingSchema";

export interface Study {
  id: string;
  name: string;
  rubric_version: string;
  status: string;
  created_at: string;
}
export interface MyStudy extends Study {
  rated: number;
  total: number;
}
export interface PacketQuestion {
  stem: string;
  choices: string[];
  correct_index: number;
  explanation: string;
}
export interface BlindItem {
  id: string;
  anon_code: string;
  rating: Rating | null;
  packet: {
    anon_id: string;
    topic: string;
    bloom_level: string;
    learning_objective: string;
    sub_objectives: string[];
    content_markdown: string;
    slides: { title: string; bullets: string[] }[];
    examples: { context: string; analysis: string }[];
    quiz: PacketQuestion[];
    practice: PacketQuestion;
    source_chunks: string[];
  };
}
export interface BlindStudy {
  study: Study;
  items: BlindItem[];
  rated: number;
  total: number;
}
export async function listStudies() {
  return (await api.get<Study[]>("/studies")).data;
}
export async function getRaters(id: string) {
  return (await api.get<{ emails: string[] }>(`/studies/${id}/raters`)).data;
}
export async function setRaters(id: string, emails: string[]) {
  return (await api.put<{ emails: string[] }>(`/studies/${id}/raters`, { emails })).data;
}
export async function myStudies() {
  return (await api.get<MyStudy[]>("/studies/mine")).data;
}
export async function myStudyItems(id: string) {
  return (await api.get<BlindStudy>(`/studies/mine/${id}/items`)).data;
}
export async function saveRating(studyId: string, itemId: string, rating: Rating) {
  return (await api.put<Rating>(`/studies/mine/${studyId}/items/${itemId}/rating`, rating)).data;
}
