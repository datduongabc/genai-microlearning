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

Trung bình, kèm khoảng tin cậy bootstrap 95% (2.000 lần lấy mẫu lại, theo câu hỏi).

**Không tính các câu từ chối vào faithfulness.** Ragas coi câu "tài liệu không đề cập" là một nhận
định, và chấm nó là không có căn cứ. Nếu tính vào, một câu từ chối đúng sẽ kéo faithfulness xuống.
Vì vậy câu nào bị judge đánh dấu là noncommittal (answer relevancy đúng bằng 0) thì được đếm riêng
ở cột "từ chối", và không tính vào faithfulness.

| Tập | n | Từ chối | Faithfulness | Answer relevancy |
| --- | ---: | ---: | --- | --- |
| Tất cả | 50 | 4 | 0,967 (0,916–1,000) | 0,725 (0,648–0,788) |
| advanced-programming | 10 | 1 | 1,000 | 0,744 (0,558–0,871) |
| calculus 1 | 10 | 0 | 0,900 (0,700–1,000) | 0,743 (0,697–0,792) |
| machine-learning | 10 | 0 | 1,000 | 0,847 (0,781–0,908) |
| operating-system | 10 | 2 | 0,958 (0,875–1,000) | 0,685 (0,439–0,888) |
| scientific-socialism | 10 | 1 | 0,978 (0,933–1,000) | 0,604 (0,432–0,725) |

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

## Sau khi sửa prompt chat (lượt 2)

Prompt cũ cấm cả việc "suy diễn". Vì vậy chat từ chối cả những câu chỉ cần đọc đoạn code có trong
nguồn. Prompt mới:
- Vẫn cấm đưa vào thông tin ngoài nguồn.
- Cho phép suy luận và tính toán trực tiếp trên nội dung của nguồn, có trình bày các bước.
- Yêu cầu chép công thức giữ nguyên từng ký hiệu.

Trước khi chạy lại cả 50 câu, mình thử 3 câu ngoài phạm vi môn: nấu phở bò (môn Hệ điều hành),
React `useEffect` (môn Lập trình nâng cao), định lý Bayes (môn Giải tích 1). Cả 3 vẫn bị từ chối.

Sau đó sinh lại cả 50 câu với prompt mới và chấm lại bằng cùng judge. Lượt 2 vẫn do
`gemini-3.5-flash-lite` sinh và `gpt-oss-120b` chấm, ở cả 50 câu. Dữ liệu thô:
`chat-eval/answers-gemini-fast-v2.jsonl` và `chat-eval/ragas-groq-pro-v2.jsonl`.

| | Lượt 1 | Lượt 2 |
| --- | --- | --- |
| Từ chối | 4 | 2 (Round Robin, liên minh giai cấp) |
| Faithfulness | 0,967 (0,916–1,000) | 0,966 (0,920–0,995) |
| Answer relevancy | 0,725 (0,648–0,788) | 0,755 (0,691–0,805) |

- **Answer relevancy theo cặp câu:** tăng +0,030, khoảng tin cậy 95% từ −0,007 đến +0,076. Chưa có
  ý nghĩa thống kê.
- **Hai câu từ chối sai trước đây giờ được trả lời:**
  - `obj.display()`: trả lời "Subclass".
  - Race condition: chỉ ra đúng biến `available_resources`.
- **Câu tích phân:** giữ đúng `√` và đạt faithfulness 1,0.
- **Faithfulness không giảm.** Như vậy cho phép suy luận không làm chat thêm nhận định không có căn cứ.
- **Một câu mới bị điểm thấp:** câu machine-learning về điều kiện nửa mặt phẳng, faithfulness 0,14.
  Nguồn của câu này là bản ghi lời giảng ("theta triangle transpose X"), và câu trả lời diễn đạt lại
  lời giảng đó mà không bám sát được. Một lượt chạy chưa đủ để tách phần do model sinh với phần do
  judge.

## Giới hạn

- **Bộ câu hỏi dễ hơn thực tế.** Mỗi câu được một model đặt từ đúng một chunk, và chunk đó luôn được
  truy xuất. Vì vậy bộ này đo việc trả lời từ ngữ cảnh đúng, không đo câu hỏi cần nhiều đoạn, câu hỏi
  ngoài phạm vi môn học, hay hội thoại nhiều lượt. Faithfulness 0,967 là cận trên trong điều kiện
  này, không phải con số chung cho mọi loại câu hỏi.
- **Một judge, một lượt chấm.** Chưa so judge với người chấm (kappa) và chưa lặp lại lượt chấm.
- **Answer relevancy có thể bị kéo xuống ở câu hỏi tiếng Việt.** Prompt của Ragas viết bằng tiếng
  Anh. Môn scientific-socialism, toàn câu hỏi tiếng Việt, có điểm thấp nhất (0,604). Đây mới là giả
  thuyết, chưa được kiểm.
- **Đo với Jina, chưa đo với Jev.** Muốn đo với Jev thì chạy lại `chat_eval.py answer` với
  `RERANKER_PROVIDER=jev`; bộ câu hỏi và script chấm giữ nguyên.

## Việc nên làm tiếp

- **Từ chối quá mức và ký hiệu toán bị hỏng:** đã sửa bằng prompt ở lượt 2 (xem trên).
- **Bổ sung bộ câu hỏi.** Thêm câu cần nhiều đoạn, câu ngoài phạm vi môn học (để kiểm việc từ chối
  đúng) và câu nối tiếp trong hội thoại, có người gán nhãn.
