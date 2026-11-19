// The self-check writes "<criterion> fail: <reason>" for each criterion a question failed
// (backend generation/common.py failed_criteria), joined by "; ", which a reason may contain too,
// so the parts are found by their labels.
const CHECK_LABEL: Record<string, string> = {
  Groundedness: 'Không có trong nguồn',
  Answerability: 'Không có đúng một đáp án',
  "Bloom's level": 'Chưa đúng mức Bloom',
  Distractors: 'Phương án nhiễu',
  Wording: 'Cách diễn đạt',
  'Syntax/IWF': 'Lỗi hình thức',
}
const CHECK_PART = /(?:^|; )(Groundedness|Answerability|Bloom's level|Distractors|Wording|Syntax\/IWF) fail: /

export function checkReasons(failReason: string): string[] {
  const parts = failReason.split(CHECK_PART)
  if (parts.length === 1) return [failReason]
  const reasons: string[] = []
  for (let i = 1; i < parts.length; i += 2) reasons.push(`${CHECK_LABEL[parts[i]]}: ${parts[i + 1].trim()}`)
  return reasons
}
