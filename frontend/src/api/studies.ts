import { api } from "./client";
import { followDownload, sendFile, type DownloadTarget, type UploadTarget } from "./upload";
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
  // The file the admin picked for this packet, to judge it against; null when there is none.
  reference: { filename: string; size: number } | null;
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

export interface ResultRating {
  rater: string;
  kind: "human" | "llm_judge";
  scores: Record<string, { score: number; justification: string }>;
  findings: { claim: string; field: string; reason: string }[];
  notes: string;
}
export interface StudyResults {
  criteria: string[];
  total: number;
  raters: { email: string; number: number | null; rated: number; invited: boolean }[];
  has_judge: boolean;
  unpaired: string[];
  items: { id: string; anon_code: string; group: "rag_llm" | "llm_no_rag" | "human"; pair_code: string; topic: string; ratings: ResultRating[] }[];
}
export async function getStudyResults(id: string) {
  return (await api.get<StudyResults>(`/studies/${id}/results`)).data;
}
// The same files the CLI export writes, for analyze_study.py.
export async function downloadStudyFile(id: string, file: string) {
  return (await api.get<Blob>(`/studies/${id}/results/${file}`, { responseType: "blob" })).data;
}

export interface StudyFile {
  id: string;
  filename: string;
  size: number;
  created_at: string;
}
// What the admin sees of a packet: the group too, since only the raters are blind to it.
export interface StudyItemSummary {
  id: string;
  anon_code: string;
  group: "rag_llm" | "llm_no_rag" | "human";
  module_id: string | null;
  // The RAG module both members of a pair share; null for a human-written packet.
  pair_id: string | null;
  reference_file_id: string | null;
  packet: { topic: string };
}
export async function getStudyItems(id: string) {
  return (await api.get<{ items: StudyItemSummary[] }>(`/studies/${id}`)).data.items;
}
export async function listStudyFiles(id: string) {
  return (await api.get<StudyFile[]>(`/studies/${id}/files`)).data;
}
// The same three steps as a course document: ask where, send, report it sent.
export async function uploadStudyFile(id: string, file: File, onProgress?: (fraction: number) => void) {
  const { data: started } = await api.post<{ file_id: string; upload: UploadTarget }>(
    `/studies/${id}/files`, { filename: file.name, size: file.size });
  await sendFile(started.upload, file, onProgress);
  return (await api.post<StudyFile>(`/studies/${id}/files/${started.file_id}/complete`)).data;
}
export async function deleteStudyFile(id: string, fileId: string) {
  await api.delete(`/studies/${id}/files/${fileId}`);
}
export async function downloadStudyReference(id: string, fileId: string) {
  await followDownload((await api.get<DownloadTarget>(`/studies/${id}/files/${fileId}/download`)).data);
}
// Sets the packet's file and, for a RAG/no-RAG pair, its partner's; answers every packet's file.
export async function setItemReference(id: string, itemId: string, fileId: string | null) {
  return (await api.put<{ id: string; reference_file_id: string | null }[]>(
    `/studies/${id}/items/${itemId}/reference`, { file_id: fileId })).data;
}
export async function downloadPacketReference(studyId: string, itemId: string) {
  await followDownload((await api.get<DownloadTarget>(`/studies/mine/${studyId}/items/${itemId}/reference`)).data);
}
