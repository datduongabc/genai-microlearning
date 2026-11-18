import type { CaptionCue } from '../lib/captions'
import { api } from './client'
import type { FlashcardSource } from './flashcards'
import type { VideoCost } from '../lib/spent'
import type { DownloadTarget } from './upload'

// A passage a scene cites, resolved by the server from its chunk id (the same shape a card has).
export type VideoSource = FlashcardSource

export type SceneKind = 'title' | 'illustration' | 'formula' | 'code' | 'table' | 'summary'

export type VideoStatus = 'generating' | 'draft' | 'drawing' | 'rendering' | 'ready' | 'failed'

export interface VideoScene {
  kind: SceneKind
  title: string
  bullets: string[]
  // bullet_citations[i] are the chunk ids bullets[i] is taken from.
  bullet_citations: string[][]
  formula: string | null
  code: string | null
  table: string[][] | null
  image_prompt: string | null
  // One sentence per line; narration_citations[i] belongs to line i.
  narration: string
  narration_citations: string[][]
  citations: string[]
  author: 'model' | 'lecturer'
  // "Ý n: ..." and "Câu n: ..." are about one item, "Công thức", "Code", "Bảng" about that field,
  // "Chưa có nguồn: Ý n" is derived from empty citations, and anything else is about the scene.
  flags: string[]
  has_image: boolean
  image_outdated: boolean
  needs_image: boolean
  sources: VideoSource[]
}

export interface VideoSummary {
  id: string
  course_id: string
  document_id: string
  focus: string | null
  output_language: 'vi' | 'en'
  model_alias: string
  voice_alias: string
  image_alias: string
  status: VideoStatus
  scene_count: number
  draft_version: number
  rendered_version: number | null
  duration_seconds: number | null
  error: string | null
  published_at: string | null
  created_at: string
}

export interface VideoJob {
  id: string
  job_type: 'video_script' | 'video_images' | 'video_render'
  status: 'queued' | 'running'
}

export interface VideoDetail extends VideoSummary {
  scenes: VideoScene[]
  estimated_seconds: number
  max_seconds: number
  // What stops a render, worded by the server (over 8 minutes, a missing picture…).
  render_problems: string[]
  // What a render goes ahead with but the lecturer should know (a picture drawn from an older description).
  render_warnings: string[]
  // The newest queued or running job of this video; poll while it is set.
  job: VideoJob | null
  cost: VideoCost | null
  // Of the rendered file, not the draft; empty before the first render.
  captions: CaptionCue[]
}

export interface SceneEdit {
  // The index, in the loaded draft, of the scene this one was; null for a new scene. The server
  // carries the picture, voice and flags along this index, so it is sent for every scene.
  from_index: number | null
  kind: SceneKind
  title: string
  bullets: string[]
  bullet_citations: string[][]
  formula: string | null
  code: string | null
  table: string[][] | null
  image_prompt: string | null
  narration: string
  narration_citations: string[][]
  // The stored flags the lecturer keeps; left out for a new scene, which has none.
  flags?: string[]
}

// app/generation/video_script.py and app/video/render.py.
export const MAX_SCENES = 10
export const MAX_BULLETS = 4
export const CHARS_PER_SECOND = 15

export interface MediaChoice {
  alias: string
  label: string
  price: string
}

// The voices and image models a video can be made with (app/llm/media.py), with reference prices.
export interface MediaModels {
  voices: MediaChoice[]
  images: MediaChoice[]
  default_voice: string
  default_image: string
}

export async function getMediaModels() {
  const { data } = await api.get<MediaModels>('/media-models')
  return data
}

interface VideoGenerateOptions {
  focus?: string
  outputLanguage: 'vi' | 'en'
  modelAlias?: string | null
  // Left out, the server's default (when the list of choices could not be loaded).
  voiceAlias?: string
  imageAlias?: string
}

// An empty focus is left out so that the server sees "no focus" in one way only.
export async function generateVideo(
  documentId: string,
  { focus, outputLanguage, modelAlias, voiceAlias, imageAlias }: VideoGenerateOptions,
) {
  const { data } = await api.post<{ id: string; job_id: string }>(
    '/videos',
    {
      output_language: outputLanguage,
      ...(voiceAlias ? { voice_alias: voiceAlias } : {}),
      ...(imageAlias ? { image_alias: imageAlias } : {}),
      ...(focus?.trim() ? { focus: focus.trim() } : {}),
      ...(modelAlias ? { model_alias: modelAlias } : {}),
    },
    { params: { document_id: documentId } },
  )
  return data
}

export async function listVideos(courseId: string) {
  const { data } = await api.get<VideoSummary[]>(`/courses/${courseId}/videos`)
  return data
}

export async function getVideo(id: string) {
  const { data } = await api.get<VideoDetail>(`/videos/${id}`)
  return data
}

export async function saveVideoScenes(id: string, draftVersion: number, scenes: SceneEdit[]) {
  const { data } = await api.put<VideoDetail>(`/videos/${id}/scenes`, { draft_version: draftVersion, scenes })
  return data
}

// Without indexes, every illustration whose picture is missing or older than its description.
export async function drawIllustrations(id: string, sceneIndexes?: number[]) {
  const { data } = await api.post<{ job_id: string; scene_indexes: number[] }>(
    `/videos/${id}/illustrations`,
    sceneIndexes ? { scene_indexes: sceneIndexes } : {},
  )
  return data
}

export async function renderVideo(id: string) {
  const { data } = await api.post<{ job_id: string }>(`/videos/${id}/render`)
  return data
}

export async function publishVideo(id: string, published: boolean) {
  const { data } = await api.post<VideoDetail>(`/videos/${id}/${published ? 'publish' : 'unpublish'}`)
  return data
}

export async function deleteVideo(id: string) {
  await api.delete(`/videos/${id}`)
}

// A short-lived S3 link, or without S3 an API path that needs the token (see videoFileBlob).
export async function getVideoFile(id: string) {
  const { data } = await api.get<DownloadTarget>(`/videos/${id}/file`)
  return data
}

export async function videoFileBlob(path: string) {
  const { data } = await api.get<Blob>(path, { responseType: 'blob' })
  return data
}

// The slide as the render prints it. It needs the bearer token, so it comes down as a blob; `version`
// only changes the URL, so nothing between the browser and the API can hand back an older slide.
export async function getScenePreview(id: string, index: number, version: string) {
  const { data } = await api.get<Blob>(`/videos/${id}/scenes/${index}/preview.png`, {
    responseType: 'blob',
    params: { v: version },
  })
  return data
}
