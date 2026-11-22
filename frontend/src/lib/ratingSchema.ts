import { z } from "zod";

// The module rubric of docs/rubric.md; a study made under an older one takes no new module ratings.
export const RUBRIC_VERSION = "v3";

export const rubric = [
  {
    key: "accuracy",
    question: "Thông tin có khớp tài liệu nguồn không?",
    label: "Độ chính xác",
  },
  {
    key: "completeness",
    question: "Đã đủ những ý cần để đạt mục tiêu chưa? Ý thuộc chủ đề khác thì không trừ điểm.",
    label: "Độ đầy đủ",
  },
  {
    key: "consistency",
    question: "Việc mục tiêu đòi hỏi (động từ của mục tiêu) có đúng là việc nội dung dạy, ví dụ minh hoạ và câu hỏi yêu cầu không?",
    label: "Tính nhất quán",
  },
  {
    key: "hallucination_score",
    question: "Kiểm từng ý về kiến thức: ý nào không có căn cứ trong nguồn? Bối cảnh giả định của ví dụ không tính.",
    label: "Thông tin bịa",
  },
  {
    key: "slide_quality",
    question: "Slide có bỏ chi tiết thừa, làm nổi ý chính, mỗi slide một ý, và tóm tắt đúng nội dung không?",
    label: "Chất lượng slide",
  },
  {
    key: "quiz_quality",
    question: "Đánh dấu lỗi viết câu hỏi của từng câu. Điểm tự tính: câu có tối đa một lỗi là đạt.",
    label: "Chất lượng quiz",
  },
  {
    key: "bloom_fidelity",
    question: "Nội dung có khớp mức Bloom không?",
    label: "Độ khớp mức Bloom",
  },
  {
    key: "query_alignment",
    question: "Nội dung có bám chủ đề được giao và tập trung vào một mục tiêu chính không?",
    label: "Bám chủ đề gốc",
  },
  {
    key: "examples_feedback",
    question: "Ví dụ có đúng và trình bày từng bước không? Lời giải mỗi câu có nói vì sao đáp án đúng và vì sao phương án khác sai không?",
    label: "Ví dụ và lời giải",
  },
] as const;
// The item-writing flaws of Tarrant et al. (2006), as backend/app/generation/evaluation.py lists them.
export const ITEM_WRITING_FLAWS: [string, string][] = [
  ["longest_option_correct", "Đáp án đúng dài, chi tiết hơn các phương án khác"],
  ["ambiguous_information", "Câu hỏi hoặc phương án mơ hồ, khó hiểu"],
  ["implausible_distractors", "Có phương án nhiễu vô lý"],
  ["true_or_false", "Các phương án là chuỗi mệnh đề đúng/sai"],
  ["absolute_terms", "Dùng từ tuyệt đối (luôn luôn, không bao giờ, tất cả)"],
  ["complex_k_type", "Phương án là tổ hợp các phương án khác (A và B, ...)"],
  ["negatively_worded", "Câu hỏi dùng dạng phủ định"],
  ["convergence_cues", "Đáp án đúng chung nhiều thành phần nhất với các phương án khác"],
  ["lost_sequence", "Phương án không xếp theo thứ tự số hoặc thời gian"],
  ["unfocused_stem", "Phải đọc phương án mới hiểu câu hỏi"],
  ["none_of_the_above", "Có phương án \"không có đáp án nào đúng\""],
  ["word_repeats", "Từ trong câu hỏi chỉ lặp lại ở đáp án đúng"],
  ["more_than_one_correct", "Có hơn một đáp án đúng"],
  ["logical_cues", "Câu hỏi có gợi ý giúp đoán ra đáp án"],
  ["all_of_the_above", "Có phương án \"tất cả đều đúng\""],
  ["fill_in_the_blank", "Câu hỏi dạng điền vào chỗ trống ở giữa"],
  ["vague_terms", "Phương án dùng từ mơ hồ (thường, đôi khi)"],
  ["grammatical_cues", "Phương án không khớp ngữ pháp với câu hỏi"],
  ["gratuitous_information", "Câu hỏi có thông tin thừa"],
];
const questionFlaws = z.object({ question: z.number().int(), flaws: z.array(z.string()) });
export type QuestionFlaws = z.infer<typeof questionFlaws>;
// docs/rubric.md: the share of questions with at most one flaw, all of them 5, none of them 1.
export function quizQualityScore(flaws: QuestionFlaws[]): number {
  const acceptable = flaws.filter((q) => new Set(q.flaws).size <= 1).length / flaws.length;
  return 1 + Math.floor(4 * acceptable + 0.5);
}
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
  examples_feedback: dimension,
  quiz_flaws: z.array(questionFlaws),
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
    examples_feedback: { ...blank },
    quiz_flaws: [],
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
// The form holds every kind the same way, its scores keyed by criterion; a module adds the flaws
// of each of its questions.
export type RatingForm = MediaRating & { quiz_flaws: QuestionFlaws[] };
export function ratingFormSchema(kind: PacketKind) {
  return z.object({
    anon_id: z.string(),
    scores: z.object(Object.fromEntries(rubricOf(kind).map((c) => [c.key, dimension]))),
    hallucination_findings: ratingSchema.shape.hallucination_findings,
    overall_notes: z.string(),
    quiz_flaws: z.array(questionFlaws),
  });
}
// `questions` is how many questions a module packet has, its quiz and its practice question.
export function toRatingForm(kind: PacketKind, code: string, rating: Rating | MediaRating | null,
  questions = 0): RatingForm {
  const blank = { score: NaN, justification: "" };
  const stored = rating && ("scores" in rating ? rating.scores : rating as unknown as Record<string, Dimension>);
  const saved = rating && "quiz_flaws" in rating ? rating.quiz_flaws : [];
  const quiz_flaws = kind === "module" ? Array.from({ length: questions }, (_, i) =>
    ({ question: i + 1, flaws: saved.find((q) => q.question === i + 1)?.flaws ?? [] })) : [];
  const scores: Record<string, Dimension> = Object.fromEntries(
    rubricOf(kind).map((c) => [c.key, stored?.[c.key] ?? { ...blank }]));
  // Computed from the start, so a packet just opened is not taken for one with unsaved changes.
  if (quiz_flaws.length) scores.quiz_quality = { ...scores.quiz_quality, score: quizQualityScore(quiz_flaws) };
  return {
    anon_id: code,
    scores,
    quiz_flaws,
    hallucination_findings: rating?.hallucination_findings ?? [],
    overall_notes: rating?.overall_notes ?? "",
  };
}
// A module rating is sent with its criteria at the top, as the module rubric always was.
export function fromRatingForm(kind: PacketKind, values: RatingForm): Rating | MediaRating {
  const { quiz_flaws, ...media } = values;
  return kind === "module" ? { ...values.scores, anon_id: values.anon_id, quiz_flaws,
    hallucination_findings: values.hallucination_findings, overall_notes: values.overall_notes } as Rating : media;
}
export const raterEmailsSchema = z.object({
  emails: z.array(z.email("Email không hợp lệ")).max(100),
});
