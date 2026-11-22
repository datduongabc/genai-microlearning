# Sinh nội dung đồng thời trên AWS, 2026-11-22

Đo trên AWS với model thật: N giảng viên cùng lúc, mỗi người yêu cầu một module, một bộ flashcard và một video (kịch bản, vẽ hình, dựng).
Mục đích là đo hiệu năng của hệ thống (hàng đợi, autoscale, tài nguyên), không chấm chất lượng nội dung.
Dữ liệu thô: [2026-11-22-generation-concurrency/](2026-11-22-generation-concurrency/) (`media.json` từng job, `profile.json` từ CloudWatch).

## Cách đo

- Stack demo (image `0a81138`), GPT-6 Luna (`openai-fast`), Jev, voice và image model mặc định, qua địa chỉ CloudFront từ một laptop ở Việt Nam.
- `devops/loadtest/drive_generation.py seed-http --users 15` với `SEED_PAGES=20` của `Chapter 5_CPU Scheduling.pdf`, rồi `media-http --users 5` và `media-http --users 10 --skip 5`.
- Mỗi giảng viên gửi ba yêu cầu cùng lúc; khi kịch bản xong thì yêu cầu vẽ hình, rồi dựng video.
- Thời gian chờ là từ lúc gửi đến lần hỏi trạng thái đầu tiên thấy job đang chạy (hỏi mỗi 2 s, nên trễ tối đa 2 s); hàng đợi không lưu thời điểm bắt đầu.
- Lúc bắt đầu: 2 task worker (4 job mỗi task), renderer 0 task.
- Tài nguyên: `devops/loadtest/cloudwatch_profile.py 2026-11-22T03:40:00Z 2026-11-22T03:55:00Z`.

## Kết quả

Giây, p50 / p95. Mọi job đều xong (5 người: 25 job; 10 người: 50 job), không lần thử lại nào thất bại.

| Job | Chờ, 5 người | Tổng, 5 người | Chờ, 10 người | Tổng, 10 người |
| --- | ---: | ---: | ---: | ---: |
| Module | 2,6 / 2,7 | 262,9 / 295,4 | 2,5 / 2,6 | 189,2 / 307,9 |
| Flashcard | 27,2 / 39,6 | 39,6 / 53,9 | 146,3 / 179,1 | 166,7 / 207,6 |
| Kịch bản video | 54,0 / 121,6 | 140,1 / 207,4 | 160,7 / 179,3 | 252,5 / 283,8 |
| Vẽ hình | 22,6 / 30,8 | 32,8 / 43,1 | 2,1 / 79,9 | 16,4 / 90,1 |
| Dựng video | 159,7 / 178,2 | 208,8 / 217,0 | 2,3 / 24,7 | 61,5 / 80,0 |

- Thời gian API trả lời yêu cầu: tối đa 0,62 s.
- Video dài 173 đến 230 s, 6 đến 8 cảnh, 2 đến 5 hình.
- Lượt 5 người mất 424,8 s, lượt 10 người 378,3 s.

## Autoscale và tài nguyên

Sự kiện ECS:

- worker: 2 → 3 task lúc 03:43:10, 3 → 5 lúc 03:50:11, khoảng 2 phút sau khi hàng đợi dâng.
- renderer: 0 → 4 task lúc 03:45:17 và 03:46:19.

Trong cửa sổ 03:40-03:55:

| Thành phần | CPU TB / max | Ghi chú |
| --- | --- | --- |
| api | 7,3% / 21,8% | RAM 20% |
| worker | 3,1% / 11,3% (task cao nhất 74,1%) | RAM tối đa 19% |
| renderer | 22,5% / 59,9% (task cao nhất 96,3%) | RAM tối đa 14% |
| embedder | 9,4% / 98,9% | đỉnh do nạp 15 tài liệu lúc đầu |
| gateway | 6,8% / 9,4% | RAM 45% |
| RDS | tối đa 7,1% | tối đa 44 kết nối, RAM trống tối thiểu khoảng 2,8 GB |

