import { describe, expect, it } from 'vitest'
import { flashcardSpent, videoSpent } from '../../../lib/spent'

describe('videoSpent', () => {
  it('adds the script, the pictures and the voice when all are priced', () => {
    expect(videoSpent({
      script: { usd: 0.01, llm_calls: 3, priced_calls: 3 }, images: { usd: 0.07 }, voice: { usd: 0.005 },
    })).toBe('Chi phí khoảng $0,085')
  })

  it('says so when some script calls have no price, instead of passing a smaller number as the total', () => {
    expect(videoSpent({ script: { usd: 0.01, llm_calls: 3, priced_calls: 2 }, images: { usd: 0.07 } }))
      .toBe('Chi phí ít nhất $0,08 (chưa rõ giá của 1 lần gọi AI viết lời thoại)')
  })

  it('says the script cost is unknown for a video made before it was recorded', () => {
    expect(videoSpent({ script: { usd: null, llm_calls: 10 }, images: { usd: 0.07 } }))
      .toBe('Chi phí ít nhất $0,07 (chưa rõ giá của 10 lần gọi AI viết lời thoại)')
    expect(videoSpent({ script: { usd: null, llm_calls: 10 } })).toBe('Chưa rõ giá của 10 lần gọi AI viết lời thoại')
  })

  it('shows nothing before anything was spent', () => {
    expect(videoSpent(null)).toBeNull()
    expect(videoSpent({})).toBeNull()
  })
})

describe('flashcardSpent', () => {
  it('shows the price of a set', () => {
    expect(flashcardSpent({ usd: 0.0031, calls: 2, priced_calls: 2 })).toBe('Chi phí khoảng $0,003')
  })

  it('says so when part of the calls has no price', () => {
    expect(flashcardSpent({ usd: 0.003, calls: 3, priced_calls: 2 }))
      .toBe('Chi phí ít nhất $0,003 (chưa rõ giá của 1 lần gọi model)')
    expect(flashcardSpent({ usd: null, calls: 2, priced_calls: 0 })).toBe('Chưa rõ giá của 2 lần gọi model')
  })

  it('shows nothing for a set made before costs were kept', () => {
    expect(flashcardSpent({})).toBeNull()
    expect(flashcardSpent(undefined)).toBeNull()
  })
})
