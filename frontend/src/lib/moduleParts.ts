import { createContext } from 'react'
import type { ExplanationItem, ModuleDetail } from '../api'

export type Citation = NonNullable<ModuleDetail['claim_citations']>[number]
export interface OpenedSource { citation: Citation; number: number }

// The passage a lecturer is checking a statement against. Opening it does not cover the statement:
// on a wide screen it is the column beside the text, on a phone a sheet over the lower half.
export const SourceContext = createContext<{ opened: OpenedSource | null; open: (source: OpenedSource, from: HTMLElement) => void }>({
  opened: null, open: () => {},
})

export const COMPONENT_LABEL: Record<string, string> = {
  content: 'Nội dung',
  slides: 'Slide bài giảng',
  example: 'Ví dụ minh họa',
  quiz: 'Câu hỏi kiểm tra',
  practice: 'Câu hỏi luyện tập',
  explanation: 'Giải thích đáp án',
}

// The explanation a learner sees for each question, by position: the one in the Explanation
// component, which is the teacher's edited version and the one sent to Moodle.
export function explanationsByQuestion(module: ModuleDetail, type: string): Record<number, string> {
  const component = module.components.find((c) => c.type === 'explanation')
  const items = (component?.content.items as ExplanationItem[] | undefined) ?? []
  return Object.fromEntries(
    items.filter((i) => i.component === type).map((i) => [i.position, i.explanation_text]),
  )
}

// One wording for every editor of a part whose save failed.
export const SAVE_FAILED = 'Không thể lưu thay đổi. Vui lòng thử lại.'
