# Docling so với tầng đọc hiện tại, và chi phí xử lý toàn bộ docs/documents

Ngày đo: 2026-09-24.
Mọi số dưới đây đo trên máy phát triển (4 lõi CPU, không GPU), trong container tách riêng từ ảnh `do-an-backend`, ảnh sản phẩm không bị sửa.

## 1. Docling

Tệp đo: calculus1/Chương 4a, chemistry1/Chương 6, dsa/heap.pdf, operating-system/Chapter 1, advanced-programming/Inheritance.pptx.
Thước đo: tỉ lệ dòng vụn (dòng có tối đa 3 ký tự), số ký tự vùng mã riêng (PUA, ký hiệu phông Symbol không đọc ra), số công thức Docling đánh dấu không giải mã được, số giây mỗi trang.

| Tệp | Cách đọc | Dòng | Vụn | PUA | Công thức bỏ trắng | Giây/trang |
|---|---|---|---|---|---|---|
| calculus1 4a | hiện tại | 3661 | 91% | 1224 | - | 0,01 |
| calculus1 4a | Docling | 245 | 10% | 50 | 112 | 1,3 |
| calculus1 4a | Docling + công thức | 247 | 10% | 50 | 0 | 146,7 |
| chemistry1 6 | hiện tại | 1166 | 78% | 368 | - | 0,04 |
| chemistry1 6 | Docling | 187 | 4% | 49 | 50 | 1,0 |
| chemistry1 6 | Docling + công thức | 187 | 4% | 49 | 0 | 226,9 |
| dsa heap | hiện tại | 1388 | 2% | 0 | - | 0,01 |
| dsa heap | Docling | 498 | 4% | 0 | 0 | 1,1 |
| OS chapter 1 | hiện tại | 1165 | 27% | 0 | - | 0,01 |
| OS chapter 1 | Docling | 563 | 1% | 0 | 0 | 0,6 |
| Inheritance.pptx | hiện tại | 227 | 24% | 0 | - | 0,00 |
| Inheritance.pptx | Docling | 224 | 19% | 0 | 0 | 0,02 |

Chi phí cài đặt: venv nặng thêm 373 MB, model tải về lần đầu 1115 MB.
`pip install docling` kéo về torchvision không khớp torch bản CPU của ảnh, phải ghim `torchvision==0.29.0` từ kho CPU của PyTorch.

Đối chiếu với ảnh trang gốc (calculus1 4a trang 4 và 9):

- Docling thường bỏ trắng mọi công thức, nên mất nhiều hơn tầng đọc hiện tại vốn còn giữ mảnh vụn.
- Docling + công thức giải mã được cả 112 công thức nhưng mất dấu "=" ở mọi chỗ (thay bằng `\quad`).
- Nó đọc sai một số công thức: ∫1/x dx thành "1 - dx", ∫1/(a²-x²) dx mất hệ số 1/(2a) và thêm dấu trừ.
- Một công thức (bước tính I₃ trang 9) thoái hoá thành mảng hàng nghìn chữ "c"; hóa chương 6 cũng có một mảng như vậy.
- Chữ tiếng Việt trong khung công thức bị rã từng ký tự.
- Chỗ "ln(2^x - 1)" trông như lỗi nhưng đúng là slide gốc in như vậy; Docling chép đúng.
- Hóa chương 6: phương trình phản ứng đọc khá tốt, nhưng mất điện tích ion (SO₄²⁻ thành SO₄, Cl⁻ thành Cl) và mất "=" trong biểu thức hằng số cân bằng.

Token của cùng tệp calculus1 4a (tokenizer Gemini): hiện tại 9.586, Docling 2.830, Docling + công thức 20.270.
LaTeX của Docling dài dòng và mảng thoái hoá chiếm 10% tệp, nên chế độ công thức tốn gấp đôi token chứ không giảm.

Trên tài liệu tiếng Anh, Docling thường gom dòng và dựng bảng tốt hơn, nhưng làm mất nội dung:

