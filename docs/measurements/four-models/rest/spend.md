Chi phí đọc từ `GET /api/v1/credits` của OpenRouter (số dư = `total_credits - total_usage`).
Số đọc trước mỗi bước, và số đọc sau lấy khoảng 90 giây sau khi bước kết thúc.
Các bước chạy lần lượt, không song song.
Số đọc thô nằm trong `spend.log`.

| Bước | Số dư đầu (USD) | Số dư cuối (USD) | Chi phí (USD) | Ước tính (USD) |
| --- | ---: | ---: | ---: | ---: |
| 1. Injection, `deepseek-flash` (131 ca) | 2,5849 | 2,4629 | 0,1220 | 0,15 |
| 1. Injection, `claude-fast` (131 ca) | 2,4629 | 2,3248 | 0,1381 | 0,12 |
| 1. Injection, `openai-fast` (131 ca) | 2,3248 | 2,2606 | 0,0642 | 0,30 |
| 1. Injection, `gemini-flash` (131 ca) | 2,2606 | 1,4615 | 0,7991 | 0,60 |
| **1. Cộng** | | | **1,1234** | 1,10 |
| 2. Thẻ và lời thoại, `deepseek-flash` | 1,4615 | 1,4238 | 0,0377 | |
| 2. Thẻ và lời thoại, `claude-fast` | 1,4238 | 1,4029 | 0,0209 | |
| 2. Thẻ và lời thoại, `openai-fast` | 1,4029 | 1,3897 | 0,0132 | |
| 2. Thẻ và lời thoại, `gemini-flash` | 1,3897 | 1,3095 | 0,0802 | |
| 2. Vẽ 2 hình và dựng video của `deepseek-flash` | 1,3095 | 1,2250 | 0,0845 | 0,15-0,30 |
| **2. Cộng** | | | **0,2365** | 0,50 |
| 3. Đọc lại công thức, `deepseek-flash` (12 trang) | 1,2250 | 1,1880 | 0,0370 | |
| 3. Đọc lại công thức, `claude-fast` (12 trang) | 1,1880 | 1,1827 | 0,0053 | |
| 3. Đọc lại công thức, `openai-fast` (12 trang) | 1,1827 | 1,1737 | 0,0090 | |
| 3. Đọc lại công thức, `gemini-flash` (12 trang) | 1,1737 | 1,0528 | 0,1209 | |
| 3. Thử một ảnh với `deepseek-flash` và `claude-fast` | 1,0528 | 1,0527 | 0,0001 | |
| **3. Cộng** | | | **0,1723** | 0,15 |
| **Tổng** | 2,5849 | 1,0527 | **1,5322** | mức trần 2,2 |

Ước tính 0,60 của `gemini-flash` ở bước 1 giả định 15 ca fence_escape đã có sẵn.
Các file chạy tiếp đó đã mất, nên cả 131 ca được chạy lại; ước tính điều chỉnh khoảng 0,7.
Không bước nào vượt 2 lần ước tính.
