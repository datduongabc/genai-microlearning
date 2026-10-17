import { z } from "zod";

export const rubric = [
  {
    key: "accuracy",
    question: "Thông tin có khớp tài liệu nguồn không?",
    label: "Độ chính xác",
    guide:
      "Chỉ đối chiếu nội dung, quiz và lời giải với tài liệu nguồn. 5: khớp mọi thông tin; 3: sai chi tiết; 1: sai ý chính hoặc đáp án.",
  },
  {
    key: "completeness",
    question: "Đã đủ ý cần thiết để đạt mục tiêu chưa?",
    label: "Độ đầy đủ",
    guide:
      "Đối chiếu toàn bộ nguồn; liệt kê ý lớn bị bỏ sót. Chỉ trừ điểm nếu ý đó cần để đạt mục tiêu. 5: đủ; 3: thiếu một ý; 1: thiếu phần lớn.",
  },
  {
    key: "consistency",
    question: "Quiz, lời giải và ví dụ có khớp nhau không?",
    label: "Tính nhất quán",
    guide:
      "Quiz, lời giải và ví dụ có khớp nội dung và đáp án không? 5: tất cả khớp; 3: một chỗ lệch; 1: nhiều mâu thuẫn.",
  },
  {
    key: "hallucination_score",
    question: "Có khẳng định nào không có căn cứ trong nguồn?",
    label: "Hallucination",
    guide:
      "Kiểm từng câu với nguồn; ghi từng claim không có căn cứ. 5: không có (danh sách rỗng); 4: một chi tiết phụ; 3: 2–3 claim hoặc một ý chính; 2: nhiều claim; 1: phần lớn không có căn cứ.",
  },
  {
    key: "slide_quality",
    question: "Slide có đúng, đủ và ngắn gọn không?",
    label: "Chất lượng slide",
    guide:
      "5: tóm tắt đúng, đủ, ngắn gọn; 3: thiếu ý hoặc quá dài; 1: sai, rời rạc hoặc chép cả đoạn.",
  },
  {
    key: "quiz_quality",
    question: "Quiz có hợp lý và đo đúng mục tiêu không?",
    label: "Chất lượng quiz",
    guide:
      "Kiểm độ khó theo Bloom, phương án nhiễu, dấu hiệu lộ đáp án và ý bị lặp. 5: đều hợp lý; 3: vài lỗi; 1: đoán được mà không học.",
  },
  {
    key: "bloom_fidelity",
    question: "Nội dung có khớp mức Bloom không?",
    label: "Độ khớp mức Bloom",
    guide:
      "Nhớ: nhận biết; Hiểu: diễn giải; Áp dụng: tình huống mới; Phân tích: quan hệ; Đánh giá: phán đoán có tiêu chí; Sáng tạo: giải pháp mới. 5: khớp; 3: lệch một mức ở một phần; 1: lệch rõ rệt.",
  },
  {
    key: "query_alignment",
    question: "Nội dung có bám chủ đề được giao không?",
    label: "Bám chủ đề gốc",
    guide:
      "Đối chiếu chủ đề được giao, không chỉ mục tiêu module tự đặt. 5: đúng chủ đề; 3: phần đáng kể thuộc chủ đề lân cận; 1: lạc chủ đề.",
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
      claim: z.string().trim().min(1, "Vui lòng nhập claim"),
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
export const raterTextSchema = z
  .object({ emailsText: z.string() })
  .transform(({ emailsText }) => ({
    emails: emailsText
      .split(/\n/)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  }))
  .pipe(raterEmailsSchema);
