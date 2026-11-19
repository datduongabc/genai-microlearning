import { z } from "zod";

export const rubric = [
  {
    key: "accuracy",
    question: "Thông tin có khớp tài liệu nguồn không?",
    label: "Độ chính xác",
  },
  {
    key: "completeness",
    question: "Đã đủ ý cần thiết để đạt mục tiêu chưa?",
    label: "Độ đầy đủ",
  },
  {
    key: "consistency",
    question: "Câu hỏi, lời giải và ví dụ có khớp nhau không?",
    label: "Tính nhất quán",
  },
  {
    key: "hallucination_score",
    question: "Có khẳng định nào không có căn cứ trong nguồn?",
    label: "Thông tin bịa",
  },
  {
    key: "slide_quality",
    question: "Slide có đúng, đủ và ngắn gọn không?",
    label: "Chất lượng slide",
  },
  {
    key: "quiz_quality",
    question: "Quiz và bài luyện tập có hợp lý, đo đúng mục tiêu không?",
    label: "Chất lượng quiz",
  },
  {
    key: "bloom_fidelity",
    question: "Nội dung có khớp mức Bloom không?",
    label: "Độ khớp mức Bloom",
  },
  {
    key: "query_alignment",
    question: "Nội dung có bám chủ đề được giao không?",
    label: "Bám chủ đề gốc",
  },
] as const;
const dimension = z.object({
  score: z.number("Vui lòng chọn điểm").int().min(1).max(5),
  justification: z.string().trim().min(1, "Vui lòng ghi nhận xét"),
});
export const ratingSchema = z.object({
  anon_id: z.string(),
  accuracy: dimension,
  completeness: dimension,
  consistency: dimension,
  hallucination_score: dimension,
  slide_quality: dimension,
  quiz_quality: dimension,
  bloom_fidelity: dimension,
  query_alignment: dimension,
  hallucination_findings: z.array(
    z.object({
      claim: z.string().trim().min(1, "Vui lòng nhập khẳng định"),
      field: z.string(),
      reason: z.string().trim().min(1, "Vui lòng nhập lý do"),
    }),
  ),
  overall_notes: z.string(),
});
export type Rating = z.infer<typeof ratingSchema>;
export function emptyRating(code: string): Rating {
  const blank = { score: NaN, justification: "" };
  return {
    anon_id: code,
    accuracy: { ...blank },
    completeness: { ...blank },
    consistency: { ...blank },
    hallucination_score: { ...blank },
    slide_quality: { ...blank },
    quiz_quality: { ...blank },
    bloom_fidelity: { ...blank },
    query_alignment: { ...blank },
    hallucination_findings: [],
    overall_notes: "",
  };
}
export const raterEmailsSchema = z.object({
  emails: z.array(z.email("Email không hợp lệ")).max(100),
});
