import { describe, expect, it } from 'vitest'
import type { VideoDetail, VideoScene } from '../../../api'
import {
  estimatedSeconds,
  flagsFor,
  needsReview,
  newScene,
  parseTable,
  sceneFlags,
  shownFlags,
  slideVersion,
  toDrafts,
  toEdits,
} from '../../../lib/videoDraft'

const scene = (extra: Partial<VideoScene> = {}): VideoScene => ({
  kind: 'summary', title: 'FCFS', bullets: ['Đến trước', 'Phục vụ trước'], bullet_citations: [['c1'], ['c2']],
  formula: null, code: null, table: null, image_prompt: null, narration: 'Câu một.\nCâu hai.',
  narration_citations: [['c1'], []], citations: ['c1', 'c2'], author: 'model', flags: [],
  has_image: false, image_outdated: false, needs_image: false, sources: [], ...extra,
})

const video = (scenes: VideoScene[]) => ({ scenes } as VideoDetail)
const counter = () => {
  let n = 0
  return () => n++
}

describe('videoDraft', () => {
  it('sends a loaded draft back as it was, each scene with its index', () => {
    const scenes = [scene(), scene({ title: 'RR' })]
    const edits = toEdits(toDrafts(video(scenes), counter()))

    expect(edits.map((e) => e.from_index)).toEqual([0, 1])
    expect(edits[0]).toEqual({
      from_index: 0, kind: 'summary', title: 'FCFS', bullets: ['Đến trước', 'Phục vụ trước'],
      bullet_citations: [['c1'], ['c2']], formula: null, code: null, table: null, image_prompt: null,
      narration: 'Câu một.\nCâu hai.', narration_citations: [['c1'], []], flags: [],
    })
  })

  it('keeps where a scene came from when scenes are moved, and sends a new one as null without flags', () => {
    const drafts = toDrafts(video([scene({ title: 'A' }), scene({ title: 'B' })]), counter())
    const edits = toEdits([drafts[1], newScene(counter()), drafts[0]].map((d, i) => (i === 1 ? { ...d, title: 'Mới' } : d)))

    expect(edits.map((e) => [e.from_index, e.title])).toEqual([[1, 'B'], [null, 'Mới'], [0, 'A']])
    expect('flags' in edits[1]).toBe(false)
  })

  it('labels each item as the stored flags name it, and the label stays when items move', () => {
    const [draft] = toDrafts(video([scene({ flags: ['Ý 2: ngược nghĩa', 'Câu 1: sai số', 'Chưa có nguồn: Câu 2', 'Lời thoại ngắn'] })]), counter())

    expect(draft.bullets.map((b) => b.label)).toEqual(['Ý 1', 'Ý 2'])
    expect(draft.sentences.map((s) => s.label)).toEqual(['Câu 1', 'Câu 2'])
    // "Chưa có nguồn" is worked out from the citations, never kept or sent.
    expect(draft.flags).toEqual(['Ý 2: ngược nghĩa', 'Câu 1: sai số', 'Lời thoại ngắn'])
    expect(flagsFor(draft.flags, draft.bullets[1].label)).toEqual(['Ý 2: ngược nghĩa'])
    expect(flagsFor(draft.flags, null)).toEqual([])
    expect(sceneFlags(draft.flags)).toEqual(['Lời thoại ngắn'])

    const moved = { ...draft, bullets: [draft.bullets[1]] }
    expect(flagsFor(moved.flags, moved.bullets[0].label)).toEqual(['Ý 2: ngược nghĩa'])
    // The flag of the removed bullet is not shown: the save drops it.
    expect(shownFlags({ ...draft, bullets: [draft.bullets[0]] })).not.toContain('Ý 2: ngược nghĩa')
  })

  it('shows a field flag only while the scene has that field', () => {
    const [draft] = toDrafts(video([scene({ kind: 'formula', formula: 'x^2', flags: ['Công thức: sai dấu'] })]), counter())

    expect(shownFlags(draft)).toEqual(['Công thức: sai dấu'])
    expect(shownFlags({ ...draft, kind: 'summary' })).toEqual([])
  })

  it('drops blank items with their citations, so each line keeps its own list', () => {
    const [draft] = toDrafts(video([scene()]), counter())
    const [edit] = toEdits([{ ...draft, sentences: [{ ...draft.sentences[0], text: '  ' }, draft.sentences[1]] }])

    expect(edit.narration).toBe('Câu hai.')
    expect(edit.narration_citations).toEqual([[]])
  })

  it('sends only the field of the scene kind', () => {
    const [draft] = toDrafts(video([scene({ kind: 'illustration', image_prompt: 'Một hàng đợi' })]), counter())

    expect(toEdits([draft])[0].image_prompt).toBe('Một hàng đợi')
    expect(toEdits([{ ...draft, kind: 'formula', formula: 'a+b' }])[0]).toMatchObject({ image_prompt: null, formula: 'a+b' })
    expect(toEdits([{ ...draft, imagePrompt: '   ' }])[0].image_prompt).toBeNull()
  })

  it('reads a table one row a line, cells split by a bar', () => {
    expect(parseTable('A | B\n\n 1 |2 ')).toEqual([['A', 'B'], ['1', '2']])
    expect(parseTable('  ')).toBeNull()
  })

  it('estimates the length as the server does, from the narration it would save', () => {
    const drafts = toDrafts(video([scene({ narration: 'a'.repeat(14) + '\n' + 'b'.repeat(15), narration_citations: [[], []] })]), counter())

    expect(estimatedSeconds(drafts)).toBe(2)
  })

  it('marks a scene for review for a shown flag or an uncited item', () => {
    const [cited] = toDrafts(video([scene({ narration_citations: [['c1'], ['c1']] })]), counter())

    expect(needsReview(cited)).toBe(false)
    expect(needsReview({ ...cited, sentences: [{ ...cited.sentences[0], citations: [] }] })).toBe(true)
    expect(needsReview({ ...cited, flags: ['Lời thoại ngắn'] })).toBe(true)
  })

  it('changes a slide version only when what the slide prints changes', () => {
    const base = scene()

    expect(slideVersion({ ...base, narration: 'Khác.' }, 0, 0)).toBe(slideVersion(base, 0, 0))
    expect(slideVersion({ ...base, title: 'Khác' }, 0, 0)).not.toBe(slideVersion(base, 0, 0))
    expect(slideVersion(base, 1, 0)).not.toBe(slideVersion(base, 0, 0))
    expect(slideVersion({ ...base, has_image: true }, 0, 0)).not.toBe(slideVersion(base, 0, 0))
    // A drawing job ending counts for an illustration only.
    expect(slideVersion(base, 0, 1)).toBe(slideVersion(base, 0, 0))
    const drawn = scene({ kind: 'illustration', image_prompt: 'x' })
    expect(slideVersion(drawn, 0, 1)).not.toBe(slideVersion(drawn, 0, 0))
  })
})