- Không đọc speaker notes của PPTX: mọi dòng mô tả getter/setter trong notes của Inheritance.pptx biến mất.
- Dính chữ ở một số PDF: "Illustratehowsearchingalgorithmsworkondatastructures" (dsa heap).
- Bảng trộn ô bị nhân bản nội dung ra mọi cột (bảng syllabus của OS chapter 1).

Kết luận: không dùng Docling.
Chế độ công thức quá chậm cho sản phẩm không có GPU (bộ slide 36 trang mất 88 phút và chiếm hết CPU của worker), vẫn sai công thức và tốn token hơn.
Chế độ thường bỏ trắng công thức và mất speaker notes, tức là mất đúng phần giảng viên hay viết.

## 2. Khối lượng docs/documents

11 môn, 100 tài liệu (89 PDF, 11 PPTX) và 6 bài giảng video (mỗi bài có cả mp3 lẫn mp4, 38 phút không trùng).
4.195 trang, 67 trang phải OCR, 1,79 triệu ký tự, 2.550 chunk theo chunker hiện tại.
714 nghìn token theo countTokens của Gemini.

Ký tự mỗi token: tiếng Anh 3,4-3,9; hóa 2,3; toán tiếng Việt (calculus1, linear-algebra, probability-statistics) 1,5-1,8.
Chunker đang ước token bằng ký tự chia 4, nên với toán tiếng Việt nó đánh giá thấp khoảng 2,5 lần.

## 3. Token của một lượt sinh module (đo thật, gemini-fast)

Mỗi module: 20 lệnh gọi LLM, 4 lượt nhúng câu truy vấn, 100-210 giây.

| Tài liệu | Token vào | Token ra |
|---|---|---|
| Encapsulation.pptx | 65.137 | 7.186 |
| Chapter 3 Processes.pdf | 90.396 | 7.255 |
| Chương 4a Tích phân bất định.pdf | 285.593 | 5.874 |

Với tài liệu giải tích, bước viết nội dung nhận 66 nghìn token (tiếng Anh 20 nghìn) và bước kiểm tra độ liên quan chạy lại 3 lần.
Công thức vỡ vụn vừa làm truy hồi kém vừa làm lượt sinh đắt gấp 3-4 lần.

## 4. Chi phí xử lý một lượt

Giá paid tier ngày 2026-09-24.
Giả định 67 tài liệu có hồ sơ token như tiếng Anh, 24 như toán tiếng Việt, 9 (hóa) ở giữa: trung bình 137 nghìn token vào và 6,8 nghìn token ra mỗi module.
Số của Claude nhân token Gemini với 1,3 và chưa đo được, vì count_tokens bị từ chối khi tài khoản Anthropic chưa có số dư.

| Model | 100 module | 400 module |
|---|---|---|
| Gemini Flash-Lite ($0,30/$2,50) | 5,8 USD | 23 USD |
| Gemini 3.8 Flash ($0,75/$3,75) | 13 USD | 51 USD |
| Gemini 3.1 Pro ($2/$12) | 36 USD | 142 USD |
| Claude Haiku 4.5 ($1/$5) | 22 USD | 89 USD |
| Claude Sonnet 5 ($2/$10) | 45 USD | 178 USD |
| Claude Opus 5.5 ($4/$20) | 89 USD | 356 USD |
| Claude Fable 5.1 ($10/$50) | 223 USD | 890 USD |

Nhúng toàn bộ kho khoảng 0,15 USD với Gemini, phiên âm 38 phút video khoảng 0,05 USD.
Claude và DeepSeek không có API nhúng.
Voyage AI (Anthropic giới thiệu) cho 200 triệu token miễn phí nhưng ra vector 1024 chiều, phải migration cột `Vector(768)`.

Tài khoản Google Cloud của dự án đòi nạp trước tối thiểu 800.000 đ để bật trả phí, không phải 5 USD như tài liệu Gemini API ghi.
