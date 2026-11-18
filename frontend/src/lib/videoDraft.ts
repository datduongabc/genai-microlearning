import { CHARS_PER_SECOND, type SceneEdit, type SceneKind, type VideoDetail, type VideoScene, type VideoSource } from '../api'

// How the editor turns the server's draft into what the lecturer edits and back into a save. Kept
// apart from the page so the rules the server relies on (from_index, flag labels, one citation list
// per sentence) are tested on their own.

export const NO_SOURCE = 'Chưa có nguồn'

// How the server labels the item a flag is about (app/api/videos.py _items).
export const WHOLE_ITEM_LABEL: Partial<Record<SceneKind, string>> = { formula: 'Công thức', code: 'Code', table: 'Bảng' }
const ITEM_FLAG = /^(Ý \d+|Câu \d+|Công thức|Code|Bảng): (.*)$/s

// A bullet or a sentence as it is edited. `label` is the one it had in the loaded draft ("Ý 2"),
// which is how its stored flags name it, so they stay with it when items are moved or removed.
export interface ItemDraft {
  key: number
  text: string
  citations: string[]
  label: string | null
}

export interface SceneDraft {
  key: number
  // Its index in the loaded draft, sent back so its picture, voice and flags come with it.
  fromIndex: number | null
  kind: SceneKind
  title: string
  bullets: ItemDraft[]
  sentences: ItemDraft[]
  formula: string
  code: string
  // One row a line, cells split by "|".
  table: string
  imagePrompt: string
  // The stored flags the lecturer keeps, "Chưa có nguồn" aside: that one is worked out from the
  // citations, here as on the server, and cannot be dismissed.
  flags: string[]
  // Every passage the loaded scene cites and every one the lecturer picked since, so a source
  // unticked from one item can still be ticked again and each ticked one shows its label.
  sources: VideoSource[]
}

function itemDrafts(texts: string[], citations: string[][], prefix: string, nextKey: () => number): ItemDraft[] {
  return texts.map((text, i) => ({ key: nextKey(), text, citations: citations[i] ?? [], label: `${prefix} ${i + 1}` }))
}

export function toDrafts(video: VideoDetail, nextKey: () => number): SceneDraft[] {
  return video.scenes.map((s, i) => ({
    key: nextKey(),
    fromIndex: i,
    kind: s.kind,
    title: s.title,
    bullets: itemDrafts(s.bullets, s.bullet_citations, 'Ý', nextKey),
    sentences: itemDrafts(s.narration ? s.narration.split('\n') : [], s.narration_citations, 'Câu', nextKey),
    formula: s.formula ?? '',
    code: s.code ?? '',
    table: (s.table ?? []).map((row) => row.join(' | ')).join('\n'),
    imagePrompt: s.image_prompt ?? '',
    flags: s.flags.filter((f) => !f.startsWith(`${NO_SOURCE}: `)),
    sources: s.sources,
  }))
}

export function parseTable(text: string): string[][] | null {
  const rows = text.split('\n').filter((line) => line.trim()).map((line) => line.split('|').map((cell) => cell.trim()))
  return rows.length > 0 ? rows : null
}

const filled = (items: ItemDraft[]) => items.filter((item) => item.text.trim() !== '')

export function toEdits(scenes: SceneDraft[]): SceneEdit[] {
  return scenes.map((s) => {
    // An emptied bullet or sentence is a removed one: the server takes no blank items.
    const bullets = filled(s.bullets)
    const sentences = filled(s.sentences)
    return {
      from_index: s.fromIndex,
      kind: s.kind,
      title: s.title.trim(),
      bullets: bullets.map((b) => b.text.trim()),
      bullet_citations: bullets.map((b) => b.citations),
      // Only the field of the scene's kind is kept by the server, so only that one is sent.
      formula: s.kind === 'formula' && s.formula.trim() ? s.formula : null,
      code: s.kind === 'code' && s.code.trim() ? s.code : null,
      table: s.kind === 'table' ? parseTable(s.table) : null,
      image_prompt: s.kind === 'illustration' && s.imagePrompt.trim() ? s.imagePrompt : null,
      narration: sentences.map((t) => t.text.trim()).join('\n'),
      narration_citations: sentences.map((t) => t.citations),
      ...(s.fromIndex === null ? {} : { flags: s.flags }),
    }
  })
}

export const editsKey = (scenes: SceneDraft[]) => JSON.stringify(toEdits(scenes))

// The same count the server makes (app/generation/video_script.py estimated_seconds).
export function estimatedSeconds(scenes: SceneDraft[]): number {
  return toEdits(scenes).reduce((sum, s) => sum + s.narration.length, 0) / CHARS_PER_SECOND
}

// A short stand-in for what a slide prints, so its preview is asked for again only when that changes.
export function slideVersion(scene: VideoScene, index: number, picturesDrawn: number): string {
  const printed = JSON.stringify([index, scene.kind, scene.title, scene.bullets, scene.formula, scene.code, scene.table,
    scene.citations, scene.has_image, scene.image_outdated, scene.kind === 'illustration' ? picturesDrawn : 0])
  let hash = 5381
  for (let i = 0; i < printed.length; i++) hash = (hash * 33 + printed.charCodeAt(i)) | 0
  return (hash >>> 0).toString(36)
}

export function flagsFor(flags: string[], label: string | null): string[] {
  if (label === null) return []
  return flags.filter((f) => f.startsWith(`${label}: `))
}

export const sceneFlags = (flags: string[]) => flags.filter((f) => !ITEM_FLAG.test(f))
export const flagMessage = (flag: string) => flag.match(ITEM_FLAG)?.[2] ?? flag

// A scene the lecturer adds: nothing drawn or voiced, no flags, one sentence to start writing in.
export function newScene(nextKey: () => number): SceneDraft {
  return {
    key: nextKey(), fromIndex: null, kind: 'summary', title: '', bullets: [],
    sentences: [{ key: nextKey(), text: '', citations: [], label: null }],
    formula: '', code: '', table: '', imagePrompt: '', flags: [], sources: [],
  }
}

// The flags the editor shows on a scene. One about an item that was removed, or about a field the
// scene's kind no longer has, is dropped by the save, so it is neither shown nor counted.
export function shownFlags(scene: SceneDraft): string[] {
  const labels = [...scene.bullets, ...scene.sentences].map((item) => item.label).concat(WHOLE_ITEM_LABEL[scene.kind] ?? null)
  return [...sceneFlags(scene.flags), ...labels.flatMap((label) => flagsFor(scene.flags, label))]
}

// "Cần xem lại": a flag to read, or a bullet or sentence written without a source.
export function needsReview(scene: SceneDraft): boolean {
  return shownFlags(scene).length > 0 || [...filled(scene.bullets), ...filled(scene.sentences)].some((i) => i.citations.length === 0)
}
