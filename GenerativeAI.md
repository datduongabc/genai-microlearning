# Đề tài: Xây dựng hệ thống AI tạo sinh tự động tạo các mô-đun Microlearning từ tài liệu môn học trên LMS

**Mã đề tài:** HK261-DAGD1-033
**Sinh viên thực hiện:** DƯƠNG NGUYỄN TẤN ĐẠT
**Ngành:** Khoa học Máy tính

---

## 1. Chi tiết đề tài

### Mô tả

Đề tài nghiên cứu và xây dựng một hệ thống sử dụng Generative AI để chuyển đổi các tài nguyên học tập có sẵn của giảng viên (PDF, PowerPoint, giáo trình, syllabus, tài liệu HTML, video/transcript) thành các **Microlearning Module** có cấu trúc sư phạm rõ ràng.

Một microlearning module có thể bao gồm:
`Learning Objective` → `Content` → `Slides` → `Example` → `Practice` → `Quiz` → `Explanation`.

Hệ thống được tích hợp với Moodle để giảng viên có thể tạo nhanh các bài học ngắn từ nội dung môn học.

### Yêu cầu

* **Nhiệm vụ 1: Khảo sát**
  * Generative AI trong giáo dục
  * AI lesson generation
  * AI slide generation
  * Microlearning
  * RAG trong giáo dục
* **Nhiệm vụ 2:** Xây dựng pipeline xử lý tài liệu.
* **Nhiệm vụ 3:** Xây dựng mô hình sinh.
* **Nhiệm vụ 4:** Xây dựng giao diện/plugin kết nối Moodle.
* **Nhiệm vụ 5: Đánh giá**
  * Độ chính xác, tính đầy đủ, tính nhất quán
  * Hallucination (ảo giác AI)
  * Chất lượng slides, chất lượng câu hỏi
  * Mức độ phù hợp với learning objectives.

---

## 2. Kế hoạch triển khai

### Giai đoạn 1 — Nghiên cứu và xây dựng Prototype (3 tháng - Đồ án tốt nghiệp)

**Mục tiêu:** Xây dựng AI Microlearning Generator phiên bản 1.
**Công việc:**

* Khảo sát các phương pháp GenAI.
* Xây dựng bộ dữ liệu môn học thử nghiệm.
* Xây dựng RAG/knowledge retrieval.
* Thiết kế prompt/template.
* Sinh micro-module, sinh slides, sinh quiz.
* Xây dựng prototype.

### Giai đoạn 2 — Tích hợp và đánh giá (3 tháng - Luận văn tốt nghiệp)

**Công việc:**

* Tích hợp với LMS.
* Cho phép giảng viên chỉnh sửa nội dung AI.
* Xây dựng rubric đánh giá.
* So sánh: `Human-created` vs `LLM-generated` vs `RAG + LLM`.
* Phân tích hallucination và tối ưu prompt/model.

---

## 3. Nhiệm vụ chuyên môn phân bổ

### 3.1. AI/LLM

*Đây là vị trí "trái tim" của hệ thống GenAI, chịu trách nhiệm cho chất lượng đầu ra của nội dung.*

* **Nhiệm vụ:**
  * Nghiên cứu và lựa chọn mô hình nền tảng (Foundation Models như GPT-4, Llama 3, hoặc Gemini) phù hợp với bài toán.
  * Xây dựng kiến trúc RAG (Retrieval-Augmented Generation) để giải quyết triệt để vấn đề "ảo giác" (hallucination) của AI, đảm bảo nội dung và quiz tạo ra bám sát 100% vào tài liệu gốc của giảng viên.
  * Thực hiện Prompt Engineering phức tạp (Chain-of-Thought, Few-shot prompting) để hướng dẫn AI sinh ra Micro-content và Quiz với định dạng chuẩn (ví dụ: JSON) có độ chính xác cao.
* **Tiêu chí đánh giá (Deliverables):** Module AI Core hoạt động ổn định, có API endpoint rõ ràng, tốc độ phản hồi (latency) tối ưu.

### 3.2. Backend & Data

*Xây dựng bộ khung (backbone) của LMS và xử lý luồng dữ liệu (Data Pipeline) cho module AI.*

