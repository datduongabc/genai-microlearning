| Bước | Đầu (USD) | Cuối (USD) | Chi phí (USD) | Ước tính (USD) |
| --- | ---: | ---: | ---: | ---: |
| 0. Smoke check gemini-flash, deepseek-flash | 5,910534 | 5,911154 | 0,000620 | 0,01 |
| A. fence_escape, openai-fast | 5,911154 | 5,951960 | 0,040806 | 0,01 |
| A. fence_escape, deepseek-flash | 5,951960 | 5,970226 | 0,018266 | 0,04 |
| B. 50 câu, gemini-flash | 5,970226 | 6,188374 | 0,218148 | |
| B. 50 câu, claude-fast | 6,188374 | 6,221624 | 0,033250 | |
| B. 50 câu, openai-fast | 6,221624 | 6,243025 | 0,021401 | |
| B. 50 câu, deepseek-flash | 6,243025 | 6,270075 | 0,027050 | |
| B. cộng | | | 0,299849 | 0,5 |
| C. gemini-flash chấm bởi claude-fast (lượt mất do máy khởi động lại) | 6,270075 | 6,379151 | 0,109076 | |
| C. gemini-flash chấm bởi claude-fast (chạy lại) | 6,379151 | 6,488999 | 0,109848 | |
| C. openai-fast chấm bởi claude-fast | 6,488999 | 6,577035 | 0,088036 | |
| C. deepseek-flash chấm bởi claude-fast | 6,577035 | 6,742495 | 0,165461 | |
| C. claude-fast chấm bởi openai-fast | 6,742495 | 6,801831 | 0,059337 | |
| C. 10 câu gemini-flash chấm bởi openai-fast | 6,801831 | 6,812305 | 0,010474 | |
| C. cộng | | | 0,542231 | 0,8 |
| **Tổng** | 5,910534 | 6,812305 | **0,901771** | |
