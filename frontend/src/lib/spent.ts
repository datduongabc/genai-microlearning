// What the lecturer is told a video or a flashcard set has cost. A total that leaves out calls whose
// price the provider did not report must not read as the whole bill, so it says "ít nhất" and how many
// calls are missing.

export interface CallsCost {
  usd?: number | null
  // The model calls made, and how many of them came back with a price.
  calls?: number
  llm_calls?: number
  priced_calls?: number
}

export interface VideoCost {
  script?: CallsCost
  images?: { usd?: number | null }
  voice?: { usd?: number | null }
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString('vi-VN', { maximumFractionDigits: 3 })}`
}

function unpriced(part: CallsCost | undefined): number {
  const calls = part?.calls ?? part?.llm_calls ?? 0
  // A cost without priced_calls was recorded before the count existed: an amount then means all were priced.
  const priced = part?.priced_calls ?? (part?.usd != null ? calls : 0)
  return Math.max(calls - priced, 0)
}

function sentence(total: number, missing: number, what: string): string | null {
  if (missing === 0) return total > 0 ? `Đã tốn khoảng ${formatUsd(total)}` : null
  const gap = `chưa rõ giá của ${missing} lần gọi ${what}`
  return total > 0 ? `Đã tốn ít nhất ${formatUsd(total)} (${gap})` : `Chưa rõ giá của ${missing} lần gọi ${what}`
}

export function videoSpent(cost: VideoCost | null | undefined): string | null {
  const total = (cost?.script?.usd ?? 0) + (cost?.images?.usd ?? 0) + (cost?.voice?.usd ?? 0)
  return sentence(total, unpriced(cost?.script), 'model viết lời thoại')
}

export function flashcardSpent(cost: CallsCost | null | undefined): string | null {
  return sentence(cost?.usd ?? 0, unpriced(cost ?? undefined), 'model')
}