## Đọc kết quả

- Thời gian chờ là độ trễ của autoscale, không phải quá tải: không job nào lỗi, CPU và database còn rất dư.
- Lượt 10 người: 30 job đến cùng lúc với 8 chỗ, nên flashcard và kịch bản chờ khoảng 2,5 phút cho đến khi có thêm worker.
- Lượt 5 người: renderer khởi động từ 0 task, nên dựng video chờ khoảng 160 s.
  Ở lượt 10 người renderer đã có sẵn từ lượt trước, nên chờ 2 s.
- Muốn giảm thời gian chờ: giữ sẵn tối thiểu 1 renderer và nhiều worker hơn trong giờ cao điểm, hoặc scale sớm hơn.
  Mỗi cách đổi chi phí lấy độ trễ.

## Giới hạn

- Mỗi mức đo một lần, cùng một tài liệu cho mọi người.
- Chi phí model của lượt đo chưa tổng hợp.
- Thời điểm bắt đầu chạy có sai số tới 2 s.

## Đọc có thời gian nghỉ (k6)

Kịch bản `devops/loadtest/mixed_read.js`: mỗi người dùng ảo là một giảng viên chuyển trang, nghỉ 10 đến 20 s mỗi trang, trung bình 2,2 request một trang.
Tải tăng theo bậc 300, 600, 1.200 người dùng ảo; mỗi bậc giữ 2 đến 3 phút, chỉ phần giữ được tính (`devops/loadtest/analyze_mixed.py`).
Lúc bắt đầu có 1 task api.
Chạy 04:20:47 đến 04:33:39 UTC từ một laptop ở Việt Nam qua CloudFront.
Tóm tắt k6: `2026-11-22-generation-concurrency/read-k6-summary.txt`; tài nguyên: `read-profile.json`.
CSV từng request (152 MB) không lưu vào repo.

| Người dùng ảo | req/s | p50 | p95 | p99 | Lỗi |
| --- | ---: | ---: | ---: | ---: | ---: |
| 300 | 43,5 | 48 ms | 90 ms | 144 ms | 0% |
| 600 | 85,6 | 72 ms | 304 ms | 449 ms | 0% |
| 1.200 | 126,7 | 1.985 ms | 6.481 ms | 6.901 ms | 0% |

- Toàn lượt: 58.198 request, 8 lỗi (0,01%), mọi check đạt.
- Ở 1.200 người, một task api bão hoà ở khoảng 127 req/s (CPU 100%), khớp với 124 req/s đo trước đây; tải mong muốn khoảng 175 req/s.
- Autoscale bật task api thứ hai lúc 04:30:32, nhận traffic lúc 04:31:28, khoảng 2 phút trước khi bậc cuối kết thúc; task thứ ba bật lúc 04:34:39, sau khi đo xong.
- Database: CPU tối đa 25,4%, tối đa 44 kết nối, nên không phải điểm nghẽn.

### Hệ quả cho cấu hình

- Một task api giữ p95 dưới 1 s đến ít nhất 86 req/s (600 người online với nhịp này); điểm gãy nằm giữa 86 và 127 req/s.
- Ngưỡng scale hiện tại là 6.000 request/phút mỗi task (100 req/s), sát điểm gãy, nên p95 vượt 1 s trước khi có task mới.
  Hạ xuống khoảng 4.000 request/phút (67 req/s) sẽ scale trước điểm gãy.
- Với 30.000 người dùng và 10 đến 20% online (3.000 đến 6.000 người, giả định nhịp 15 s mỗi trang như trên), tải khoảng 440 đến 880 req/s, tức 6 đến 11 task api ở 80 req/s mỗi task; cấu hình production cho tối đa 12.
  Đây là ngoại suy từ một task, chưa đo nhiều task cùng lúc ở mức đó.
- Autoscale mất khoảng 3 đến 4 phút từ lúc tải tăng đến lúc task mới phục vụ; tải tăng đột ngột (đầu giờ học) cần đặt sẵn số task tối thiểu theo lịch.