* **Nhiệm vụ:**
  * Thiết kế và phát triển RESTful API/GraphQL phục vụ cho hệ thống LMS cốt lõi (quản lý khóa học, người dùng, tiến độ học tập).
  * Thiết kế hệ cơ sở dữ liệu kép: RDBMS (PostgreSQL/MySQL) cho dữ liệu nghiệp vụ và Vector Database (Milvus, Pinecone, hoặc pgvector) để lưu trữ embedding phục vụ cho kiến trúc RAG.
  * Xây dựng luồng tiền xử lý tài liệu học tập (Document Parsing): bóc tách text từ PDF, DOCX, xử lý chunking dữ liệu sao cho giữ được ngữ nghĩa.
* **Tiêu chí đánh giá (Deliverables):** Hệ thống API chịu tải tốt, sơ đồ thiết kế CSDL (ERD) đạt chuẩn dạng chuẩn 3NF, pipeline xử lý tài liệu không bị lỗi font hay mất cấu trúc.

### 3.3. Frontend & UI/UX

*Giải quyết bài toán hiển thị đa nền tảng và tối ưu trải nghiệm học tập.*

* **Nhiệm vụ:**
  * Thiết kế wireframe và phát triển giao diện Web/App (sử dụng React, Vue hoặc Angular).
  * Tối ưu hóa UI/UX đặc thù cho Micro-learning: thiết kế giao diện học tập dạng thẻ (flashcards), luồng bài học ngắn (bite-sized), thao tác lướt/chuyển bài mượt mà.
  * Xây dựng các component tương tác thực thi Quiz (trắc nghiệm một lựa chọn, nhiều lựa chọn, điền khuyết) và hiển thị phản hồi/giải thích (feedback) tức thì từ AI.
* **Tiêu chí đánh giá (Deliverables):** Giao diện Responsive hoạt động tốt trên cả Mobile và Desktop, đạt tiêu chuẩn về tốc độ tải trang và tính thân thiện (Usability).

### 3.4. Công nghệ Giáo dục & Đảm bảo Chất lượng (EdTech QA & Validation)

*Kiểm soát chất lượng nội dung AI và quy trình sư phạm.*

* **Nhiệm vụ:**
  * Nghiên cứu cơ sở lý luận về Micro-learning và Thang độ tư duy Bloom (Bloom's Taxonomy) để xây dựng bộ tiêu chí (rubric) đánh giá chất lượng Quiz.
  * Thực hiện kiểm thử có sự tham gia của con người (Human-in-the-loop) để đối chiếu, chấm điểm mức độ sư phạm của Micro-content và Quiz do AI tạo ra.
  * Viết kịch bản kiểm thử (Test Cases) cho các tính năng của LMS, đảm bảo luồng nghiệp vụ sư phạm (Enroll -> Learn -> Test -> Certificate) không xảy ra lỗi.
* **Tiêu chí đánh giá (Deliverables):** Bộ guideline/rubric đánh giá chất lượng nội dung, báo cáo độ chính xác của AI (đo lường tỷ lệ sinh nội dung lỗi), và các kịch bản Automation Test cơ bản.

### 3.5. Project Manager & DevOps

*Đảm bảo tiến độ luận văn/đồ án và khả năng vận hành thực tế của sản phẩm.*

* **Nhiệm vụ:**
  * Thiết lập pipeline CI/CD (GitHub Actions hoặc GitLab CI) để tự động hóa quá trình kiểm thử và triển khai.
  * Container hóa ứng dụng (Docker) và triển khai toàn bộ hệ thống (Frontend, Backend, Vector DB) lên môi trường Cloud (AWS, Azure, hoặc GCP).
  * Đảm bảo an toàn bảo mật, đặc biệt là bảo mật khóa API (API keys) của các dịch vụ LLM bên thứ ba để tối ưu chi phí.
* **Tiêu chí đánh giá (Deliverables):** Hệ thống có thể truy cập trực tuyến (live URL), mã nguồn được container hóa hoàn toàn, báo cáo tiến độ và tài liệu kiến trúc hệ thống (System Architecture Document) hoàn thiện.

---

## 4. Tài liệu tham khảo

* [Moodle and AI Solutions](https://moodle.com/solutions/moodle-and-ai/)
* [OpenLMS (Bản dịch tiếng Việt)](https://www-openlms-net.translate.goog/?_x_tr_sl=en&_x_tr_tl=vi&_x_tr_hl=vi&_x_tr_pto=tc)
* [Khan Academy - Teacher Tools](https://www.khanacademy.org/signup/extra-info/teacher?continue=https%3A%2F%2Fclassroom.khanacademy.org%2Fteacher%2Ftools)
