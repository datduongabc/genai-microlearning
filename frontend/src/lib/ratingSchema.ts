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
export type RubricCriterion = { key: string; label: string; question: string };
export type PacketKind = "module" | "flashcards" | "video";
// Flashcard sets and videos are rated on their own, not compared, on criteria of their own.
export const mediaRubric: Record<"flashcards" | "video", RubricCriterion[]> = {
  flashcards: [
    { key: "accuracy", label: "Độ chính xác", question: "Mặt sau có đúng với tài liệu không?" },
    { key: "focus", label: "Trọng tâm", question: "Thẻ có hỏi những ý chính của tài liệu, hoặc đúng chủ đề được yêu cầu không?" },
    { key: "clarity", label: "Rõ ràng", question: "Mỗi thẻ hỏi một ý, mặt trước rõ ràng, mặt sau ngắn gọn không?" },
    { key: "no_duplicates", label: "Không trùng lặp", question: "Các thẻ có tránh hỏi lại cùng một ý không?" },
  ],
  video: [
    { key: "accuracy", label: "Độ chính xác", question: "Lời đọc và hình có đúng với tài liệu không?" },
    { key: "focus", label: "Trọng tâm", question: "Video có tập trung vào ý chính, hoặc đúng chủ đề được yêu cầu không?" },
    { key: "coherence", label: "Mạch lạc", question: "Các cảnh có đi theo thứ tự hợp lý, dễ theo dõi không?" },
    { key: "visuals_match", label: "Hình khớp lời", question: "Hình và ý chính trên màn hình có khớp với lời đọc không?" },
  ],
};
// Shown to the admin only: raters see codes.
export const GROUP_LABEL: Record<string, string> = {
  rag_llm: "RAG", llm_no_rag: "Không RAG", human: "Người viết", flashcards: "Flashcard", video: "Video",
};
export function rubricOf(kind: PacketKind): readonly RubricCriterion[] {
  return kind === "module" ? rubric : mediaRubric[kind];
}
type Dimension = z.infer<typeof dimension>;
type Finding = Rating["hallucination_findings"][number];
// What the server stores for a flashcard set or a video.
export interface MediaRating {
  anon_id: string;
  scores: Record<string, Dimension>;
  hallucination_findings: Finding[];
  overall_notes: string;
}
// The form holds every kind the same way, its scores keyed by criterion.
export type RatingForm = MediaRating;
export function ratingFormSchema(kind: PacketKind) {
  return z.object({
    anon_id: z.string(),
    scores: z.object(Object.fromEntries(rubricOf(kind).map((c) => [c.key, dimension]))),
    hallucination_findings: ratingSchema.shape.hallucination_findings,
    overall_notes: z.string(),
  });
}
export function toRatingForm(kind: PacketKind, code: string, rating: Rating | MediaRating | null): RatingForm {
  const blank = { score: NaN, justification: "" };
  const stored = rating && ("scores" in rating ? rating.scores : rating as unknown as Record<string, Dimension>);
  return {
    anon_id: code,
    scores: Object.fromEntries(rubricOf(kind).map((c) => [c.key, stored?.[c.key] ?? { ...blank }])),
    hallucination_findings: rating?.hallucination_findings ?? [],
    overall_notes: rating?.overall_notes ?? "",
  };
}
// A module rating is sent with its criteria at the top, as the module rubric always was.
export function fromRatingForm(kind: PacketKind, values: RatingForm): Rating | MediaRating {
  return kind === "module" ? { ...values.scores, anon_id: values.anon_id, hallucination_findings: values.hallucination_findings,
    overall_notes: values.overall_notes } as Rating : values;
}
export const raterEmailsSchema = z.object({
  emails: z.array(z.email("Email không hợp lệ")).max(100),
});
