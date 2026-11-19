import { z } from "zod";

// Each criterion's levels 1 to 5, as in docs/rubric.md; 2 and 4 are the steps between its anchors.
export const rubric = [
  {
    key: "accuracy",
    question: "Thông tin có khớp tài liệu nguồn không?",
    label: "Độ chính xác",
    levels: [
      "Sai ở ý chính, hoặc đáp án đúng của quiz thực ra sai theo nguồn.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Vài chi tiết sai lệch, ý chính vẫn đúng.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Mọi thông tin khớp với nguồn.",
    ],
  },
  {
    key: "completeness",
    question: "Đã đủ ý cần thiết để đạt mục tiêu chưa?",
    label: "Độ đầy đủ",
    levels: [
      "Thiếu phần lớn các ý cần thiết.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Thiếu một ý cần thiết, hoặc nhiều ý chỉ nhắc lướt.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Đủ mọi ý cần thiết cho mục tiêu.",
    ],
  },
  {
    key: "consistency",
    question: "Câu hỏi, lời giải và ví dụ có khớp nhau không?",
    label: "Tính nhất quán",
    levels: [
      "Nhiều câu hỏi, lời giải hoặc ví dụ mâu thuẫn với nội dung.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Một câu hỏi về điều chưa dạy, hoặc một lời giải lệch đáp án.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Mọi câu hỏi, lời giải và ví dụ khớp với nội dung và với nhau.",
    ],
  },
  {
    key: "hallucination_score",
    question: "Có khẳng định nào không có căn cứ trong nguồn?",
    label: "Thông tin bịa",
    levels: [
      "Phần lớn nội dung không có căn cứ trong nguồn.",
      "Nhiều câu không có căn cứ, rải rác khắp module.",
      "Hai đến ba câu không có căn cứ, hoặc một câu ở ý chính.",
      "Một câu không có căn cứ, ở chi tiết phụ.",
      "Không có câu nào không có căn cứ.",
    ],
  },
  {
    key: "slide_quality",
    question: "Slide có đúng, đủ và ngắn gọn không?",
    label: "Chất lượng slide",
    levels: [
      "Sai nội dung, rời rạc, hoặc chép nguyên văn cả đoạn.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Đúng nhưng thiếu ý chính, hoặc gạch đầu dòng dài như đoạn văn.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Tóm tắt đúng, đủ ý chính, ngắn gọn.",
    ],
  },
  {
    key: "quiz_quality",
    question: "Quiz và bài luyện tập có hợp lý, đo đúng mục tiêu không?",
    label: "Chất lượng quiz",
    levels: [
      "Đoán được đáp án mà không cần học, hoặc câu hỏi không kiểm được gì.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Vài phương án nhiễu sai lộ liễu, hoặc hai câu kiểm cùng một ý.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Đúng độ khó, phương án nhiễu hợp lý, không lộ đáp án, mỗi câu một ý.",
    ],
  },
  {
    key: "bloom_fidelity",
    question: "Nội dung có khớp mức Bloom không?",
    label: "Độ khớp mức Bloom",
    levels: [
      "Lệch rõ rệt so với mức khai báo.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Lệch một mức ở một phần.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Khớp hoàn toàn mức khai báo.",
    ],
  },
  {
    key: "query_alignment",
    question: "Nội dung có bám chủ đề được giao không?",
    label: "Bám chủ đề gốc",
    levels: [
      "Nói về chủ đề khác.",
      "Giữa mức 1 và 3: lỗi nghiêm trọng ở nhiều chỗ.",
      "Phần đáng kể nói về chủ đề lân cận.",
      "Giữa mức 3 và 5: có lỗi nhỏ, không ảnh hưởng việc học.",
      "Toàn bộ xoay quanh đúng chủ đề gốc.",
    ],
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
