# Chất lượng câu trả lời chat theo Ragas, 2026-11-07

Lần đầu đo faithfulness và answer relevance của chat theo khóa học, hai chỉ số thầy hướng dẫn yêu cầu.

## Điều kiện đo

- **Mã:** `main` tại `d82afc7` (sau #85). Có `backend/scripts/chat_eval.py` (sinh câu trả lời) và
  `backend/scripts/chat_eval_ragas.py` (chấm điểm).
- **Dữ liệu:** năm môn đã nạp cho lần đo ngày 2026-11-04. DB đo được dump ra ở chế độ chỉ đọc,
  rồi nạp vào một DB tạm và migrate lên 0029. Gồm 1.880 chunk, nhúng bằng Qwen3-Embedding-0.6B.
- **Câu hỏi:** cứ 5 câu lấy 1 trong bộ 250 câu retrieval (`truy_van_5mon.json`), mỗi môn 10 câu, tổng
  50 câu. Bộ câu hỏi đặt trong `chat-eval/questions.json`, mỗi câu kèm nội dung chunk mà nó được đặt từ đó.
- **Sinh câu trả lời:** chạy đúng đường của `POST /courses/{id}/chat` cho một lượt hỏi, không có lịch
  sử: `search_for_chat` lấy 30 ứng viên lai, Jina v2 rerank lấy 8 chunk, rồi `answer_question`.
  - Model là `gemini-fast`, chạy ở chế độ thí nghiệm (đã tắt fallback).
  - Cả 50 câu đều do `gemini-3.5-flash-lite` phục vụ, gói miễn phí.
  - Jina trả lời ở cả 50 câu. Cấu hình này là Jina, không phải Jev: Jev còn chờ trường đồng ý và
    hiện TypeSafe đã hết credit.
- **Chấm điểm:** dùng Ragas 0.2.15.
  - `Faithfulness`: chia câu trả lời thành các nhận định rồi kiểm từng nhận định với 8 chunk ngữ cảnh.
  - `ResponseRelevancy`: judge viết ba câu hỏi hợp với câu trả lời, rồi lấy độ tương đồng embedding
    giữa chúng và câu hỏi gốc. Embedding là `embed-default`, cũng là embedder của ứng dụng.
  - Judge là `groq-pro`. Cả 50 lượt chấm đều do `openai/gpt-oss-120b` phục vụ, tức khác hãng với
    model sinh. Fallback của gateway đã tắt, và script dừng ngay nếu có lời gọi nào do model cùng
    hãng với model sinh phục vụ.
  - Đây là lượt chấm duy nhất trả tiền (Groq).
- **Dữ liệu thô:** `chat-eval/answers-gemini-fast.jsonl` và `chat-eval/ragas-groq-pro.jsonl`.

## Kết quả

Trung bình, kèm khoảng tin cậy bootstrap 95% (2.000 lần lấy mẫu lại, theo câu hỏi):

| Tập | n | Faithfulness | Answer relevancy |
| --- | ---: | --- | --- |
| Tất cả | 50 | 0,959 (0,904–0,996), 49 câu có nhận định | 0,725 (0,648–0,788) |
| advanced-programming | 10 | 1,000 | 0,744 (0,558–0,871) |
| calculus 1 | 10 | 0,900 (0,700–1,000) | 0,743 (0,697–0,792) |
| machine-learning | 10 | 1,000 | 0,847 (0,781–0,908) |
| operating-system | 10 | 0,907 (0,778–1,000), 9 câu có nhận định | 0,685 (0,439–0,888) |
| scientific-socialism | 10 | 0,980 (0,940–1,000) | 0,604 (0,432–0,725) |

Phía truy xuất:
- Chunk mà câu hỏi được đặt từ đó nằm trong 8 chunk ngữ cảnh ở cả 50 câu.
- Chat trích dẫn đúng chunk đó ở 48 câu.
- Thời gian một câu trả lời: trung vị 4,0 s, p90 6,6 s, lâu nhất 13,8 s, gồm cả rerank trên CPU.

## Đọc từng câu

**Bốn câu có answer relevancy bằng 0.** Ragas cho 0 khi câu trả lời là kiểu từ chối hay nói chung
chung. Trong cả bốn câu, chat trả lời "tài liệu không đề cập", dù chunk nguồn có trong ngữ cảnh:

| Câu hỏi | Nguồn có gì | Đánh giá |
| --- | --- | --- |
| `obj.display()` in ra gì (advanced-programming) | Có đủ code, chỉ dòng kết quả là `// Output: ???` | **Từ chối sai.** Đáp án `Subclass` suy ra được từ chính đoạn code |
| Biến nào gây race condition trong `decrease_count` và `increase_count` (operating-system) | Có đủ code của bài tập | **Từ chối sai.** Câu hỏi cần phân tích đoạn code có sẵn |
| Thời gian chờ trung bình của Round Robin, quantum 5 (operating-system) | Chỉ có đề bài, không có lời giải | Câu hỏi đòi tự tính. Từ chối là chấp nhận được, nhưng một trợ giảng thì sẽ tính |
| Tuyên ngôn nêu vấn đề liên minh giai cấp thế nào (scientific-socialism) | Chỉ có tiêu đề mục | **Câu hỏi không trả lời được từ nguồn.** Đây là lỗi của bộ câu hỏi |

**Một câu có faithfulness bằng 0** (calculus 1, tích phân lượng giác). Câu trả lời chép kết quả từ
nguồn, nhưng ký hiệu căn `√` bị đổi thành `∕(∕`. Nguồn ghi `3/(√(cos x)^3)+3/5 cos x√(cos^2 x)^3`,
còn câu trả lời ghi `3/(∕(∕(cos x)^3))+3/5 cos x∕(∕(cos^2 x)^3)`. Judge đánh giá nhận định này
không có căn cứ, và đánh giá như vậy là đúng.

## Giới hạn

- **Bộ câu hỏi dễ hơn thực tế.** Mỗi câu được một model đặt từ đúng một chunk, và chunk đó luôn được
  truy xuất. Vì vậy bộ này đo việc trả lời từ ngữ cảnh đúng, không đo câu hỏi cần nhiều đoạn, câu hỏi
  ngoài phạm vi môn học, hay hội thoại nhiều lượt. Faithfulness 0,959 là cận trên trong điều kiện
  này, không phải con số chung cho mọi loại câu hỏi.
- **Một judge, một lượt chấm.** Chưa so judge với người chấm (kappa) và chưa lặp lại lượt chấm.
- **Answer relevancy có thể bị kéo xuống ở câu hỏi tiếng Việt.** Prompt của Ragas viết bằng tiếng
  Anh. Môn scientific-socialism, toàn câu hỏi tiếng Việt, có điểm thấp nhất (0,604). Đây mới là giả
  thuyết, chưa được kiểm.
- **Đo với Jina, chưa đo với Jev.** Muốn đo với Jev thì chạy lại `chat_eval.py answer` với
  `RERANKER_PROVIDER=jev`; bộ câu hỏi và script chấm giữ nguyên.

## Việc nên làm tiếp

- **Từ chối quá mức khi câu hỏi cần suy luận trên code hay bài tập có sẵn trong nguồn.** Đây là lỗi
  của prompt chat, sửa được mà không tốn tiền. Sau khi sửa, chạy lại hai câu này.
- **Ký hiệu toán bị làm hỏng trong câu trả lời.** Cần tìm xem lỗi nằm ở model hay ở khâu xử lý đầu ra.
- **Bổ sung bộ câu hỏi.** Thêm câu cần nhiều đoạn, câu ngoài phạm vi môn học (để kiểm việc từ chối
  đúng) và câu nối tiếp trong hội thoại, có người gán nhãn.
